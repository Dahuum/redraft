"""
inplace_spike.py — backend for the in-place-editing test route (EXPERIMENTAL).

Powers /spike/analyze and /spike/edit (see api.py). This is the research path
toward a real guarantee: change ONLY the characters, leave the font, size,
position, weight, and everything else byte-for-byte identical.

WHY THIS EXISTS (read this before touching the matching logic)
----------------------------------------------------------------
The naive approach — "find this exact sequence of glyph codes sitting
together in one place in the content stream" — fails on real documents,
because real PDF generators very often DON'T draw one field's text as a
single contiguous instruction. A field's text is frequently split across
SEVERAL separate text-showing operators (Tj/TJ) — one per word, sometimes one
per letter — each with its own precise positioning. This is exactly how
custom letter-spacing / "styled" text gets produced by design tools.

So the engine here does NOT search "within one instruction". It:
  1. Parses the actual content-stream tokens (its own small tokenizer —
     literal strings, hex strings, arrays, names, numbers, operators).
  2. Builds an ordered list of every text-showing run (Tj / TJ / ' / "),
     each broken into its individual STRING TOKENS (so kerning numbers and
     other, unrelated string tokens in the same array are never touched).
  3. FLATTENS the character codes across ALL those runs, in document order,
     regardless of what operators sit between them — this is what lets it
     find text that's split across many small drawing instructions.
  4. Locates the target sequence in that flattened stream, then decides,
     precisely, how far the edit can safely reach:
       - fully inside ONE string token           -> splice just that token
       - exactly spans one or more WHOLE runs     -> rewrite the first run,
                                                      blank the rest
       - anything messier (a match that starts or ends partway through a
         run that ALSO carries unrelated text, or is split by a kerning
         number WITHIN one run)                   -> refuse, and say exactly
                                                      why, rather than risk a
                                                      subtly wrong splice.

TEXT INSIDE FORM XOBJECTS (very common — read this one)
----------------------------------------------------------------
Many real PDFs — anything exported by a headless-browser print-to-PDF
pipeline, and a lot of resume builders / design tools — draw the ENTIRE page
as a single Form XObject: the page's own content stream is just
"q ... /X11 Do ... Q", and ALL the actual text lives inside object X11's own,
separate content stream. Treating "the page's content stream" as the only
place to look (an earlier version of this file did exactly that) means such
a PDF finds ZERO editable text, not "some fields, some not" — every field
fails identically. So this engine searches the page's own stream AND every
Form XObject it uses (Image XObjects are skipped — their "stream" is raw
pixel data, not PDF operators, and tokenizing it would be garbage). Each
found run remembers which physical object it came from, so an edit is
written back to the RIGHT object.

KNOWN, NAMED LIMITS (as of this version) — read before assuming a failure is
a bug:
  - Only ONE level of Form XObject nesting is walked from the page (a Form
    XObject that itself invokes a further nested Form XObject is not
    followed). Not yet observed in practice; would show as "no_runs_for_font".
  - A match that would require editing across two DIFFERENT physical objects
    (e.g. half on the page, half inside an XObject — not a realistic
    document, but guarded against) is refused, not risked.
  - Text that has been converted to vector outlines (no text operator at
    all, just filled curves shaped like letters) cannot be found here at
    all — and cannot, in principle, be edited as "text" by ANY tool, because
    it no longer exists as text in the file.
  - Duplicate occurrences of the exact same text on a page are not
    disambiguated by position — the first match found is used.
  - Two different EMBEDDED COPIES of "the same" font (by name) could in
    theory assign different glyph-ids to the same character; this engine
    keys glyph maps by font name, not by the specific embedded font object.
"""
import base64
import io
import re

import fitz

import font_extend

try:                       # optional: fast pixel diff; pure-python fallback below
    import numpy as _np
except Exception:          # noqa: BLE001
    _np = None


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def _font_info(doc):
    """subtype{name->PDF subtype}, used{name->set(chars it currently renders)}."""
    subtype, used = {}, {}
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            subtype[f[3].split("+")[-1]] = f[2]
    for pno in range(doc.page_count):
        d = doc[pno].get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            for line in b["lines"]:
                for s in line["spans"]:
                    bag = used.setdefault(s["font"].split("+")[-1], set())
                    for ch in s["chars"]:
                        bag.add(_norm(ch["c"]))
    return subtype, used


def _spans(page):
    out = []
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            for s in line["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


# ── glyph-id maps: character -> glyph-id, built from what's actually rendered ─
# get_texttrace() exposes each rendered character's glyph-id. For a subset font
# this is the honest source of truth for "which characters (and which glyph-id)
# this specific embedding already has" — there is no reliable cmap to consult
# directly for a Type0/CID-subset font.
def _name_key(name: str) -> str:
    """A font name reduced to its identity: lowercase alphanumerics only.

    Two naming conventions meet in this engine and do not agree. Maps built
    from the PDF structure key on the BaseFont ("Tw Cen MT Bold"); maps built
    from extraction key on whatever the extractor normalises that to
    ("TwCenMT-Bold"). Looking one up with the other's key silently missed, so
    the glyph map for a font came back empty and every character in it looked
    missing. Reduced this way both become "twcenmtbold".
    """
    return "".join(c for c in name.lower() if c.isalnum())


def _lookup_by_name(mapping: dict, name: str):
    """mapping[name], falling back to a match on _name_key."""
    if name in mapping:
        return mapping[name]
    want = _name_key(name)
    for k, v in mapping.items():
        if _name_key(k) == want:
            return v
    return None


def _gid_maps(doc):
    gm = {}
    for pno in range(doc.page_count):
        for sp in doc[pno].get_texttrace():
            nm = (sp.get("font") or "").split("+")[-1]
            fm = gm.setdefault(nm, {})
            for c in sp.get("chars", []):
                ucs, gid = c[0], c[1]
                if isinstance(ucs, int) and ucs > 0:
                    fm[_norm(chr(ucs))] = gid
    return gm


_HEX_TOK = re.compile(rb"<([0-9A-Fa-f]+)>")
_BFRANGE_ENTRY = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", re.S)


def _hex_to_unicode_str(hx: bytes) -> str:
    """A ToUnicode destination hex string is big-endian UTF-16 code units —
    decode properly (not one-byte-per-char) so surrogate pairs and multi-
    char ligature expansions ("fi" -> U+0066 U+0069) come out right."""
    h = hx.decode("ascii", "ignore")
    if len(h) % 4:
        h += "0" * (4 - len(h) % 4)
    try:
        return bytes.fromhex(h).decode("utf-16-be")
    except (ValueError, UnicodeDecodeError):
        return ""


def _parse_tounicode_cmap(data: bytes) -> dict:
    """/ToUnicode CMap stream -> {byte_code: unicode_str}. Handles both
    beginbfchar (explicit code->string pairs) and beginbfrange (a code range
    mapped either to a single incrementing base value or an explicit array
    per code) — the two constructs the PDF spec defines for this CMap type."""
    code_map = {}
    for block in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        pairs = _HEX_TOK.findall(block.group(1))
        for i in range(0, len(pairs) - 1, 2):
            code_map[int(pairs[i], 16)] = _hex_to_unicode_str(pairs[i + 1])
    for block in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for m in _BFRANGE_ENTRY.finditer(block.group(1)):
            lo_i, hi_i, dst = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
            if dst.startswith(b"["):
                for off, d in enumerate(_HEX_TOK.findall(dst)):
                    if lo_i + off > hi_i:
                        break
                    code_map[lo_i + off] = _hex_to_unicode_str(d)
            else:
                dst_hex = dst[1:-1]
                if len(dst_hex) == 4:
                    base = int(dst_hex, 16)
                    for off in range(hi_i - lo_i + 1):
                        code_map[lo_i + off] = chr(base + off)
                else:
                    s = _hex_to_unicode_str(dst_hex)
                    for off in range(hi_i - lo_i + 1):
                        code_map[lo_i + off] = s
    return code_map


# ── simple (non-CID) font code maps: empirically from /ToUnicode, mirroring
# _gid_maps()'s approach for CID fonts. A simple font's rendered "glyph-id"
# (from get_texttrace) is the font's OWN internal glyph index, unrelated to
# the PDF-level byte code — so unlike CID (Identity-mapped, gid==code) it
# can't be reused directly. But /ToUnicode is exactly "byte code -> text",
# which is what's needed, AND it's the same source PyMuPDF's own text
# extraction already trusts — so an edit search built from it can never
# disagree with what the user sees. This is what makes LaTeX/pdflatex
# documents (which very commonly fold "fi"/"fl" etc into a SINGLE byte code,
# via a custom /Encoding /Differences array) actually editable: the naive
# `text.encode("latin-1")` assumption silently splits a ligature into two
# ASCII bytes that never appear together in the real content stream. ──────
_CID_GLYPH_MEMO: dict = {}


def _cid_glyph_coverage(doc, font_display_name: str, cids):
    """Do these CIDs have real outlines in the embedded program?

    True / False, or None when it can't be determined (then the caller must
    not treat absence of proof as proof).

    This is the CID counterpart of _simple_font_glyph_coverage, and it exists
    because /ToUnicode is NOT evidence that a glyph can be drawn. It is a
    reverse map for text extraction: it can name a CID for a character the
    font program has no outline for. Encoding from it alone produced a page
    whose text extracted as 'Fécture' while the 'é' cell contained ZERO ink —
    the exact silent corruption this engine was built to stop, reintroduced
    by trusting the wrong table. The glyph program is the only authority on
    what can actually be drawn.
    """
    type0_xref = None
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[3].split("+")[-1] != font_display_name:
                continue
            if "/Subtype/Type0" not in doc.xref_object(f[0], compressed=True).replace(" ", ""):
                continue
            type0_xref = f[0]
            break
        if type0_xref:
            break
    if type0_xref is None:
        return None
    refs = _font_stream_refs(doc, type0_xref)
    if not refs or refs == "cff":
        return None
    try:
        raw = doc.xref_stream(refs["ff_xref"])
        if not raw:
            return None
        key = (len(raw), raw[:24], raw[-24:])
        glyf = _CID_GLYPH_MEMO.get(key)
        if glyf is None:
            from fontTools.ttLib import TTFont
            tt = TTFont(io.BytesIO(raw), fontNumber=0, lazy=True)
            if "glyf" not in tt:
                return None
            order = tt.getGlyphOrder()
            table = tt["glyf"]
            glyf = set()
            for gid, gname in enumerate(order):
                try:
                    g = table[gname]
                except Exception:  # noqa: BLE001
                    continue
                if getattr(g, "numberOfContours", 0) != 0:
                    glyf.add(gid)
            _CID_GLYPH_MEMO[key] = glyf
    except Exception:  # noqa: BLE001
        return None
    # CIDToGIDMap Identity is the overwhelmingly common case and the only one
    # this checks; anything else is reported as unknown rather than guessed.
    kind, val = doc.xref_get_key(refs["cid_xref"], "CIDToGIDMap")
    if kind == "name" and val not in ("/Identity", "Identity"):
        return None
    if kind == "xref":
        return None
    return all((c in glyf) or c == 0 for c in cids)


def _cid_code_maps(doc):
    """Type0 font display name -> {"rev": {text: CID}, "max_len": n}.

    The CID analogue of _simple_font_code_maps, and it exists because the
    merged map from get_texttrace cannot serve a CID font whose display name
    is shared. The extractor normalises 'Tw Cen MT Bold' and 'TwCenMT-Bold'
    to one name, so a texttrace map for that name mixes one font's TrueType
    glyph ids with the other's CIDs and neither font's codes come out whole.
    Reading each font OBJECT's own /ToUnicode keeps them apart — which is
    exactly what _simple_font_code_maps has always done for simple fonts.
    """
    return _tounicode_code_maps(doc, want_type0=True)


def _simple_font_code_maps(doc):
    return _tounicode_code_maps(doc, want_type0=False)


def _tounicode_code_maps(doc, want_type0: bool):
    out, seen_xrefs = {}, set()
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            xref, subtype, name = f[0], f[2], f[3].split("+")[-1]
            if (subtype == "Type0") != want_type0 or name in out or xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            obj = doc.xref_object(xref, compressed=True)
            m = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
            if not m:
                continue
            data = doc.xref_stream(int(m.group(1)))
            if not data:
                continue
            code_to_str = _parse_tounicode_cmap(data)
            if not code_to_str:
                continue
            rev = {}
            for code, s in code_to_str.items():
                if s and s not in rev:  # lowest/first code wins on duplicate text
                    rev[s] = code
            if rev:
                out[name] = {"rev": rev, "max_len": max(len(s) for s in rev)}
    return out


def _encode_simple_text(text, rev, max_len):
    """Greedy longest-match encode against a font's own empirical code map.
    Returns None (never a partial guess) if any part of the text isn't
    covered — the caller falls back to the whole string's plain latin-1
    encoding in that case, exactly the prior behavior, so this can only ever
    improve on it, never introduce a new silently-wrong edit."""
    codes, i, n = [], 0, len(text)
    while i < n:
        matched = False
        for L in range(min(max_len, n - i), 0, -1):
            code = rev.get(text[i:i + L])
            if code is not None:
                codes.append(code)
                i += L
                matched = True
                break
        if not matched:
            return None
    return codes


# A font with NO /ToUnicode at all (common on Acrobat/Distiller-generated
# forms — real example: an IRS fillable form's Helvetica/WinAnsiEncoding
# text) has no empirical code map to build from _simple_font_code_maps at
# all. The prior fallback in that case was a blind `text.encode("latin-1")`
# — WRONG for the ubiquitous case of a font that explicitly declares
# /Encoding /WinAnsiEncoding, because WinAnsiEncoding is Windows-1252, NOT
# Latin-1/ISO-8859-1, in the 0x80-0x9F range: exactly where ordinary
# smart-quote/en-dash/bullet characters live in everyday Word/Acrobat
# documents (U+2019 '’', U+2013 '–', U+2022 '•', ...) — latin-1
# can't encode them at all (raises), so every field containing one used to
# fail outright. Only applied when the font's OWN declared encoding name
# confirms it (get_fonts()'s encoding field) — for a custom /Differences
# font (reported as "" by PyMuPDF) this never triggers, leaving that path
# untouched.
_ENCODING_CODECS = {"WinAnsiEncoding": "cp1252", "MacRomanEncoding": "mac_roman"}


def _simple_font_encodings(doc):
    out = {}
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            name = f[3].split("+")[-1]
            if name not in out and f[5]:
                out[name] = f[5]
    return out


def _encode_fallback(text, encoding_name):
    codec = _ENCODING_CODECS.get(encoding_name)
    if codec:
        try:
            return list(text.encode(codec))
        except UnicodeEncodeError:
            pass
    try:
        return list(text.encode("latin-1"))
    except UnicodeEncodeError:
        return None


_SIMPLE_GLYPH_MEMO: dict = {}  # font display name -> set of covered chars, or None


def _simple_font_glyph_coverage(doc, font_display_name: str):
    """Characters the embedded subset actually has a drawable glyph for, per
    the font program's own cmap table — distinct from _encode_fallback's
    WinAnsi/MacRoman *encoding* table, which defines a byte<->character
    mapping regardless of whether this specific subsetted font file embeds
    that glyph's outline. A subset built for the document's ORIGINAL text
    (e.g. "Ilyas Zouine") legitimately has no outline for 'h' or 'm' — the
    encoding table still reports a valid byte for them, so _encode_fallback
    "succeeds" while what actually gets painted is garbage. Returns None if
    the font can't be found/parsed (caller should skip the check, not treat
    unknown as missing)."""
    if font_display_name in _SIMPLE_GLYPH_MEMO:
        return _SIMPLE_GLYPH_MEMO[font_display_name]
    coverage = None
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[3].split("+")[-1] != font_display_name:
                continue
            try:
                raw = doc.extract_font(f[0])[3]
                if raw:
                    from fontTools.ttLib import TTFont
                    import io as _io
                    tt = TTFont(_io.BytesIO(raw), fontNumber=0, lazy=True)
                    cmap = tt.getBestCmap() or {}
                    coverage = {chr(cp) for cp in cmap}
            except Exception:  # noqa: BLE001 — unparseable font -> skip, don't block
                coverage = None
            break
        if coverage is not None:
            break
    _SIMPLE_GLYPH_MEMO[font_display_name] = coverage
    return coverage


def _page_font_refmap(page):
    """PDF resource name (e.g. 'F0') -> display font name (subset prefix stripped)."""
    return {f[4]: f[3].split("+")[-1] for f in page.get_fonts(full=True)}


def _page_font_subtypes(page):
    """PDF resource name -> that resource's own PDF subtype.

    Needed because a display name is not unique. This fixture embeds
    'TwCenMT-Regular' TWICE — once as a TrueType font (subset tag BCDFEE+)
    and once as a Type0 CID font (BCDGEE+) — and stripping the subset tag
    makes them indistinguishable by name. A single name->subtype map silently
    keeps whichever came last, so every run drawn with the other one was
    decoded under the wrong encoding.
    """
    return {f[4]: f[2] for f in page.get_fonts(full=True)}


# ── every physical content stream text can live in: the page's own stream,
# PLUS every Form XObject it uses (see module docstring — this is what makes
# whole-page-as-one-XObject exports, e.g. many headless-browser PDF exports,
# actually searchable instead of reporting zero editable text). ──────────────
def _content_streams(doc, page):
    streams = []
    page.clean_contents()
    for pxref in page.get_contents():
        data = doc.xref_stream(pxref)
        if data:
            streams.append({"xref": pxref, "data": data})
    seen = {s["xref"] for s in streams}
    for xo in page.get_xobjects():
        xref = xo[0]
        if xref in seen:
            continue
        try:
            subtype = doc.xref_get_key(xref, "Subtype")
        except Exception:  # noqa: BLE001
            subtype = None
        if subtype and subtype[1] == "/Form":
            data = doc.xref_stream(xref)
            if data:
                streams.append({"xref": xref, "data": data})
                seen.add(xref)
    return streams


def _all_runs(streams):
    """_text_runs() per stream, each run tagged with the xref/bytes it came
    from, so a located edit is written back to the right physical object."""
    runs = []
    for s in streams:
        for r in _text_runs(s["data"]):
            r["xref"] = s["xref"]
            r["data"] = s["data"]
            runs.append(r)
    return runs


# ── a minimal PDF content-stream tokenizer (enough to find text runs) ────────
_WS = set(b" \t\r\n\f\x00")
_DELIM = set(b"()<>[]{}/%")


_LIT_ESCAPES = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C,
               0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}


def _decode_pdf_literal(raw: bytes) -> bytes:
    """A PDF literal string's `\\ddd` octal escapes (and \\n\\r\\t\\b\\f\\\\()
    escapes) represent single ARBITRARY bytes — including exactly the kind of
    non-printable byte codes fonts assign to ligatures ("fi" as a single byte
    2, written in the stream as the four characters \\, 0, 0, 2). Reading the
    token's payload without decoding these treats that as four separate
    literal characters instead of the one real byte, so it silently never
    matches anything real. Depth-tracking in the tokenizer above only needs
    to find where the string ENDS (any char after a backslash is skipped,
    escaped or not) — this is the separate step that recovers the actual
    intended bytes for matching/searching."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != 0x5C:
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        c2 = raw[i]
        if c2 in _LIT_ESCAPES:
            out.append(_LIT_ESCAPES[c2])
            i += 1
        elif 0x30 <= c2 <= 0x37:
            val, cnt = 0, 0
            while i < n and cnt < 3 and 0x30 <= raw[i] <= 0x37:
                val = val * 8 + (raw[i] - 0x30)
                i += 1
                cnt += 1
            out.append(val & 0xFF)
        elif c2 in (0x0D, 0x0A):
            if c2 == 0x0D and i + 1 < n and raw[i + 1] == 0x0A:
                i += 2
            else:
                i += 1
        else:
            out.append(c2)
            i += 1
    return bytes(out)


def _tokenize(data: bytes):
    toks, i, n = [], 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1; continue
        if c == 0x25:  # % comment
            while i < n and data[i] not in (0x0A, 0x0D):
                i += 1
            continue
        if c == 0x28:  # ( literal string
            j = i; i += 1; depth = 1
            while i < n and depth:
                if data[i] == 0x5C:  # backslash escape
                    i += 2; continue
                if data[i] == 0x28: depth += 1
                elif data[i] == 0x29: depth -= 1
                i += 1
            toks.append(("str_lit", j, i, _decode_pdf_literal(data[j + 1:i - 1])))
            continue
        if c == 0x3C:  # <
            if i + 1 < n and data[i + 1] == 0x3C:
                toks.append(("op", i, i + 2, b"<<")); i += 2; continue
            j = i; i += 1
            while i < n and data[i] != 0x3E:
                i += 1
            body = data[j + 1:i]; i += 1
            toks.append(("str_hex", j, i, bytes(ch for ch in body if ch not in _WS)))
            continue
        if c == 0x3E:  # >
            if i + 1 < n and data[i + 1] == 0x3E:
                toks.append(("op", i, i + 2, b">>")); i += 2; continue
            i += 1; continue
        if c == 0x5B:
            toks.append(("arr_open", i, i + 1, None)); i += 1; continue
        if c == 0x5D:
            toks.append(("arr_close", i, i + 1, None)); i += 1; continue
        if c == 0x2F:  # /name
            j = i; i += 1
            while i < n and data[i] not in _WS and data[i] not in _DELIM:
                i += 1
            toks.append(("name", j, i, data[j:i])); continue
        j = i
        while i < n and data[i] not in _WS and data[i] not in _DELIM:
            i += 1
        tok = data[j:i] or bytes([data[i]])
        if not tok:
            i += 1; continue
        toks.append(("num" if tok[:1] in b"+-.0123456789" else "op", j, i, tok))
    return toks


def _gids_from_tok(t):
    """A string token's raw bytes -> list of 2-byte codes (CID) — the byte-pair
    reading used everywhere below; for simple fonts the caller reads 1 byte at
    a time instead (see _text_runs, which keeps both interpretations cheap by
    storing raw codes per byte AND per byte-pair)."""
    kind, _s, _e, payload = t
    out = []
    if kind == "str_hex":
        hx = payload.decode("ascii", "ignore")
        if len(hx) % 2:
            hx += "0"
        raw = bytes.fromhex(hx)
    else:
        raw = payload
    return raw


def _codes_1byte(raw: bytes):
    return list(raw)


def _codes_2byte(raw: bytes):
    if len(raw) % 2:
        raw += b"\x00"
    return [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw), 2)]


# ── text runs: every Tj/TJ/'/" call, broken into its own string tokens ──────
def _text_runs(data: bytes):
    """
    Every text-showing operator call, in document order. Each run:
      {op, font: b'/F0' or None,
       start, end,               # the OPERAND's byte range (for reference)
       full_start, full_end,     # operand THROUGH the operator keyword — the
                                  # range that can be safely deleted whole
       str_toks: [ {start,end,raw(bytes)} ]}   # each string token, in order
    Codes are decoded lazily by the caller (1-byte for simple fonts, 2-byte
    for CID), since the SAME tokenizer output serves both font kinds.
    """
    toks = _tokenize(data)
    runs, cur_font = [], None
    TEXT_OPS = (b"Tj", b"'", b'"')
    for idx, t in enumerate(toks):
        if t[0] != "op":
            continue
        if t[3] == b"Tf":
            for k in range(idx - 1, max(-1, idx - 5), -1):
                if toks[k][0] == "name":
                    cur_font = toks[k][3]; break
        elif t[3] in TEXT_OPS:
            k = idx - 1
            if k >= 0 and toks[k][0] in ("str_hex", "str_lit"):
                raw = _gids_from_tok(toks[k])
                runs.append({
                    "op": t[3].decode(), "font": cur_font,
                    "start": toks[k][1], "end": toks[k][2],
                    "full_start": toks[k][1], "full_end": t[2],
                    "str_toks": [{"start": toks[k][1], "end": toks[k][2], "raw": raw}],
                })
        elif t[3] == b"TJ":
            if idx - 1 < 0 or toks[idx - 1][0] != "arr_close":
                continue
            arr_end = toks[idx - 1][2]
            j = idx - 2
            seq = []
            while j >= 0 and toks[j][0] != "arr_open":
                seq.append(toks[j]); j -= 1
            if j < 0:
                continue
            arr_start = toks[j][1]
            seq.reverse()
            str_toks = []
            pending_gap = None
            for tt in seq:
                if tt[0] == "num":
                    try:
                        pending_gap = float(tt[3])
                    except ValueError:
                        pending_gap = None
                    continue
                if tt[0] in ("str_hex", "str_lit"):
                    str_toks.append({"start": tt[1], "end": tt[2], "raw": _gids_from_tok(tt),
                                    "gap_before": pending_gap})
                    pending_gap = None
            if str_toks:
                runs.append({
                    "op": "TJ", "font": cur_font,
                    "start": arr_start, "end": arr_end,
                    "full_start": arr_start, "full_end": t[2],
                    "str_toks": str_toks,
                })
    return runs


def _decode_toks(run, is_cid):
    """Attach '.codes' (list[int]) to each str_tok of `run`, per byte width."""
    dec = _codes_2byte if is_cid else _codes_1byte
    for tok in run["str_toks"]:
        tok["codes"] = dec(tok["raw"])
    return run


# A TJ array's numbers are pure positioning, not glyphs — but many real PDF
# generators (dvips/pdfTeX output is the textbook case) draw an inter-word
# SPACE this way instead of an actual space glyph: "[(Google)-250(Brain)]TJ"
# has no space character anywhere in it. A small in-word kerning nudge
# (tightening "Wo", "Pr", etc) is a totally different, much smaller number —
# empirically well under 100 in magnitude on real documents — so a
# comfortably larger negative gap is treated as an inferred space and given
# a code (32) in the flattened stream, purely so a literal space in the
# searched text can match it. This is generous on purpose: a false positive
# here just means one more character to match against (a harmless, still-
# refused non-match if the search text didn't actually expect a space
# there) — it can never cause a wrong silent edit.
_SPACE_GAP_THRESHOLD = -150.0


# ── flatten codes across runs of ONE font, in document order ────────────────
def _flatten(runs, font_name, refmap, is_cid, refsub=None):
    flat, loc = [], []
    for ri, run in enumerate(runs):
        ref = (run["font"] or b"").decode("latin-1").lstrip("/")
        if refmap.get(ref) != font_name:
            continue
        if refsub is not None:
            # Only search runs drawn with a resource whose OWN subtype matches
            # the encoding being assumed. Two resources can share a display
            # name with different subtypes, and decoding a 2-byte CID run as
            # single bytes (or the reverse) yields codes that match nothing.
            if (refsub.get(ref) == "Type0") != bool(is_cid):
                continue
        _decode_toks(run, is_cid)
        for ti, tok in enumerate(run["str_toks"]):
            if ti > 0 and not is_cid:
                gap = tok.get("gap_before")
                if gap is not None and gap <= _SPACE_GAP_THRESHOLD:
                    flat.append(32)
                    loc.append((ri, "gap", ti))
            for ci, g in enumerate(tok["codes"]):
                flat.append(g)
                loc.append((ri, ti, ci))
    return flat, loc


def _find_all(flat, needle):
    if not needle or len(needle) > len(flat):
        return []
    out, n, m = [], len(flat), len(needle)
    for i in range(n - m + 1):
        if flat[i:i + m] == needle:
            out.append(i)
    return out


def _codes_to_bytes(codes, is_cid):
    if is_cid:
        width, fmt = 4, "%04X"
    else:
        width, fmt = 2, "%02X"
    return ("".join(fmt % (c & (0xFFFF if is_cid else 0xFF)) for c in codes)).encode()


# ── locate (pure — never mutates) ────────────────────────────────────────────
def _locate(runs, refmap, font_name, old_codes, is_cid, which=None,
            refsub=None):
    flat, loc = _flatten(runs, font_name, refmap, is_cid, refsub=refsub)
    if not flat:
        return {"ok": False, "reason": "no_runs_for_font"}
    positions = _find_all(flat, old_codes)
    if not positions:
        return {"ok": False, "reason": "sequence_not_found"}
    n_occurrences = len(positions)
    p = positions[0]
    # `which` selects among several occurrences of the same string in this
    # font on this page. The caller decides which one it means by TRYING each
    # and checking that the span it pointed at actually changed (see edit),
    # because estimating each occurrence's position from the content stream
    # proved producer-dependent: walking back for the last Tm and
    # accumulating Td/TD works on a LaTeX paper and yields coordinates
    # matching nothing on a Chrome-printed export.
    if which is not None and 0 <= which < n_occurrences:
        p = positions[which]
    L = len(old_codes)
    touched = []
    for k in range(p, p + L):
        ri = loc[k][0]
        if not touched or touched[-1] != ri:
            touched.append(ri)
    r_first, r_last = touched[0], touched[-1]

    if len({runs[ri]["xref"] for ri in touched}) > 1:
        return {"ok": False, "reason": "spans_multiple_streams"}

    if r_first == r_last:
        ti0, ci0 = loc[p][1], loc[p][2]
        ti1, ci1 = loc[p + L - 1][1], loc[p + L - 1][2]
        if ti0 == "gap" or ti1 == "gap":
            return {"ok": False, "reason": "kerning_split_within_run"}
        if ti0 == ti1:
            return {"ok": True, "case": "single_token", "run": r_first, "tok": ti0,
                    "code_lo": ci0, "code_hi": ci1, "n_occurrences": n_occurrences}
        # Spans multiple string tokens WITHIN one TJ call (e.g. one token per
        # word, as dvips/pdfTeX commonly emits). Landing cleanly on both
        # token boundaries (start of the first, end of the last) only proves
        # the match doesn't straddle a token PARTIALLY — it says nothing
        # about whether the gap(s) being crossed are safe to drop. A large
        # negative gap between two DIFFERENT words is fungible inter-word
        # spacing (dropping the exact number and using default spacing there
        # is not a visible style change). A small kerning nudge INSIDE what
        # is really one continuous word/phrase (e.g. hand-tuned letter-pair
        # spacing) is deliberate styling — collapsing it away would silently
        # change the rendered result, exactly what this engine refuses to
        # risk. So EVERY boundary crossed must itself look like a real
        # inter-word gap, not just the outer edges of the match.
        str_toks = runs[r_first]["str_toks"]
        last_tok_len = len(str_toks[ti1]["codes"])
        boundaries_ok = all(
            (str_toks[t].get("gap_before") is not None
             and str_toks[t]["gap_before"] <= _SPACE_GAP_THRESHOLD)
            for t in range(ti0 + 1, ti1 + 1)
        )
        if ci0 == 0 and ci1 == last_tok_len - 1:
            # Whole tokens on both ends, so nothing outside the match is
            # touched. If a crossed gap is a real inter-word space this is the
            # plain multi-token case. If one of them is a KERNING nudge, the
            # rewrite also discards that nudge — and that is now allowed
            # rather than refused, because the nudge belongs to text being
            # replaced in its entirety. Preserving one letter-pair's spacing
            # from a value that no longer exists is not fidelity, and refusing
            # over it was the single largest category of otherwise-editable
            # fields (18 of 55 refusals across four documents).
            #
            # It is still a real difference, so it is REPORTED rather than
            # done quietly: `dekerned` says it happened and `dropped_gaps`
            # says by how much.
            #
            # Partial token coverage stays refused: with ci0 > 0 the token
            # holds text BEFORE the match, and collapsing from the token's
            # start would eat it.
            dropped = [str_toks[t].get("gap_before")
                       for t in range(ti0 + 1, ti1 + 1)]
            return {"ok": True, "case": "multi_token", "run": r_first,
                    "tok_lo": ti0, "tok_hi": ti1, "n_occurrences": n_occurrences,
                    "dekerned": not boundaries_ok,
                    "dropped_gaps": [g for g in dropped if g is not None]
                                    if not boundaries_ok else []}
        return {"ok": False, "reason": "kerning_split_within_run"}

    last_run = runs[r_last]
    last_tok_idx = len(last_run["str_toks"]) - 1
    last_code_idx = len(last_run["str_toks"][last_tok_idx]["codes"]) - 1
    starts_clean = (loc[p][1] == 0 and loc[p][2] == 0)
    ends_clean = (loc[p + L - 1][1] == last_tok_idx and loc[p + L - 1][2] == last_code_idx)
    if not (starts_clean and ends_clean):
        return {"ok": False, "reason": "ragged_multirun_boundary"}
    return {"ok": True, "case": "multi_run", "touched": touched, "n_occurrences": n_occurrences}


# ── apply (mutating — given a successful locate result) ─────────────────────
def _apply(doc, runs, loc_result, new_codes, is_cid):
    if loc_result["case"] == "single_token":
        run = runs[loc_result["run"]]
        xref, data = run["xref"], run["data"]
        tok = run["str_toks"][loc_result["tok"]]
        ci0, ci1 = loc_result["code_lo"], loc_result["code_hi"]
        merged = tok["codes"][:ci0] + new_codes + tok["codes"][ci1 + 1:]
        new_bytes = b"<" + _codes_to_bytes(merged, is_cid) + b">"
        new_data = data[:tok["start"]] + new_bytes + data[tok["end"]:]
    elif loc_result["case"] == "multi_token":
        # Whole tokens (tok_lo..tok_hi inclusive) plus everything between
        # them (other tokens, gap numbers, brackets) collapse into ONE new
        # string token — same "rewrite first, drop the rest" idea as
        # multi_run below, just done as a single splice since it's all one
        # physical run.
        run = runs[loc_result["run"]]
        xref, data = run["xref"], run["data"]
        start = run["str_toks"][loc_result["tok_lo"]]["start"]
        end = run["str_toks"][loc_result["tok_hi"]]["end"]
        new_hex = b"<" + _codes_to_bytes(new_codes, is_cid) + b">"
        new_data = data[:start] + new_hex + data[end:]
    else:
        touched = loc_result["touched"]
        r_first = touched[0]
        xref, data = runs[r_first]["xref"], runs[r_first]["data"]
        new_hex = _codes_to_bytes(new_codes, is_cid)
        first_repl = b"[<" + new_hex + b">]TJ"
        edits = [(runs[r_first]["full_start"], runs[r_first]["full_end"], first_repl)]
        for ri in touched[1:]:
            rr = runs[ri]
            edits.append((rr["full_start"], rr["full_end"], b""))
        edits.sort(key=lambda e: e[0], reverse=True)
        new_data = data
        for s, e, rep in edits:
            new_data = new_data[:s] + rep + new_data[e:]
    doc.update_stream(xref, new_data)


_REASON_MSG = {
    "no_runs_for_font": ("Couldn't find any text drawn with this font in the page's own content stream "
                        "or the Form XObjects it uses (nested more than one level deep, or drawn in some "
                        "other way this test doesn't parse yet)."),
    "sequence_not_found": ("Found the field via text extraction, but couldn't locate the exact glyph "
                          "sequence in the content (unusual encoding, or it's drawn through something "
                          "this test doesn't parse)."),
    "spans_multiple_streams": ("This text would need to be edited across two different embedded objects "
                              "at once (e.g. partly on the page, partly inside an embedded object) — "
                              "refusing rather than risk a mismatched edit."),
    "kerning_split_within_run": ("This text starts or ends part-way through a drawing instruction that "
                                "carries its own letter-spacing, so rewriting it would disturb the "
                                "spacing of neighbouring text that isn't being changed. (A kerned run "
                                "the change covers COMPLETELY is rebuilt instead — only the partial "
                                "case is refused.)"),
    "ragged_multirun_boundary": ("This text starts or ends in the middle of a drawing instruction that "
                                "ALSO contains other, unrelated text — this test only edits runs that are "
                                "cleanly and entirely covered by the change."),
    "spans_multiple_fonts": ("This text is drawn with more than one font — a word processor will "
                            "switch fonts mid-sentence for a character its first font lacks, and this "
                            "engine edits text within a single font at a time. Editing it would mean "
                            "coordinating a splice across two font resources at once."),
    "cannot_reflow": ("This value is longer than the one it replaces, and the text after it "
                      "on the same line is positioned in a way that can't be moved to make "
                      "room — so it can't be lengthened here without drawing over that text."),
    "no_content_stream": "This page has no content stream.",
    "not_found": "Couldn't find that text in the document.",
    "unmappable": "Couldn't map every character to a glyph (complex/shaped script?).",
    "encoding": "A character here isn't supported by this simple font's encoding.",
}


def analyze(pdf_bytes: bytes) -> dict:
    """Per field: font/subtype + whether an edit of the field's OWN text would
    currently locate cleanly (a same-text probe), and why not if it wouldn't.
    This lets the UI show what's editable BEFORE the user tries anything."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    subtype, used = _font_info(doc)
    gid_maps = None
    simple_maps = None
    simple_encodings = None
    fields, simple, cid = [], 0, 0
    for pno in range(doc.page_count):
        page = doc[pno]
        runs = _all_runs(_content_streams(doc, page))
        refmap = _page_font_refmap(page)
        for s in _spans(page):
            nm = s["font"].split("+")[-1]
            st = subtype.get(nm, "?")
            is_simple = st != "Type0"
            simple += is_simple
            cid += (not is_simple)
            text = _norm(s["text"])

            codes = None
            if is_simple:
                if simple_maps is None:
                    simple_maps = _simple_font_code_maps(doc)
                if simple_encodings is None:
                    simple_encodings = _simple_font_encodings(doc)
                cm = simple_maps.get(nm)
                codes = _encode_simple_text(text, cm["rev"], cm["max_len"]) if cm else None
                if codes is None:
                    codes = _encode_fallback(text, simple_encodings.get(nm))
            else:
                if gid_maps is None:
                    gid_maps = _gid_maps(doc)
                fm = gid_maps.get(nm, {})
                cand = [fm.get(ch) for ch in text]
                codes = cand if all(c is not None for c in cand) else None

            editable, limitation = False, "couldn't map this field's own text to codes"
            if codes is not None:
                res = _locate(runs, refmap, nm, codes, not is_simple)
                editable = res["ok"]
                limitation = None if editable else _REASON_MSG.get(res["reason"], res["reason"])

            fields.append({
                "id": len(fields), "page": pno, "text": text, "font": nm, "subtype": st,
                "simple": bool(is_simple),
                "bbox": [round(v, 1) for v in s["bbox"]],
                "editable": editable, "limitation": limitation,
            })
    page_sizes = [{"width": doc[i].rect.width, "height": doc[i].rect.height}
                 for i in range(doc.page_count)]
    n_pages = doc.page_count
    doc.close()
    return {"pages": n_pages, "page_sizes": page_sizes,
            "count": len(fields), "simple": simple, "cid": cid, "fields": fields}


# ── "extend" tier: inject a missing glyph via font_extend.py ────────────────
# Only attempted when a real edit needs a character that has genuinely never
# been drawn in this exact embedded font/weight/style before (see
# font_extend.py's module docstring for why that's a real, common case — not
# a bug). Falls back to the honest refusal if anything about it fails: unknown
# font family, network failure, non-glyf font, unitsPerEm mismatch, etc.
def _font_stream_refs(doc, type0_xref):
    """Type0 font xref -> {"cid_xref", "ff_xref", "tu_xref"} (the descendant
    CIDFontType2 dict, its embedded FontFile2 stream, and the /ToUnicode CMap
    stream), or None if the chain is missing or shaped differently than
    expected. /ToUnicode matters even though rendering itself (CIDToGIDMap)
    never consults it: it's the SEPARATE map text-extraction, copy/paste,
    search and accessibility tools use to know what Unicode character a glyph
    represents — skip updating it and a newly-injected glyph displays
    correctly but extracts as U+FFFD, which is exactly the kind of silent,
    non-obvious corruption this whole engine exists to avoid."""
    obj = doc.xref_object(type0_xref, compressed=True)
    # /DescendantFonts is an array, and the array may be written inline
    # ("[11 0 R]") or as an indirect reference to an array object ("11 0 R").
    # Both are valid PDF and different producers pick different ones: PyMuPDF
    # writes it inline, Word/Office writes it indirect. Matching only the
    # inline form made the CID extend tier fail with "no_stream_refs" on every
    # Office-produced document — the same both-forms handling the /W array
    # already had, just never applied here.
    m = re.search(r"/DescendantFonts\s*\[\s*(\d+)\s+0\s+R", obj)
    if not m:
        m_ind = re.search(r"/DescendantFonts\s+(\d+)\s+0\s+R", obj)
        if not m_ind:
            return None
        arr = doc.xref_object(int(m_ind.group(1)), compressed=True) or ""
        m = re.search(r"(\d+)\s+0\s+R", arr)
        if not m:
            return None
    cid_xref = int(m.group(1))
    cid_obj = doc.xref_object(cid_xref, compressed=True)
    m2 = re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", cid_obj)
    if not m2:
        return None
    fd_xref = int(m2.group(1))
    fd_obj = doc.xref_object(fd_xref, compressed=True)
    m3 = re.search(r"/FontFile2\s+(\d+)\s+0\s+R", fd_obj)
    if not m3:
        # /FontFile3 = CFF-flavored OpenType or bare CFF (Subtype /Type1C or
        # /OpenType) — a genuinely different, not-yet-supported outline format,
        # not just "the chain is missing"; flag it distinctly so the caller can
        # report the real reason instead of a generic "unknown font family".
        if re.search(r"/FontFile3\s+\d+\s+0\s+R", fd_obj):
            return "cff"
        return None
    m4 = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
    tu_xref = int(m4.group(1)) if m4 else None
    return {"cid_xref": cid_xref, "ff_xref": int(m3.group(1)), "tu_xref": tu_xref}


def _add_tounicode_entries(doc, tu_xref, gid_to_unicode: dict,
                           hex_digits: int = 4) -> bool:
    """Append a beginbfchar/endbfchar block mapping each new GID (as a 2-byte
    CID, since CIDToGIDMap=Identity) to its Unicode codepoint, right before
    endcmap. Multiple bfchar blocks in one CMap are standard, valid syntax —
    the existing ones are never touched. Returns False (no-op) if the stream
    doesn't look like a CMap we understand, rather than risk corrupting it."""
    if tu_xref is None or not gid_to_unicode:
        return False
    data = doc.xref_stream(tu_xref)
    if not data or b"endcmap" not in data:
        return False
    lines = [f"{len(gid_to_unicode)} beginbfchar"]
    for gid, cp in gid_to_unicode.items():
        # A simple font's CMap keys are 1-byte character codes; a CID font's
        # are 2-byte CIDs. Writing four hex digits into a 1-byte codespace
        # produces a CMap no extractor can follow.
        lines.append(f"<{gid:0{hex_digits}X}> <{cp:04X}>")
    lines.append("endbfchar\n")
    block = ("\n".join(lines)).encode("latin-1")
    idx = data.rfind(b"endcmap")
    new_data = data[:idx] + block + data[idx:]
    doc.update_stream(tu_xref, new_data)
    return True


_EXTEND_FAIL_MSG = {
    "no_font_ref": "Couldn't locate this font's own reference on the page.",
    "no_stream_refs": "Couldn't locate this font's embedded file inside the PDF structure.",
    "no_donor": ("This font's family isn't recognized as one this test can find an open-source "
                "donor for (custom/commercial font, an unusual name, or a temporary network/rate-"
                "limit issue fetching it)."),
    "not_glyf": ("This font uses CFF/PostScript outlines (not TrueType/glyf) — glyph injection "
                "for this font type isn't built yet."),
    "units_per_em_mismatch": "This font and its donor use incompatible internal scales — skipped rather than risk a wrong size.",
    "glyph_not_in_donor": "The open-source donor font doesn't have this exact character either.",
    "bad_width_array": "This font's width table has an unexpected structure — skipped rather than risk misaligned text.",
    "extend_failed": "Couldn't merge the glyph into this font.",
}


def _finish_cid_extend(doc, refs, result, missing_chars):
    """Write an extended CID font back: /W widths, the font program,
    and /ToUnicode. Shared by both donor sources."""
    kind, val = doc.xref_get_key(refs["cid_xref"], "W")
    additions = "".join(f" {result['gid'][ch]}[{result['width_1000'][ch]}]" for ch in missing_chars)
    if kind == "array" and val and val.endswith("]"):
        # /W stored inline on the CIDFontType2 dict itself.
        doc.xref_set_key(refs["cid_xref"], "W", val[:-1] + additions + "]")
    elif kind == "xref" and val:
        # /W stored as an indirect reference to a separate array object (also
        # valid PDF — PyMuPDF's own embedder produces this form) — rewrite
        # THAT object's content directly, leaving the CIDFontType2 dict's /W
        # key pointing at the same xref.
        m = re.match(r"(\d+)\s+0\s+R", val)
        if not m:
            return None, "bad_width_array"
        w_xref = int(m.group(1))
        arr = doc.xref_object(w_xref, compressed=True)
        if not arr or not arr.strip().endswith("]"):
            return None, "bad_width_array"
        doc.update_object(w_xref, arr.strip()[:-1] + additions + "]")
    else:
        return None, "bad_width_array"  # unexpected /W shape — refuse rather than risk it
    doc.update_stream(refs["ff_xref"], result["font_bytes"])
    _add_tounicode_entries(doc, refs.get("tu_xref"),
                           {result["gid"][ch]: ord(ch) for ch in missing_chars})
    return result["gid"], None


def _try_extend(doc, font_display_name, missing_chars):
    """Attempt to inject `missing_chars` into font_display_name's embedded
    subset. On success, mutates `doc` (new FontFile2 + extended /W array) and
    returns ({char: gid}, None); on failure returns (None, reason) and leaves
    doc untouched (well-formed no-op — callers must not assume partial
    success). `reason` is a specific, named cause (see _EXTEND_FAIL_MSG) —
    never a single catch-all, so the actual problem (e.g. a CFF-outline font,
    vs. a truly unknown family) is visible instead of masked."""
    type0_xref = None
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[3].split("+")[-1] != font_display_name:
                continue
            # Only a Type0 object can have the descendant chain this walks.
            # A display name is not unique — the attestation fixture embeds
            # 'TwCenMT-Regular' as both a TrueType and a Type0 font — and
            # taking the first name match handed a TrueType xref to a lookup
            # for /DescendantFonts, which then reported "no_stream_refs" for
            # a font that was perfectly extendable.
            if "/Subtype/Type0" not in doc.xref_object(f[0], compressed=True).replace(" ", ""):
                continue
            type0_xref = f[0]
            break
        if type0_xref:
            break
    if type0_xref is None:
        return None, "no_font_ref"
    refs = _font_stream_refs(doc, type0_xref)
    if refs == "cff":
        return None, "not_glyf"
    if not refs:
        return None, "no_stream_refs"
    subset_bytes = doc.xref_stream(refs["ff_xref"])

    # The family's own other cut, already embedded in this document, before
    # anything downloadable. Without this the CID path takes whatever
    # resolve_donor offers — which for a commercial family is a lookalike, so
    # a Tw Cen MT field would quietly gain Poppins letterforms even though a
    # real Tw Cen MT cut is sitting a few objects away in the same file. The
    # simple-font path has looked here since it was written; this one never
    # did.
    try:
        import font_donors
        import glyph_synth
        in_doc = font_donors.find_in_document_donor(
            doc, font_display_name, missing_chars)
        if in_doc:
            result = glyph_synth.inject_into_font(subset_bytes, in_doc["glyphs"])
            return _finish_cid_extend(doc, refs, result, missing_chars)
    except Exception:  # noqa: BLE001 — fall through to the network donor
        pass

    donor, donor_kind = font_extend.resolve_donor_detailed(font_display_name)
    if not donor:
        return None, "no_donor"
    # A SUBSTITUTE — a different typeface standing in for one with no
    # open-source relative — must be measured before it is allowed on the
    # page. Injected on the unitsPerEm ratio alone it keeps every one of its
    # own proportions: a Poppins 'z' standing in for Tw Cen MT Bold came out
    # 33% too tall and read as a capital Z inside the word. The genuine family
    # needs no such correction and takes the path below unchanged.
    if donor_kind == "substitute":
        try:
            import font_donors
            import glyph_synth
            corrected = font_donors.correct_external_donor(
                subset_bytes, donor, missing_chars)
        except Exception:  # noqa: BLE001
            corrected = None
        if corrected:
            result = glyph_synth.inject_into_font(subset_bytes, corrected["glyphs"])
            return _finish_cid_extend(doc, refs, result, missing_chars)
        # The transform could not be learned at all. Raw injection is then the
        # only option left, and it is disclosed rather than silent.
    try:
        result = font_extend.extend_font(subset_bytes, donor, missing_chars)
    except ValueError as e:
        msg = str(e)
        if "glyf-outline" in msg:
            reason = "not_glyf"
        elif "unitsPerEm mismatch" in msg:
            reason = "units_per_em_mismatch"
        elif "no glyph for" in msg:
            reason = "glyph_not_in_donor"
        else:
            reason = "extend_failed"
        return None, reason
    except Exception:  # noqa: BLE001 — any other failure -> honest refusal, not a guess
        return None, "extend_failed"

    return _finish_cid_extend(doc, refs, result, missing_chars)


_SIMPLE_EXTEND_FAIL_MSG = {
    "no_font_ref": "Couldn't locate this font's own reference on the page.",
    "no_fontfile": "This font's outlines aren't embedded as a TrueType program.",
    "no_donor": ("No other cut of this family is embedded in the document, and no "
                 "open-source donor could be resolved for it."),
    "bad_widths": "This font's width table has an unexpected structure — skipped rather than risk misaligned text.",
    "inject_failed": "Couldn't merge the glyph into this font.",
    "donor_too_different": (
        "This character isn't in the document's own copy of this font, no other "
        "cut of the family is embedded either, and the substitute available for "
        "it measures too far from these letterforms to stand in for them — so "
        "drawing it would visibly change the typeface."),
}


def _simple_font_refs(doc, font_display_name: str, require_truetype: bool = True):
    """Locate a SIMPLE (non-CID) font by display name.

    `require_truetype` gates only the OUTLINE requirement. Glyph injection
    needs a /FontFile2 TrueType program, but reading advance widths does not:
    /FirstChar and /Widths are on the font dictionary of a Type1 font just the
    same. Demanding TrueType for both meant width lookups failed on every
    Type1 font — which is most of a LaTeX document — and that silently
    disabled the overflow check on exactly those files.

    Returns {"font_xref", "fd_xref", "ff_xref", "first_char", "last_char",
    "widths_kind", "widths_ref", "tu_xref"} or None.

    Kept separate from _font_stream_refs, which walks the Type0 ->
    DescendantFonts -> CIDFontType2 chain. A simple font has no descendant:
    its widths live in a flat /Widths array indexed from /FirstChar, and its
    outlines hang directly off its own /FontDescriptor.
    """
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[3].split("+")[-1] != font_display_name:
                continue
            xref = f[0]
            obj = doc.xref_object(xref, compressed=True)
            flat = obj.replace(" ", "")
            if "/Subtype/Type0" in flat:
                continue          # CID font: a different structure entirely
            if require_truetype and "/Subtype/TrueType" not in flat:
                continue
            m = re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", obj)
            if not m:
                return None
            fd_xref = int(m.group(1))
            fd = doc.xref_object(fd_xref, compressed=True)
            m2 = re.search(r"/FontFile2\s+(\d+)\s+0\s+R", fd)
            if not m2 and require_truetype:
                return None
            fc = re.search(r"/FirstChar\s+(\d+)", obj)
            lc = re.search(r"/LastChar\s+(\d+)", obj)
            tu = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
            kind, val = doc.xref_get_key(xref, "Widths")
            return {"font_xref": xref, "fd_xref": fd_xref,
                    "ff_xref": int(m2.group(1)) if m2 else None,
                    "first_char": int(fc.group(1)) if fc else None,
                    "last_char": int(lc.group(1)) if lc else None,
                    "widths_kind": kind, "widths_val": val,
                    "tu_xref": int(tu.group(1)) if tu else None}
    return None


def _set_simple_widths(doc, refs, code_to_width: dict) -> bool:
    """Write real advance widths into the font's /Widths array.

    Not optional. A subsetter that drops a glyph also zeroes its width, and
    the attestation fixture's Bold font really does carry 0 for 'h', 'm' and
    '4'. A simple font positions text from /Widths, not from the font
    program's own hmtx, so injecting a beautiful glyph and leaving its width
    at 0 stacks every following letter on top of it.
    """
    first, last = refs["first_char"], refs["last_char"]
    if first is None or last is None:
        return False
    kind, val = refs["widths_kind"], refs["widths_val"]
    if kind == "array" and val:
        text = val
        w_xref = None
    elif kind == "xref" and val:
        m = re.match(r"(\d+)\s+0\s+R", val)
        if not m:
            return False
        w_xref = int(m.group(1))
        text = doc.xref_object(w_xref, compressed=True)
    else:
        return False

    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    widths = [float(n) for n in nums]
    if len(widths) != (last - first + 1):
        return False   # unexpected shape — refuse rather than misalign the table

    for code, w in code_to_width.items():
        idx = code - first
        if 0 <= idx < len(widths):
            widths[idx] = w
        else:
            return False   # outside the declared range; extending it is a
                           # bigger change than this path should make silently
    body = "[" + " ".join(f"{int(round(w))}" for w in widths) + "]"
    if w_xref is not None:
        doc.update_object(w_xref, body)
    else:
        doc.xref_set_key(refs["font_xref"], "Widths", body)
    return True


def _try_extend_simple(doc, font_display_name, missing_chars, code_for=None):
    """Inject `missing_chars` into a SIMPLE font's embedded subset, in place.

    This is what keeps an edit from leaving a trace. Without it the caller
    refuses, the request falls through to the redraw engine, and that engine
    rewrites the page: it paints over the original text with a sampled
    background rectangle, stamps replacement text from a NEWLY EMBEDDED font,
    and leaves the document carrying a font resource its producer never
    wrote. Here nothing new is added to the page at all — the original font
    object keeps its name and its identity and simply gains the glyphs it was
    missing.

    `code_for` maps each character to the CHARACTER CODE this font draws it
    with, which is not the same as its Unicode codepoint. A simple font's
    /Widths is indexed by code, and under WinAnsiEncoding the 0x80-0x9F band
    holds the characters Windows-1252 puts there — a right single quote is
    byte 0x92, not U+2019. Deriving the index from ord() therefore looked up
    8217 in a table declared for codes 32..233, failed, and refused the whole
    edit. Any replacement containing a smart quote or an en-dash hit this,
    which is most real prose.

    Returns ({char: gid}, None) or (None, reason).
    """
    if code_for is None:
        code_for = {}
    refs = _simple_font_refs(doc, font_display_name)
    if not refs:
        return None, "no_font_ref"
    try:
        subset_bytes = doc.xref_stream(refs["ff_xref"])
    except Exception:  # noqa: BLE001
        return None, "no_fontfile"
    if not subset_bytes:
        return None, "no_fontfile"

    result = None
    provenance = None
    # 1. The family's own other cut, already in this document.
    try:
        import font_donors
        import glyph_synth
        donor = font_donors.find_in_document_donor(
            doc, font_display_name, missing_chars)
        if donor:
            result = glyph_synth.inject_into_font(subset_bytes, donor["glyphs"])
            provenance = donor["provenance"]
    except Exception:  # noqa: BLE001 — fall through to the network donor
        result = None

    # 2. An open-source donor — through the same measured transform, so its
    #    own proportions do not come across unchanged.
    if result is None:
        try:
            raw, raw_kind = font_extend.resolve_donor_detailed(font_display_name)
            if not raw:
                return None, "no_donor"
            if raw_kind == "substitute":
                # Another typeface entirely: measure it, or say no. See
                # font_donors.correct_external_donor.
                corrected = font_donors.correct_external_donor(
                    subset_bytes, raw, missing_chars)
                if corrected:
                    result = glyph_synth.inject_into_font(subset_bytes,
                                                          corrected["glyphs"])
                    r = corrected["report"]
                    provenance = (f"{corrected['provenance']} "
                                  f"(held-out shape agreement "
                                  f"{r.get('iou_mean') or 0:.2f})")
                else:
                    result = font_extend.extend_font(subset_bytes, raw,
                                                     missing_chars)
                    provenance = "substitute typeface, uncorrected"
            else:
                result = font_extend.extend_font(subset_bytes, raw, missing_chars)
                provenance = "the genuine family, from an open-source catalogue"
        except Exception:  # noqa: BLE001
            return None, "inject_failed"

    if not _set_simple_widths(doc, refs,
                              {code_for.get(ch, ord(ch)): result["width_1000"][ch]
                               for ch in missing_chars}):
        return None, "bad_widths"

    doc.update_stream(refs["ff_xref"], result["font_bytes"])
    # A simple font's /ToUnicode is keyed by character CODE, not by glyph id.
    _add_tounicode_entries(doc, refs.get("tu_xref"),
                           {code_for.get(ch, ord(ch)): ord(ch) for ch in missing_chars},
                           hex_digits=2)
    return result["gid"], None


# Every operator that can position text inside a BT..ET object. Matching ALL
# of them matters: the origin of a text object is set by whichever comes
# first, and it is NOT always Tm.
#
# This was originally written to understand Tm only, on the reasoning that an
# absolute text matrix is how a generator pins a run to the page. That is true
# of LaTeX and of the synthetic fixture this engine was first built against —
# and false of Microsoft Word, which emits one text object per run in the form
#
#     BT /F4 14.04 Tf 123.62 547.39 TD [(Sara Idrissi)] TJ ET
#
# with no Tm anywhere on the page. Since the text matrix is the identity at
# BT, that leading `tx ty TD` is just as absolute as a Tm would be. Reading
# only Tm therefore found NOTHING to work with on an Office document, and
# both same-line reflow and the three elastic fitting levers degraded to
# silent no-ops: a longer replacement drew straight over the comma that
# followed it (65.8pt of overrun on the attestation fixture), and a value
# that needed a little tightening to fit was refused as unfittable instead.
_POSOP_RE = re.compile(
    rb"(?:^|[\s\]>)])(?:"
    rb"(?P<tm>(?P<ma>-?[\d.]+)\s+(?P<mb>-?[\d.]+)\s+(?P<mc>-?[\d.]+)\s+"
    rb"(?P<md>-?[\d.]+)\s+(?P<tmx>-?[\d.]+)\s+(?P<tmy>-?[\d.]+)\s+Tm)"
    rb"|(?P<cm>(?P<ca>-?[\d.]+)\s+(?P<cb>-?[\d.]+)\s+(?P<cc>-?[\d.]+)\s+"
    rb"(?P<cd>-?[\d.]+)\s+(?P<ce>-?[\d.]+)\s+(?P<cf>-?[\d.]+)\s+cm)"
    rb"|(?P<td>(?P<tdx>-?[\d.]+)\s+(?P<tdy>-?[\d.]+)\s+(?P<tdop>Td|TD))"
    rb"|(?P<tl>(?P<tlv>-?[\d.]+)\s+TL)"
    rb"|(?P<nl>T\*|'|\")"
    rb"|(?P<re>(?P<rx>-?[\d.]+)\s+(?P<ry>-?[\d.]+)\s+"
    rb"(?P<rw>-?[\d.]+)\s+(?P<rh>-?[\d.]+)\s+re)"
    rb"|(?P<gs>q|Q)"
    rb"|(?P<bt>BT|ET)"
    rb")(?=[\s/\[(<]|$)")

_STR_START = re.compile(rb"[(<]")


def _mask_strings(data: bytes) -> bytes:
    """*data* with the inside of every string literal blanked, same length.

    A PDF string can contain any bytes at all, so a run of text that happens
    to read "1 0 0 1 72 700 Tm" is a perfectly legal thing to draw — and
    scanning the raw stream for operators finds it and believes it. Blanking
    string contents while keeping every byte offset means the operator scan
    cannot be fooled, and offsets taken from the masked copy still splice
    correctly into the original.
    """
    out = bytearray(data)
    i, n = 0, len(data)
    while i < n:
        m = _STR_START.search(data, i)
        if m is None:
            break
        j = m.start()
        if data[j:j + 1] == b"<":
            k = data.find(b">", j + 1)
            if k < 0:
                break
            for t in range(j + 1, k):
                out[t] = 0x20
            i = k + 1
            continue
        depth, t = 1, j + 1
        while t < n and depth:
            ch = data[t]
            if ch == 0x5C:          # backslash: skip the escaped byte
                out[t] = 0x20
                if t + 1 < n:
                    out[t + 1] = 0x20
                t += 2
                continue
            if ch == 0x28:
                depth += 1
            elif ch == 0x29:
                depth -= 1
                if depth == 0:
                    break
            out[t] = 0x20
            t += 1
        i = t + 1
    return bytes(out)


def _mat_mul(m, n):
    """m x n, for the 6-number [a b c d e f] form PDF writes matrices in."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _text_objects(data: bytes):
    """Every text object from _stream_items, for callers that want only those."""
    for kind, item in _stream_items(data):
        if kind == "text":
            yield item


def _stream_rects(data: bytes):
    """Every `re` rectangle from _stream_items, in page coordinates.

    Reflow moves text; these are what the text is decorated WITH. A link
    underline is a filled rectangle, and moving the text off it leaves the
    rule stranded under whatever now occupies that space — on a Wikipedia
    page, a footnote marker moved 30pt right and its underline stayed put,
    ruling through the value that had taken its place. Registering text with
    its own decoration is exactly the kind of trace this engine exists to
    avoid leaving.
    """
    for kind, item in _stream_items(data):
        if kind == "rect":
            yield item


def _stream_items(data: bytes):
    """Walk *data* once, yielding ("text", obj) and ("rect", r) in PAGE
    coordinates, with the graphics state composed.

    One walk for both, because both need the same q/Q/cm bookkeeping and two
    copies of it would drift apart.

    TEXT OBJECTS carry:

      bt, et      byte offsets of the object's BT and ET
      x, y        the origin of its first text, in page space
      x_at        (start, end) byte span of that origin's x operand, so a
                  caller can move the object by rewriting one number
      x_scale     page units per unit of that operand, so a caller that wants
                  to move the object by dx in page space writes dx / x_scale
      after_pos   byte offset just past the first positioning operator, where
                  text-state operators may be inserted
      positions   [(x, y)] in page space for every position the pen is set
                  to, or None when that cannot be determined
      rigid       True when shifting that one operand moves the whole object
                  and nothing else

    RECTANGLES carry x0, y0, x1, y1 (page space, normalised), plus x_at and
    x_scale so one can be moved the same way, and w_at so one that belongs to
    the field itself can be RESIZED with it.

    WHY IT WALKS THE WHOLE OBJECT
    -----------------------------
    Reading only the object's first origin is not enough. A text object may
    set the pen many times — pdfTeX emits one object per paragraph with a Td
    before each line — so an object whose FIRST origin is on some other line
    can still draw on the line being edited. Missing that would let reflow
    move half of a line and leave the rest behind. Walking the operators in
    order gives every baseline the object touches, which is also more
    capable: PDF's Td is measured from the previous line's matrix rather than
    from the pen, so an object whose positions all sit on the edited line,
    past the field, can be moved rigidly by shifting only its first origin.

    WHY IT TRACKS THE CTM
    ---------------------
    A content stream need not be written in page coordinates. Headless Chrome
    wraps the page in "q .24 0 0 -.24 0 841.92 cm" and nests further scales
    inside it, so every operand in the stream is in a different space — and
    reading them as page coordinates found NOTHING on any baseline, which
    left reflow and the fitting levers with nothing to act on. Composing q/Q
    and cm puts the positions back into the space the caller measures in.
    """
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack = []
    cur = None
    for m in _POSOP_RE.finditer(_mask_strings(data)):
        if m.group("gs"):
            if m.group("gs") == b"q":
                stack.append(ctm)
            elif stack:
                ctm = stack.pop()
            continue
        if m.group("re"):
            if abs(ctm[1]) > 1e-6 or abs(ctm[2]) > 1e-6 or not ctm[0]:
                continue                     # rotated/degenerate: leave alone
            try:
                rx, ry = float(m.group("rx")), float(m.group("ry"))
                rw, rh = float(m.group("rw")), float(m.group("rh"))
            except ValueError:
                continue
            ax0 = rx * ctm[0] + ctm[4]
            ax1 = (rx + rw) * ctm[0] + ctm[4]
            ay0 = ry * ctm[3] + ctm[5]
            ay1 = (ry + rh) * ctm[3] + ctm[5]
            yield ("rect", {"x0": min(ax0, ax1), "x1": max(ax0, ax1),
                            "y0": min(ay0, ay1), "y1": max(ay0, ay1),
                            "x_at": m.span("rx"), "w_at": m.span("rw"),
                            "x_scale": ctm[0]})
            continue
        if m.group("cm"):
            try:
                mat = tuple(float(m.group(g)) for g in
                            ("ca", "cb", "cc", "cd", "ce", "cf"))
            except ValueError:
                continue
            ctm = _mat_mul(mat, ctm)
            continue
        if m.group("bt"):
            if m.group("bt") == b"BT":
                cur = {"bt": m.end(), "ctm": ctm, "lm": None, "leading": None,
                       "positions": [], "first": None, "rigid": True}
            elif cur is not None:
                obj, cur = cur, None
                if obj["first"] is None:
                    continue
                obj["first"].update(bt=obj["bt"], et=m.start("bt"),
                                    positions=obj["positions"],
                                    rigid=obj["rigid"])
                yield ("text", obj["first"])
            continue
        if cur is None:
            continue

        # A rotated or skewed CTM, or text matrix, is not this pass's
        # business: everything downstream reasons about x and y separately.
        c = cur["ctm"]
        if abs(c[1]) > 1e-6 or abs(c[2]) > 1e-6:
            cur["positions"] = None

        if m.group("tl"):
            try:
                cur["leading"] = float(m.group("tlv"))
            except ValueError:
                cur["leading"] = None
            continue

        if m.group("tm"):
            try:
                lm = tuple(float(m.group(g)) for g in
                           ("ma", "mb", "mc", "md", "tmx", "tmy"))
            except ValueError:
                cur["positions"] = None
                continue
            if abs(lm[1]) > 1e-6 or abs(lm[2]) > 1e-6:
                cur["positions"] = None
            if cur["first"] is None:
                cur["first"] = {"x_at": m.span("tmx"), "after_pos": m.end("tm"),
                                "x_scale": c[0]}
            else:
                # A second absolute origin: shifting the first one would not
                # move the text this one places, so the object cannot be
                # moved by rewriting a single number.
                cur["rigid"] = False
            cur["lm"] = lm
        elif m.group("td"):
            try:
                tx, ty = float(m.group("tdx")), float(m.group("tdy"))
            except ValueError:
                cur["positions"] = None
                continue
            if m.group("tdop") == b"TD":
                cur["leading"] = -ty
            if cur["lm"] is None:
                # The text matrix is the identity at BT, so a LEADING Td/TD
                # carries the origin itself. Word writes every run this way
                # and emits no Tm at all.
                cur["lm"] = (1.0, 0.0, 0.0, 1.0, tx, ty)
                cur["first"] = {"x_at": m.span("tdx"), "after_pos": m.end("td"),
                                "x_scale": c[0]}
            else:
                cur["lm"] = _mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), cur["lm"])
        else:                                    # T* / ' / "
            if cur["lm"] is None or cur["leading"] is None:
                cur["positions"] = None
                continue
            cur["lm"] = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -cur["leading"]),
                                 cur["lm"])

        lm = cur["lm"]
        px = lm[4] * c[0] + lm[5] * c[2] + c[4]
        py = lm[4] * c[1] + lm[5] * c[3] + c[5]
        if cur["first"] is not None and "x" not in cur["first"]:
            cur["first"]["x"], cur["first"]["y"] = px, py
        if cur["positions"] is not None:
            cur["positions"].append((px, py))


def _reflow_same_line(doc, page, base_ys, from_x: float, dx: float,
                      slots=(), rule_h: float = 0.0, field=None,
                      tol: float = 0.6):
    """Shift text that follows the edited field on the SAME line by *dx*.

    Replacing a field with longer text makes it run into whatever came after
    it — on the attestation fixture the name overran the comma that follows
    it by 66pt, because each run is positioned by its own ABSOLUTE origin and
    therefore does not move when the text before it grows.

    A word processor would push that comma along, and so does this: for every
    text object on the same LINE — every baseline in *base_ys*, since a
    superscript has a raised baseline of its own — whose origin is at or
    right of the edited field's original end, the x of its origin is
    increased by *dx*. Nothing
    else about those objects changes — same font, same size, same string, same
    y — so the gaps between them are preserved exactly.

    Refuses (returns None) rather than guessing whenever moving an object
    would move text that must not move: an object that draws both on this
    line and on another, or both before and after the field, can only be
    moved as a whole, because its later Td offsets are relative to its
    origin. It also refuses if ANY object on the page positions text in a way
    these operators cannot resolve — measured across four real documents that
    is 1 text object in 819, and the alternative is a silently torn line.

    The shift is given in PAGE points and converted into the object's own
    space, which differs whenever a cm is in force.

    Text is not the only thing that has to move. A link underline is a filled
    rectangle, and moving the text off it leaves the rule stranded under
    whatever takes that space — measured on a Wikipedia page, a footnote
    marker moved 30.4pt right and its underline did not, ruling straight
    through the value that replaced it. But most rectangles must NOT move:
    that same line crosses a column rule 655pt tall and sits on a row
    background 16.9pt tall, and shifting either would wreck the table.
    Thinness separates them with a wide margin — the underlines are 0.73pt —
    so *rule_h* is the tallest rectangle treated as a decoration, and it must
    sit in one of the *slots* — under a particular run of this line, between
    that run's baseline and the bottom of its box. One band for the whole
    line is not precise enough: a superscript stretches the line's band
    upward far enough to swallow the underline of the line ABOVE, which then
    gets moved, or reported for not moving.

    A decoration of the FIELD does not move — it resizes. *field* gives the
    field's own x0, x1, size and new_x1. A rule matching the field at both
    ends is its underline and takes the field's new width: shortening
    "contact@1337.ma" to "contact" without narrowing it left a blue rule
    trailing 46pt into empty space. A rule covering only PART of the field —
    a link inside a longer run, which is 4 of 8 sampled fields on a Wikipedia
    page — cannot be resized faithfully, because which characters it belongs
    to is not recoverable; it is clamped so that it never extends past the
    field's text, which is the property real documents have. Measured across
    129 thin rules in four untouched documents, the worst overhang past the
    text above was 0.37pt.

    Returns the number of items shifted, or None if reflow isn't safe here.
    """
    if abs(dx) < 0.01:
        return 0
    edits, shifted = [], 0
    for st in _content_streams(doc, page):
        data = st["data"]
        hits = []
        for obj in _text_objects(data):
            pos = obj["positions"]
            if pos is None:
                # Where this object draws cannot be worked out from its
                # operators (a T* with no leading ever set, or a rotated
                # matrix). Its origin being on some other line proves
                # nothing — it could still draw on the edited one — so there
                # is no sound way to leave it out. Refuse the whole reflow;
                # the caller turns that into an honest refusal rather than a
                # half-moved line.
                return None
            on_line = [q for q in pos
                       if any(abs(q[1] - by) <= tol for by in base_ys)]
            if not on_line:
                continue                       # nowhere on the edited line
            after = [q for q in on_line if q[0] >= from_x - tol]
            if not after:
                continue                       # entirely left of the field
            if len(after) != len(pos):
                # Either the object also draws left of the field, or it also
                # draws on another line. Its later Td offsets are RELATIVE to
                # its origin, so it can only be moved as a whole — and moving
                # it as a whole would move text that must not move.
                return None
            if not obj["rigid"] or not obj["x_scale"]:
                # A second absolute origin inside the object, so rewriting
                # the first one would leave the rest of it behind; or a
                # degenerate transform there is no way to write through.
                return None
            hits.append(obj)
        # Every operand to rewrite, as (byte span, how much to add to it).
        # The operand lives in the object's own space, which is not the
        # page's when a cm is in force: a Chrome-printed page is scaled by
        # .24 and then by 3.06, so a 20pt shift on the page is a 27pt change
        # to the number in the stream.
        writes = [(o["x_at"], dx / o["x_scale"]) for o in hits]
        if slots and rule_h > 0.0:
            fx0, fx1, fsize, fnew = field if field else (0.0, 0.0, 0.0, 0.0)
            edge = 0.4 * fsize
            for r in _stream_rects(data):
                if r["y1"] - r["y0"] > rule_h or not r["x_scale"]:
                    continue                 # a background or a border
                if not any(sx0 - 1.0 <= r["x0"] and r["x1"] <= sx1 + 1.0
                           and r["y1"] <= sy_base + 0.5 and r["y0"] >= sy_low - 1.5
                           for sx0, sx1, sy_base, sy_low in slots):
                    continue                 # not a decoration of this line
                if field and abs(r["x0"] - fx0) <= edge and abs(r["x1"] - fx1) <= edge:
                    # The field's own underline: it keeps its left edge and
                    # takes the field's new width. That is the FIELD's growth,
                    # not the amount the rest of the line is pushed by — the
                    # two differ whenever slack was absorbed.
                    grow = (fnew - fx1) / r["x_scale"]
                    if float(data[slice(*r["w_at"])]) + grow > 0:
                        writes.append((r["w_at"], grow))
                    continue
                if field and fx0 - 1.0 <= r["x0"] and r["x1"] <= fx1 + 1.0:
                    # Covers only part of the field. Clamp it so it cannot
                    # end up ruling empty space, and otherwise leave it be.
                    if r["x1"] > fnew + 0.5:
                        cut = (fnew - r["x1"]) / r["x_scale"]
                        w = float(data[slice(*r["w_at"])])
                        writes.append((r["w_at"], max(cut, -w)))
                    continue
                if r["x0"] < from_x - tol:
                    # Starts before the field but is not its underline, so it
                    # spans across; moving it would drag its left end too.
                    continue
                writes.append((r["x_at"], dx / r["x_scale"]))
        if not writes:
            continue
        out = bytearray()
        last = 0
        for (a, b), delta in sorted(writes):
            out += data[last:a] + f"{float(data[a:b]) + delta:.4f}".encode("latin-1")
            last = b
        out += data[last:]
        shifted += len(writes)
        edits.append((st["xref"], bytes(out)))
    for xref, payload in edits:
        doc.update_stream(xref, payload)
    return shifted


# How much width may be reclaimed before an edit is refused as not fitting,
# and in what order. These are the three elastic levers a typesetter uses to
# justify a line, applied here in order of how invisible each one is, and each
# bounded by what professional justification settings actually allow:
#
#   1. WORD SPACING (Tw). The most elastic thing on a line — inter-word space
#      is what justification stretches and squeezes first, and InDesign's
#      default minimum is 80% of normal. Only usable on simple fonts: the PDF
#      spec applies Tw to single-byte code 32, so it does nothing for a
#      2-byte CID encoding.
#   2. TRACKING (Tc). A fiftieth of an em is invisible in running text.
#   3. GLYPH SCALING (Tz). Condenses the letterforms themselves, so it goes
#      last; 97% is a common professional bound for justification.
#
# Font SIZE is deliberately not on this list. Changing it is a visible change
# of identity, which is the thing this whole engine exists to avoid, so when
# the three levers together are not enough the edit is refused with the exact
# shortfall rather than silently resized.
# Sentinel: the edited run could only be read back truncated, which is
# itself proof that it left the page.
_TRUNCATED = object()

# Bounds on the search for the right (font object, encoding, occurrence).
# The ordinary case resolves on the first attempt; these only stop a
# pathological page from turning one edit into hundreds of parses.
_MAX_OCCURRENCE_TRIES = 6
_MAX_LOCATE_ATTEMPTS = 24

# How informative each refusal is, highest first. The search tries several
# (font object, encoding, occurrence) combinations, and most of them fail
# simply because they are the wrong combination — "sequence_not_found" from a
# wrong guess says nothing. When one attempt reaches a real structural limit,
# THAT is what the caller should be told. Reporting the first refusal instead
# told users "unusual encoding" about a field whose actual problem was custom
# letter-spacing, because the wrong-encoding attempt happened to run first.
_REFUSAL_RANK = {
    "kerning_split_within_run": 90,
    "ragged_multirun_boundary": 85,
    "spans_multiple_streams": 80,
    # Both of these name a specific, checked structural fact about THIS span,
    # so they outrank the generic width verdict.
    "spans_multiple_fonts": 79,
    "cannot_reflow": 79,
    "would_overflow": 78,
    # More specific than missing_glyph, and actionable: the character could be
    # drawn, just not in these letterforms.
    "donor_too_different": 76,
    "missing_glyph": 75,
    "extend": 70,
    "unmappable": 60,
    "encoding": 55,
    "no_content_stream": 50,
    "no_runs_for_font": 10,
    "sequence_not_found": 5,
}


def _better_refusal(a, b):
    """Whichever of two refusal dicts says more about why. Either may be None."""
    if a is None:
        return b
    if b is None:
        return a
    return b if _REFUSAL_RANK.get(b.get("reason"), 0) > \
        _REFUSAL_RANK.get(a.get("reason"), 0) else a

MAX_WORDSPACE_SHRINK = 0.20
MAX_TRACK_EM = 0.02
MIN_GLYPH_SCALE = 0.97


def _text_right_limit(page, exclude_bbox=None) -> float:
    """The x past which an edit must not push text on this page.

    Two candidates, and the LARGER wins:

      - where the page's own text actually ends, which is where the producer's
        right margin is. The edited field itself is excluded, or a field that
        happens to be the rightmost thing on the page becomes its own limit
        and any growth at all reads as an overflow — which is exactly what
        happened on the small single-line fixtures, refused at "x=216 past the
        margin at x=216".
      - the page width less its left margin, taken from where text starts on
        the left. This is what makes a one-line document work at all: with
        nothing else on the page to measure against, the paper is the only
        real constraint.

    Running off the PAGE is unambiguously wrong; running past the text block
    but still on the paper is a judgement call, so the rule refuses only the
    former.
    """
    right, left = 0.0, None
    for blk in page.get_text("dict")["blocks"]:
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                bb = span["bbox"]
                if exclude_bbox is not None and abs(bb[0] - exclude_bbox[0]) < 0.6 \
                        and abs(bb[1] - exclude_bbox[1]) < 0.6:
                    continue
                right = max(right, bb[2])
                left = bb[0] if left is None else min(left, bb[0])
    page_limit = page.rect.width - min(left if left is not None else 0.0, 72.0)
    return max(right, page_limit)


def _line_spans(page, target_bbox):
    """Every text span on the same visual LINE as the field.

    Sharing a baseline is too narrow a test. A superscript sits on a raised
    baseline of its own yet plainly belongs to the line: on a Wikipedia page
    the footnote marker after "the Netherlands." is 0.40 em above it, and
    leaving it out of the line meant reflow moved the sentence after it and
    left the marker behind — the lengthened value was then drawn straight
    through it. Widening the baseline tolerance instead is guesswork, because
    the next line down is only 1.33 em away.

    What separates them is how MUCH of a run's height overlaps the field's,
    not whether any of it does. Measured as a fraction of the shorter of the
    two boxes:

        the rest of the sentence, same baseline        1.00
        a second column, 0.04 em below                 1.00
        a superscript marker, 0.40 em above            0.75
        the next line down, 1.06 em away               0.00  (0.03pt)

    A bare overlap test put that last one on the line and reflow moved it,
    breaking the rule that no other line may move. Half is comfortably
    between 0.75 and 0.00.

    PyMuPDF's own line grouping is not usable for this. On the attestation
    fixture it puts ' à ', ', ' and 'Essaouira' in DIFFERENT lines from the
    date they sit beside, so trusting it would leave the whole line behind.
    """
    box = fitz.Rect(target_bbox)
    out = []
    for blk in page.get_text("dict")["blocks"]:
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                sb = span["bbox"]
                ov = min(sb[3], box.y1) - max(sb[1], box.y0)
                ref = min(sb[3] - sb[1], box.y1 - box.y0)
                if ref <= 0 or ov < 0.5 * ref:
                    continue
                out.append(span)
    return out


def _line_baselines(page, target_bbox, page_h: float):
    """The distinct baselines, in page coordinates, that this line draws on."""
    ys = {round(page_h - sp_["origin"][1], 2)
          for sp_ in _line_spans(page, target_bbox)}
    return sorted(ys)


def _next_text_x0(page, target_bbox):
    """Leftmost x0 of the text that FOLLOWS the field on its own line, or None.

    "Follows" is judged from where each run starts, not from where the field
    ends, because those are not the same thing. On the attestation fixture the
    comma after the birthplace is positioned at x=247.97 while the birthplace
    value itself extends to x=262.0 — the comma is drawn over the tail of the
    value, in the original document, before any edit. Measuring from the
    field's END therefore concluded that nothing followed it and left the
    comma behind, and lengthening the value buried it: 13.98pt of pre-existing
    overlap became 57.12pt.

    Conversely the line's right EXTENT is not the answer either. Using it
    treated any line with text somewhere to the right as an obstruction, and
    refused 18 otherwise-good edits on a LaTeX paper whose following text was
    far enough away that the longer value never reached it.
    """
    x0 = fitz.Rect(target_bbox).x0
    best = None
    for span in _line_spans(page, target_bbox):
        if span["bbox"][0] <= x0 + 0.5:
            continue              # the field itself, or text before it
        best = span["bbox"][0] if best is None else min(best, span["bbox"][0])
    return best


def _pinned_at(positions, x: float, tol: float = 1.5) -> bool:
    """Is the text at *x* held there by one of these positioning operators?

    Takes the list from _positions_on_baseline so the engine and its tests
    judge this the same way, off one scan.

    This is the difference between text that must be moved out of the way and
    text that moves itself. Two runs can sit side by side on a line for two
    quite different reasons:

      * one text object shows them in the same string, or in one TJ array
        with a small kern between them — the second run's place comes from
        the glyph advances of the first, so lengthening the first CARRIES the
        second along, and there is nothing to move;
      * or the second run has its own Tm/Td — an absolute place on the page,
        or an offset from the object's origin, and PDF's Td is measured from
        the previous LINE's matrix rather than from the pen, so glyph
        advances never affect it. That run stays exactly where it is however
        much the text before it grows, and it has to be shifted explicitly.

    Assuming the second case for every neighbour refused 11 good edits on a
    LaTeX paper whose following sentence was in the same TJ array and simply
    flowed. Assuming the first case buried the comma after a Word field.

    An EMPTY list is a third case and not this function's to judge: it means
    the line's geometry could not be read at all, and the caller has to be
    conservative rather than conclude that nothing is pinned.
    """
    return any(abs(px - x) <= tol for px in positions)


def _positions_on_baseline(doc, page, base_ys, tol: float = 0.6):
    """Every x at which a positioning operator places text on this LINE.

    *base_ys* is every baseline the line draws on, in page coordinates — a
    line with a superscript in it has more than one.

    Empty means the engine cannot see this line's geometry at all, which is
    not the same as the line having no independently-placed runs on it. A
    page's content stream need not be written in page coordinates: headless
    Chrome wraps the whole page in ".24 0 0 -.24 0 841.92 cm" and nests
    further scales inside it, so nothing in the stream matches a coordinate
    read back from the page. Callers must treat "nothing here" as "cannot
    tell", and be conservative.
    """
    out = []
    for st in _content_streams(doc, page):
        for obj in _text_objects(st["data"]):
            for px, py in (obj["positions"] or ()):
                if any(abs(py - by) <= tol for by in base_ys):
                    out.append(px)
    return out


def _apply_text_state(doc, page, base_y_pdf: float, at_x: float,
                      tc: float = 0.0, tw: float = 0.0, tz: float = 100.0,
                      tol: float = 0.6) -> bool:
    """Set character spacing, word spacing and horizontal scale on the one
    text object at (at_x, base_y).

    Tc, Tw and Tz are ordinary PDF text-state operators, so this changes how
    the existing run is laid out without touching the glyphs, the font or the
    size. All three are reset before the block's ET, so none can leak into
    text drawn afterwards even where runs are not wrapped in q/Q.
    """
    if abs(tc) < 1e-9 and abs(tw) < 1e-9 and abs(tz - 100.0) < 1e-9:
        return True
    for st in _content_streams(doc, page):
        data = st["data"]
        for obj in _text_objects(data):
            if abs(obj["y"] - base_y_pdf) > tol or abs(obj["x"] - at_x) > tol:
                continue
            pos = obj["positions"]
            if pos is None or any(abs(q[1] - base_y_pdf) > tol for q in pos):
                # Tc/Tw/Tz stay in force to the end of the text object, so
                # setting them on an object that goes on to draw other lines
                # would re-space those lines too. Decline; the caller turns
                # that into an honest refusal rather than a silent overrun.
                continue
            at, et = obj["after_pos"], obj["et"]
            setup, reset = [], []
            if abs(tc) > 1e-9:
                setup.append(f"{tc:.4f} Tc")
                reset.append("0 Tc")
            if abs(tw) > 1e-9:
                setup.append(f"{tw:.4f} Tw")
                reset.append("0 Tw")
            if abs(tz - 100.0) > 1e-9:
                setup.append(f"{tz:.3f} Tz")
                reset.append("100 Tz")
            payload = (data[:at] + (" " + " ".join(setup)).encode("latin-1")
                       + data[at:et] + (" " + " ".join(reset) + " ").encode("latin-1")
                       + data[et:])
            doc.update_stream(st["xref"], payload)
            return True
    return False


def _fit_plan(base_w: float, avail: float, n_chars: int, n_spaces: int,
              space_w: float, size: float, allow_wordspace: bool) -> dict:
    """How to squeeze `base_w` into `avail`, or why it can't be done.

    Spends the elastic levers in order of invisibility (word spacing, then
    tracking, then glyph scaling), each within its own bound, and reports what
    is left over. Returns {"fits", "tc", "tw", "tz", "need", "recovered",
    "short"} — all in points, so a refusal can state the real shortfall
    instead of just declining.
    """
    need = base_w - avail
    if need <= 0.0:
        return {"fits": True, "tc": 0.0, "tw": 0.0, "tz": 100.0,
                "need": 0.0, "recovered": 0.0, "short": 0.0}

    remaining = need
    tw = 0.0
    if allow_wordspace and n_spaces > 0 and space_w > 0:
        tw = -min(remaining / n_spaces, MAX_WORDSPACE_SHRINK * space_w)
        remaining -= (-tw) * n_spaces

    tc = 0.0
    if remaining > 0 and n_chars > 0:
        tc = -min(remaining / n_chars, MAX_TRACK_EM * size)
        remaining -= (-tc) * n_chars

    tz = 100.0
    width_now = base_w + tc * n_chars + tw * n_spaces
    if remaining > 0 and width_now > 0:
        ratio = max(MIN_GLYPH_SCALE, (width_now - remaining) / width_now)
        tz = ratio * 100.0
        remaining -= width_now * (1.0 - ratio)

    return {"fits": remaining <= 0.05, "tc": tc, "tw": tw, "tz": tz,
            "need": need, "recovered": need - max(remaining, 0.0),
            "short": max(remaining, 0.0)}


def _parse_widths_array(doc, refs):
    """(first_char, [w1000, ...]) from a simple font's /Widths, or None."""
    first, last = refs.get("first_char"), refs.get("last_char")
    if first is None or last is None:
        return None
    kind, val = refs.get("widths_kind"), refs.get("widths_val")
    if kind == "array" and val:
        text = val
    elif kind == "xref" and val:
        m = re.match(r"(\d+)\s+0\s+R", val)
        if not m:
            return None
        text = doc.xref_object(int(m.group(1)), compressed=True)
    else:
        return None
    widths = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if len(widths) != (last - first + 1):
        return None
    return first, widths


def _cid_widths_map(doc, cid_xref) -> dict:
    """{cid: width} from a CIDFont's /W array, which comes in two shapes:
    `c [w1 w2 …]` (consecutive) and `c_first c_last w` (a run at one width)."""
    kind, val = doc.xref_get_key(cid_xref, "W")
    if kind == "array" and val:
        text = val
    elif kind == "xref" and val:
        m = re.match(r"(\d+)\s+0\s+R", val)
        if not m:
            return {}
        text = doc.xref_object(int(m.group(1)), compressed=True)
    else:
        return {}
    toks = re.findall(r"\[|\]|-?\d+(?:\.\d+)?", text)
    out, i = {}, 0
    while i < len(toks):
        if toks[i] == "[":
            i += 1
            continue
        if toks[i] == "]":
            i += 1
            continue
        try:
            c = int(float(toks[i]))
        except ValueError:
            i += 1
            continue
        if i + 1 < len(toks) and toks[i + 1] == "[":
            j = i + 2
            cid = c
            while j < len(toks) and toks[j] != "]":
                out[cid] = float(toks[j])
                cid += 1
                j += 1
            i = j + 1
        elif i + 2 < len(toks):
            try:
                c2, w = int(float(toks[i + 1])), float(toks[i + 2])
                for cid in range(c, min(c2, c + 65535) + 1):
                    out[cid] = w
            except ValueError:
                pass
            i += 3
        else:
            break
    return out


def _run_width_pt(doc, font_display_name, codes, size, is_cid) -> float:
    """Width of `codes` in points, from the PDF's OWN advance widths.

    Computed rather than measured back out of the edited page, because a run
    that overflows the page is exactly the case that needs measuring and
    exactly the case extraction cannot report: glyphs drawn past the media box
    are not extracted at all, so the text comes back silently truncated (a
    76-character name extracted as 73 and looked like it fit). /Widths is
    also what the viewer itself uses to advance the pen, so this is the
    number that actually decides the layout.

    Returns -1.0 when the width table cannot be read, so the caller can refuse
    rather than proceed on a guess.
    """
    if is_cid:
        type0 = None
        for pno in range(doc.page_count):
            for f in doc[pno].get_fonts(full=True):
                if f[3].split("+")[-1] == font_display_name:
                    type0 = f[0]
                    break
            if type0:
                break
        if type0 is None:
            return -1.0
        refs = _font_stream_refs(doc, type0)
        if not refs or refs == "cff":
            return -1.0
        wmap = _cid_widths_map(doc, refs["cid_xref"])
        if not wmap:
            return -1.0
        kind, dw = doc.xref_get_key(refs["cid_xref"], "DW")
        default = float(dw) if kind == "int" and dw else 1000.0
        total = sum(wmap.get(c, default) for c in codes)
    else:
        refs = _simple_font_refs(doc, font_display_name, require_truetype=False)
        if not refs:
            return -1.0
        parsed = _parse_widths_array(doc, refs)
        if not parsed:
            return -1.0
        first, widths = parsed
        total = 0.0
        for c in codes:
            idx = c - first
            if not (0 <= idx < len(widths)):
                return -1.0
            total += widths[idx]
    return total * size / 1000.0


def _finish_prepare(doc, tpage, nm, is_cid, old_codes, new_codes,
                    extended_chars, which):
    """Tokenise the page and locate the splice for already-encoded codes."""
    page = doc[tpage]
    streams = _content_streams(doc, page)
    if not streams:
        return {"ok": False, "reason": "no_content_stream", "message": _REASON_MSG["no_content_stream"]}
    runs = _all_runs(streams)
    refmap = _page_font_refmap(page)

    loc_result = _locate(runs, refmap, nm, old_codes, is_cid, which=which,
                         refsub=_page_font_subtypes(page))
    if not loc_result["ok"]:
        reason = loc_result["reason"]
        return {"ok": False, "reason": reason, "message": _REASON_MSG.get(reason, reason)}

    return {"ok": True, "old_codes": old_codes, "new_codes": new_codes,
            "extended_chars": extended_chars, "runs": runs, "refmap": refmap,
            "loc": loc_result}


def _prepare_edit(doc, tpage, nm, is_cid, old_n, new, which=None):
    """Encode old/new for one assumed encoding, extend the font if needed, and
    locate the splice — all on `doc`, which this MUTATES on success.

    Split out of edit() so the same work can be attempted under each subtype a
    display name actually has, on a fresh copy of the document each time. On
    success returns {"ok": True, old_codes, new_codes, extended_chars, runs,
    refmap, loc}; on failure the refusal dict, with `doc` left for the caller
    to discard.
    """
    extended_chars: list = []
    if is_cid:
        # This font OBJECT'S own /ToUnicode first. The map from get_texttrace
        # is keyed by the extractor's normalised font name, which can cover
        # two different objects — mixing one's TrueType glyph ids with the
        # other's CIDs so that neither font's codes come out whole. Reading
        # the object's own CMap keeps them apart.
        cidmap = _lookup_by_name(_cid_code_maps(doc), nm)
        if cidmap:
            _old = _encode_simple_text(old_n, cidmap["rev"], cidmap["max_len"])
            _new = _encode_simple_text(new, cidmap["rev"], cidmap["max_len"])
            # /ToUnicode can name a CID whose glyph the font program does not
            # actually contain, so the program itself is asked before this
            # shortcut is taken. Skipping that check produced text extracting
            # as 'Fécture' with no ink at all where the 'é' should be.
            if _old is not None and _new is not None and \
                    _cid_glyph_coverage(doc, nm, _new) is not False:
                return _finish_prepare(doc, tpage, nm, is_cid, _old, _new, [], which)
        fm = _lookup_by_name(_gid_maps(doc), nm) or {}
        missing = sorted({ch for ch in new if not ch.isspace() and _norm(ch) not in fm})
        if missing:
            new_gids, fail_reason = _try_extend(doc, nm, missing)
            if new_gids is None:
                why = _EXTEND_FAIL_MSG.get(fail_reason, "Couldn't add the missing glyph(s).")
                return {"ok": False, "reason": "extend", "tier": "extend", "missing": missing,
                        "extend_reason": fail_reason,
                        "message": (f"This field's font is an embedded subset that doesn't contain "
                                    f"these characters yet: {missing}. {why}")}
            fm = {**fm, **{_norm(ch): g for ch, g in new_gids.items()}}
            extended_chars = missing
        old_codes = [fm.get(_norm(ch)) for ch in old_n]
        new_codes = [fm.get(_norm(ch)) for ch in new]
        if any(c is None for c in old_codes) or any(c is None for c in new_codes):
            return {"ok": False, "reason": "unmappable", "message": _REASON_MSG["unmappable"]}
    else:
        cm = _lookup_by_name(_simple_font_code_maps(doc), nm)
        enc_name = _lookup_by_name(_simple_font_encodings(doc), nm)
        old_codes = _encode_simple_text(old_n, cm["rev"], cm["max_len"]) if cm else None
        new_codes = _encode_simple_text(new, cm["rev"], cm["max_len"]) if cm else None
        if old_codes is None:
            old_codes = _encode_fallback(old_n, enc_name)
            if old_codes is None:
                return {"ok": False, "reason": "encoding", "message": _REASON_MSG["encoding"]}
        if new_codes is None:
            new_codes = _encode_fallback(new, enc_name)
            if new_codes is None:
                return {"ok": False, "reason": "encoding", "message": _REASON_MSG["encoding"]}
            # _encode_fallback only proves the WinAnsi/MacRoman *encoding*
            # table has a byte for each character — not that this embedded
            # subset's font program actually has a glyph outline there (see
            # _simple_font_glyph_coverage's docstring). _encode_simple_text
            # above doesn't need this: it's built from the doc's own
            # /ToUnicode, so a hit there is already something the doc
            # genuinely renders.
            coverage = _simple_font_glyph_coverage(doc, nm)
            if coverage is not None:
                missing = sorted({ch for ch in new if not ch.isspace() and ch not in coverage})
                if missing:
                    # Try to give the ORIGINAL font the glyphs it lacks rather
                    # than refusing. Refusing here is not neutral: the caller
                    # falls through to the redraw engine, which repaints the
                    # background, stamps the text from a newly embedded font
                    # and leaves the document carrying a font resource its
                    # producer never wrote. Extending in place changes nothing
                    # on the page except the characters that were edited.
                    # Hand over the codes the encoder actually produced, so
                    # /Widths and /ToUnicode are keyed by character code
                    # rather than by Unicode codepoint.
                    code_for = {}
                    for ch, code in zip(new, new_codes or []):
                        code_for.setdefault(ch, code)
                    new_gids, fail_reason = _try_extend_simple(doc, nm, missing,
                                                               code_for=code_for)
                    if new_gids is None:
                        why = _SIMPLE_EXTEND_FAIL_MSG.get(
                            fail_reason, "Couldn't add the missing glyph(s).")
                        return {"ok": False, "reason": "missing_glyph",
                                "missing": missing, "extend_reason": fail_reason,
                                "message": (f"This field's font is an embedded subset that "
                                            f"doesn't contain these characters yet: "
                                            f"{missing}. {why}")}
                    extended_chars = missing

    return _finish_prepare(doc, tpage, nm, is_cid, old_codes, new_codes,
                           extended_chars, which)


def _spans_multiple_fonts(doc, tpage, old_n, old_codes_by_cid) -> bool:
    """Is this text drawn using MORE THAN ONE font resource?

    Then no single-font search can ever find it, and "couldn't locate the
    glyph sequence" is a poor description of why. Office does this routinely:
    when a run needs a character its font's subset lacks, it draws that
    fragment with a different font object and carries on. On the attestation
    document, "La Direction de l'école 1337 …" is drawn almost entirely by
    one TwCenMT-Regular object, with the two fragments containing a right
    single quote drawn by ANOTHER object of the same display name — so the
    first font's stream reads "La Direction de  1337, …", with a gap exactly
    where the other font's text belongs.

    Detected by finding the longest prefix of the target that any single font
    does contain: a substantial prefix that stops short means the text starts
    in one font and continues in another.
    """
    page = doc[tpage]
    refmap = _page_font_refmap(page)
    refsub = _page_font_subtypes(page)
    runs = _all_runs(_content_streams(doc, page))
    best = 0
    for name in dict.fromkeys(refmap.values()):
        for is_cid in (False, True):
            codes = old_codes_by_cid.get((name, is_cid))
            if not codes:
                continue
            flat, _ = _flatten(runs, name, refmap, is_cid, refsub=refsub)
            if not flat:
                continue
            lo, hi = 1, len(codes)
            while lo <= hi:
                mid = (lo + hi) // 2
                if _find_all(flat, codes[:mid]):
                    best = max(best, mid)
                    lo = mid + 1
                else:
                    hi = mid - 1
    return 3 <= best < len(old_n)


def edit(pdf_bytes: bytes, old: str, new: str, page: int = None, bbox=None, verify: bool = True) -> dict:
    """Attempt a true in-place swap of `old`->`new`. Returns a verdict and, when
    it succeeds, the edited PDF (base64) + a pixel-diff proof.

    `page` (optional): restrict the search to this page only. Without it,
    duplicate occurrences of the exact same text elsewhere in the document
    are ambiguous — the /lab tool never hits this (a human picks one field
    at a time), but a caller driving this from an index-addressed span list
    (e.g. the production /edit route) already KNOWS exactly which field it
    means and should say so.
    `bbox` (optional, most useful together with `page`): when more than one
    span on that page contains `old` (e.g. a repeated label), prefer the one
    whose bbox is closest to this one instead of just the first found — the
    same disambiguation the caller's own span list already carries.
    Passing neither preserves the exact prior first-match-in-document-order
    behavior (still the deliberate, honest choice for genuinely ambiguous
    duplicate text — see the "known limits" note in spikes/README.md).

    `verify` (default True): rasterize before/after to prove nothing outside
    the field changed (`diff_outside`/`guarantee`). This is a genuine (if
    already thoroughly regression-tested) proof step, not something the
    edit's own success depends on — `ok` is decided purely by whether
    `_locate` found a safe splice, before this ever runs. A caller applying
    MANY edits to the same document in a loop (production's /edit route) can
    pass False to skip two full-page rasterizations per field; `diff_outside`/
    `diff_inside`/`guarantee` come back as None in that case."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    subtype, used = _font_info(doc)
    old_n = _norm(old)
    page_range = [page] if page is not None else range(doc.page_count)
    candidates = []
    for pno in page_range:
        if pno < 0 or pno >= doc.page_count:
            continue
        for s in _spans(doc[pno]):
            if old_n in _norm(s["text"]):
                candidates.append((pno, s))
    if not candidates:
        doc.close()
        return {"ok": False, "reason": "not_found", "message": _REASON_MSG["not_found"]}
    if bbox is not None and len(candidates) > 1:
        bx = fitz.Rect(bbox)

        def _dist(item):
            sb = fitz.Rect(item[1]["bbox"])
            return (abs(sb.x0 - bx.x0) + abs(sb.y0 - bx.y0)
                   + abs(sb.x1 - bx.x1) + abs(sb.y1 - bx.y1))

        candidates.sort(key=_dist)
    tpage, target = candidates[0]

    nm = target["font"].split("+")[-1]
    # Which encoding this name really uses. A display name is not unique — the
    # attestation fixture embeds 'TwCenMT-Regular' as BOTH a TrueType font and
    # a Type0 CID font — so rather than trust a single name->subtype mapping
    # (which kept whichever font object came last, and sent every span drawn
    # with the other one down the wrong path), every subtype the name actually
    # has is tried and the one that can locate the text is kept. A wrong guess
    # cannot accidentally succeed: it decodes 2-byte CID codes as single bytes
    # or the reverse, and matches nothing.
    _subs = set()
    for _pno in range(doc.page_count):
        for _f in doc[_pno].get_fonts(full=True):
            if _f[3].split("+")[-1] == nm:
                _subs.add(_f[2])
    cid_options = [t == "Type0" for t in sorted(_subs)] or [subtype.get(nm) == "Type0"]
    cid_options = sorted(set(cid_options), reverse=True)   # try CID first
    page = doc[tpage]

    # WHICH font object, WHICH encoding, and WHICH occurrence to splice.
    #
    # None of the three can be read off the span. A span's reported font name
    # does not identify a font object: this fixture draws '12/05/2001' and
    # 'Essaouira' with a Type0 font whose BaseFont is 'Tw Cen MT Bold', and
    # the extractor reports their font as 'TwCenMT-Bold' — the name of a
    # DIFFERENT, TrueType object on the same page. Searching only the runs of
    # the name the span reported therefore found nothing at all and refused
    # with sequence_not_found, which is why five of eight sampled fields on
    # this document could not be edited in place. The same name can also
    # cover two objects of different subtype, and the same string can appear
    # several times in one font.
    #
    # So every plausible combination is TRIED and the result CHECKED: does the
    # span the caller pointed at now read as the replacement? Only a splice
    # into the right run can make that true, so verification — not a guess
    # about names, encodings or positions — decides. The span's own name and
    # subtype are tried first, so the ordinary case costs exactly one attempt.
    target_rect = fitz.Rect(target["bbox"])

    def _hits_target(pdf_bytes_):
        try:
            probe = fitz.open(stream=pdf_bytes_, filetype="pdf")
        except Exception:  # noqa: BLE001
            return False
        try:
            for s2 in _spans(probe[tpage]):
                if abs(s2["origin"][1] - target["origin"][1]) > 1.0:
                    continue
                if fitz.Rect(s2["bbox"]).intersects(target_rect) or \
                        abs(s2["bbox"][0] - target_rect.x0) < 2.0:
                    # Must show the replacement's BEGINNING and no longer read
                    # as the original.
                    #
                    # A prefix, not the whole string: a run that overflows the
                    # page extracts truncated, so demanding the full text made
                    # the correct candidate fail verification — the edit was
                    # then refused as sequence_not_found instead of
                    # would_overflow, and everything downstream that keeps
                    # over-long text on the page stopped firing.
                    #
                    # And it must DIFFER from the original, because testing
                    # only for the replacement is satisfied by an untouched
                    # span whenever the new text is a substring of the old —
                    # 'models' -> 'mod' looked like a hit on a span that had
                    # not changed at all.
                    txt = _norm(s2["text"])
                    head = _norm(new)[:10]
                    if head and head in txt and txt != _norm(old_n):
                        return True
            return False
        finally:
            probe.close()

    page_names = []
    for _f in doc[tpage].get_fonts(full=True):
        _n = _f[3].split("+")[-1]
        if _n not in page_names:
            page_names.append(_n)
    name_order = ([nm] if nm in page_names else []) + [n for n in page_names if n != nm]
    if not name_order:
        name_order = [nm]

    def _subtypes_of(name):
        out = []
        for _pno in range(doc.page_count):
            for _f in doc[_pno].get_fonts(full=True):
                if _f[3].split("+")[-1] == name and _f[2] not in out:
                    out.append(_f[2])
        return out or ["TrueType"]

    prep = prep_error = prep_error_other = None
    chosen_bytes = None
    attempts = 0
    for cand_nm in name_order:
        cid_opts = sorted({t == "Type0" for t in _subtypes_of(cand_nm)}, reverse=True)
        if cand_nm == nm:
            cid_opts = sorted(cid_opts, key=lambda c: c != (subtype.get(nm) == "Type0"))
        for cand_cid in cid_opts:
            for which in range(_MAX_OCCURRENCE_TRIES):
                if attempts >= _MAX_LOCATE_ATTEMPTS:
                    break
                attempts += 1
                adoc = fitz.open(stream=pdf_bytes, filetype="pdf")
                try:
                    res = _prepare_edit(adoc, tpage, cand_nm, cand_cid, old_n, new,
                                        which=which)
                except Exception:  # noqa: BLE001 — a bad guess must not lose a good one
                    adoc.close()
                    break
                if not res.get("ok"):
                    adoc.close()
                    # Keep the most informative refusal, but keep the span's
                    # OWN font separate from the others. Ranking across all
                    # candidates equally reported a glyph-coverage problem
                    # belonging to an unrelated font that merely happens to
                    # sit on the same page — for a field drawn in a different
                    # typeface entirely. A refusal is only an explanation of
                    # the field if it came from the field's own font.
                    if cand_nm == nm:
                        prep_error = _better_refusal(prep_error, res)
                    else:
                        prep_error_other = _better_refusal(prep_error_other, res)
                    break
                if which >= res["loc"].get("n_occurrences", 1):
                    adoc.close()
                    break
                _apply(adoc, res["runs"], res["loc"], res["new_codes"], cand_cid)
                cand_bytes = adoc.tobytes(garbage=0)
                adoc.close()
                if _hits_target(cand_bytes):
                    prep, chosen_bytes = res, cand_bytes
                    nm, is_cid = cand_nm, cand_cid
                    break
            if prep is not None:
                break
        if prep is not None:
            break

    if prep is None:
        # Before settling for "couldn't find it", check whether the text is
        # simply drawn in more than one font — a different situation with a
        # different answer, and one this engine does not handle.
        multi = False
        if (prep_error or prep_error_other or {}).get("reason", "sequence_not_found") \
                == "sequence_not_found":
            try:
                by_cid = {}
                for cand_nm in name_order:
                    for cand_cid in (False, True):
                        if cand_cid:
                            cmap_ = _lookup_by_name(_cid_code_maps(doc), cand_nm)
                            if cmap_:
                                by_cid[(cand_nm, True)] = _encode_simple_text(
                                    old_n, cmap_["rev"], cmap_["max_len"])
                        else:
                            cm_ = _lookup_by_name(_simple_font_code_maps(doc), cand_nm)
                            enc_ = _lookup_by_name(_simple_font_encodings(doc), cand_nm)
                            got = (_encode_simple_text(old_n, cm_["rev"], cm_["max_len"])
                                   if cm_ else None) or _encode_fallback(old_n, enc_)
                            by_cid[(cand_nm, False)] = got
                multi = _spans_multiple_fonts(doc, tpage, old_n, by_cid)
            except Exception:  # noqa: BLE001 — diagnosis must not break the refusal
                multi = False
        doc.close()
        if multi:
            return {"ok": False, "reason": "spans_multiple_fonts",
                    "message": _REASON_MSG["spans_multiple_fonts"]}
        return prep_error or prep_error_other or {
            "ok": False, "reason": "sequence_not_found",
            "message": _REASON_MSG["sequence_not_found"]}

    doc.close()
    doc = fitz.open(stream=chosen_bytes, filetype="pdf")
    page = doc[tpage]
    old_codes = prep["old_codes"]
    new_codes = prep["new_codes"]
    extended_chars = prep["extended_chars"]
    loc_result = prep["loc"]

    old_x1 = fitz.Rect(target["bbox"]).x1
    old_x0 = target["origin"][0]
    page_h = doc[tpage].rect.height
    base_y_pdf = page_h - target["origin"][1]
    edited = doc.tobytes(garbage=4, deflate=True)
    doc.close()

    # ── reflow: push whatever follows on this line so it keeps its place ──
    # A longer replacement runs into the next span, because each span carries
    # its own absolute text matrix and so does not move when the text before
    # it grows. Measured on the attestation fixture, the replacement name
    # overran the comma after it by 66pt.
    reflowed = 0
    tracking = 0.0
    wordspace = 0.0
    glyph_scale = 100.0
    new_n = _norm(new)

    # New right edge of the field, from the PDF's own advance widths rather
    # than by re-extracting the edited page (see _run_width_pt: an overflowing
    # run extracts truncated, which is the one case this must get right).
    def _field_x1(track: float = 0.0):
        """New right edge of the edited field, or None if neither method can
        establish it.

        Prefers the PDF's own advance widths (see _run_width_pt), because that
        is the only method that works when the run overflows the page: glyphs
        past the media box are not extracted, so re-reading the edited page
        reports the text silently truncated and makes an overflow look like a
        fit. Falls back to extraction for fonts whose width table can't be
        parsed — many can't, and refusing those outright turned a safety check
        into a regression that failed 16 previously-passing edits.
        """
        d = fitz.open(stream=edited, filetype="pdf")
        try:
            w = _run_width_pt(d, nm, new_codes, target["size"], is_cid)
            if w >= 0:
                return old_x0 + w + track * len(new_codes)
            texts = [_norm(s2["text"]) for s2 in _spans(d[tpage])]
            for s2, t in zip(_spans(d[tpage]), texts):
                if new_n and new_n in t:
                    return fitz.Rect(s2["bbox"]).x1
            # The full replacement isn't there but its beginning is: the run
            # was cut off at the page boundary, because glyphs outside the
            # media box are not extracted. Truncation IS the overflow, so it
            # is reported as one rather than as "couldn't measure" — which
            # would skip the check and ship the very document this is meant
            # to prevent. Found on a LaTeX paper, where the replacement ran
            # to x=613 on a 612pt page and came back looking like it fit.
            head = new_n[:12]
            if head and any(head in t for t in texts):
                return _TRUNCATED
        finally:
            d.close()
        return None

    # ── does the edited line still fit the page's own text margin? ────────
    # Reflow moves what follows, so a longer replacement pushes the whole
    # line rightward; without this check a long enough value simply runs off
    # the page (measured: a 76-character name ended at x=603 on a 595pt page).
    odoc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        right_limit = _text_right_limit(odoc[tpage], target["bbox"])
        next_x0 = _next_text_x0(odoc[tpage], target["bbox"])
        line_ys = _line_baselines(odoc[tpage], target["bbox"], page_h)
        _lspans = _line_spans(odoc[tpage], target["bbox"])
        # The width reserved for what follows has to be measured over the
        # SAME line that reflow will move. Taking it from the exact baseline
        # while reflow moved every run of the line reserved too little, and a
        # 237pt replacement pushed two neighbouring runs to x=618 and x=783
        # on a 595.92pt page — off the paper, and invisible to a check that
        # reads text back, because glyphs outside the media box are not
        # extracted at all.
        line_end_old = max((sp_["bbox"][2] for sp_ in _lspans), default=None)
        # One slot per run: where a decoration OF THAT RUN may sit, which is
        # under it — between its baseline and the bottom of its box.
        line_slots = tuple((sp_["bbox"][0], sp_["bbox"][2],
                            page_h - sp_["origin"][1], page_h - sp_["bbox"][3])
                           for sp_ in _lspans)
        line_rule_h = (max(2.0, 0.12 * (max(x["bbox"][3] for x in _lspans)
                                        - min(x["bbox"][1] for x in _lspans)))
                       if _lspans else 0.0)
        # Is the neighbour PINNED where it is, or does it flow with the
        # field? Only a pinned neighbour has to be moved out of the way.
        #
        # Finding no operator at the neighbour's x has two quite different
        # causes, and conflating them is wrong in both directions. It can
        # mean the neighbour is shown in the same string or TJ array as the
        # field, so the field's own glyph advances carry it along and there
        # is nothing to move. Or it can mean this page's coordinates cannot
        # be read at all — headless Chrome wraps the page in
        # ".24 0 0 -.24 0 841.92 cm" with further scales nested inside, so
        # nothing in its stream matches a coordinate read off the page.
        #
        # Whether ANY operator places text on this baseline separates them.
        # None at all means "cannot tell", and then the neighbour is assumed
        # pinned: accepting instead drew 14.8pt over the sentence after a
        # field on a Chrome-printed page. It deliberately does not ask about
        # the field's own x — in a LaTeX paper a field is usually a substring
        # of a longer run and has no operator of its own, and demanding one
        # refused 6 sound edits.
        seen = _positions_on_baseline(odoc, odoc[tpage], line_ys)
        next_pinned = next_x0 is not None and (
            not seen or _pinned_at(seen, next_x0))
    finally:
        odoc.close()
    # How much the value may grow before anything has to move, and how much
    # room there is once it does.
    #
    # Reserving the full width of everything to the right of the field was
    # far too pessimistic. On the W-9 the heading "What's New" shares a
    # visual line with an unrelated paragraph 212pt to its right, and
    # reserving that paragraph's whole extent left the heading 6pt of room
    # and refused a 20pt edit that had 212pt of empty space in front of it.
    #
    # So a gap is PRESERVED up to two ems and is FREE beyond that. Two ems is
    # wider than any inter-word or inter-token space a typesetter would
    # leave, and far narrower than a tab stop or a column gutter, which is
    # exactly the distinction being drawn. Measured: the space before the
    # comma after a value on the attestation fixture is 8.08pt at 14.04pt
    # type — 0.58 em, entirely preserved, as the gap-preservation guarantee
    # requires; the gutter before that W-9 paragraph is 212.58pt at 12pt type
    # — 17.7 em, almost all of it free. Where the neighbour is not pinned at
    # all it flows with the field's own glyph advances, so there is no slack
    # to spend and every point of growth pushes the line along.
    _gap_keep = 2.0 * target["size"]
    slack = 0.0
    if next_pinned and next_x0 is not None:
        slack = max(0.0, (next_x0 - old_x1) - _gap_keep)
    trailing = max(0.0, (line_end_old or old_x1) - old_x1)

    # When the extent can't be established at all, the overflow check and the
    # reflow are both skipped and the edit proceeds exactly as it did before
    # either existed — an unmeasurable line is a reason to add nothing, not a
    # reason to throw away a good edit.
    new_x1 = _field_x1()
    if new_x1 is _TRUNCATED:
        return {"ok": False, "reason": "would_overflow",
                "message": ("The replacement runs past the edge of the page — far enough "
                            "that the text is cut off entirely, so it can't be fitted on "
                            "this line.")}
    if new_x1 is not None:
        # The value has to end by the margin, and whatever it pushes has to
        # end there too — after absorbing the slack.
        avail = min(right_limit,
                    old_x1 + slack + max(0.0, right_limit - old_x1 - trailing)
                    ) - old_x0
        base_w = new_x1 - old_x0
        if base_w - avail > 0.05:
            # Spend the elastic levers a typesetter would, in order of how
            # invisible each is, and refuse with the real shortfall if they
            # are not enough. Font size is not among them: changing it is a
            # visible change of identity.
            d = fitz.open(stream=edited, filetype="pdf")
            try:
                space_w, allow_ws = 0.0, (not is_cid)
                if allow_ws:
                    refs_w = _simple_font_refs(d, nm)
                    parsed = _parse_widths_array(d, refs_w) if refs_w else None
                    if parsed and 0 <= (32 - parsed[0]) < len(parsed[1]):
                        space_w = parsed[1][32 - parsed[0]] * target["size"] / 1000.0
                    else:
                        allow_ws = False
            finally:
                d.close()
            n_sp = sum(1 for c in new_codes if c == 32) if not is_cid else 0
            plan = _fit_plan(base_w, avail, len(new_codes), n_sp, space_w,
                             target["size"], allow_ws)
            if not plan["fits"]:
                return {"ok": False, "reason": "would_overflow",
                        "needed_pt": round(plan["need"], 2),
                        "recoverable_pt": round(plan["recovered"], 2),
                        "short_by_pt": round(plan["short"], 2),
                        "message": (
                            f"The replacement is {plan['need']:.0f}pt too wide for this "
                            f"line. Tightening word spacing, tracking and glyph width as "
                            f"far as is invisible recovers {plan['recovered']:.0f}pt, "
                            f"leaving it {plan['short']:.0f}pt short — it can't be fitted "
                            f"here without changing the text or its size.")}
            tdoc = fitz.open(stream=edited, filetype="pdf")
            try:
                applied = _apply_text_state(tdoc, tdoc[tpage], base_y_pdf, old_x0,
                                            plan["tc"], plan["tw"], plan["tz"])
                if applied:
                    edited = tdoc.tobytes(garbage=4, deflate=True)
                    tracking = plan["tc"]
                    wordspace = plan["tw"]
                    glyph_scale = plan["tz"]
                    th = plan["tz"] / 100.0
                    new_x1 = old_x0 + th * (base_w + plan["tc"] * len(new_codes)
                                            + plan["tw"] * n_sp)
            finally:
                tdoc.close()
            if not applied:
                # The plan says this only fits once tightened, and the
                # tightening could not be written — so it does NOT fit.
                # Carrying on regardless is the worse answer of the two: it
                # accepts the edit and draws the over-wide run straight over
                # whatever follows it. Refuse with the same arithmetic, and
                # say that the width is all that is missing.
                return {"ok": False, "reason": "would_overflow",
                        "needed_pt": round(plan["need"], 2),
                        "recoverable_pt": 0.0,
                        "short_by_pt": round(plan["need"], 2),
                        "message": (
                            f"The replacement is {plan['need']:.0f}pt too wide for this "
                            f"line, and this text object's layout can't be tightened to "
                            f"absorb it — it can't be fitted here without changing the "
                            f"text or its size.")}

    rdoc = fitz.open(stream=edited, filetype="pdf")
    try:
        # Only the growth that the slack could not absorb has to be pushed.
        _dx = new_x1 - old_x1 if new_x1 is not None else 0.0
        _push = _dx - slack if _dx > 0 else _dx
        if new_x1 is not None and abs(_push) > 0.05:
            reflow_from = min(old_x1, next_x0) if next_x0 is not None else old_x1
            n = _reflow_same_line(rdoc, rdoc[tpage], line_ys,
                                  reflow_from, _push,
                                  slots=line_slots, rule_h=line_rule_h,
                                  field=(old_x0, old_x1, target["size"], new_x1))
            if n:
                reflowed = n
                edited = rdoc.tobytes(garbage=4, deflate=True)
    except Exception:  # noqa: BLE001 — reflow is an improvement, never a
        reflowed = 0   # reason to lose an otherwise-good edit
    finally:
        rdoc.close()

    if (next_pinned and new_x1 is not None
            and new_x1 > next_x0 + 0.05 and new_x1 > old_x1 + 0.05
            and not reflowed):
        # The value now reaches into text that is PINNED where it is by its
        # own positioning operator, and none of it could be moved. Fitting
        # reserved WIDTH for that text but cannot reserve its POSITION, so
        # carrying on would draw the new value straight through it — the
        # silent overrun this engine exists to prevent. Refuse instead.
        #
        # Every clause of the condition earns its place. `next_pinned` keeps
        # text that merely flows with the field out of it. `> old_x1` keeps a
        # pre-existing overlap out of it: this document already draws the
        # birthplace field over the comma after it, and a replacement no
        # wider than the original cannot make that worse.
        return {"ok": False, "reason": "cannot_reflow",
                "needed_pt": round(new_x1 - next_x0, 2),
                "message": _REASON_MSG["cannot_reflow"]}

    diff = {"outside": None, "inside": None}
    if verify:
        # The exclusion zone for "did anything ELSE on the page change" must
        # cover where the edited text NOW sits, not just its old extent — a
        # longer/shorter replacement legitimately occupies a different amount
        # of space; that alone isn't a violation, only something outside
        # BOTH extents is. Read this from a FRESH reopen of the serialized
        # bytes, not the live in-session `doc` — PyMuPDF can cache font/CMap
        # interpretation per session, so text extracted right after
        # update_stream() on the same object may not reflect the just-
        # written data (e.g. a ToUnicode fix) even though the bytes
        # themselves are already correct.
        excl_bbox = fitz.Rect(target["bbox"])
        edoc = fitz.open(stream=edited, filetype="pdf")
        for s2 in _spans(edoc[tpage]):
            if new_n and new_n in _norm(s2["text"]):
                excl_bbox |= fitz.Rect(s2["bbox"])
                break
        if reflowed:
            # Content was deliberately moved, so pixels outside the field DID
            # change and claiming otherwise would be false. The guarantee is
            # narrowed honestly to "nothing outside the edited LINE changed":
            # the exclusion runs from the field's left edge to the page edge,
            # across that line's own band only.
            band = fitz.Rect(target["bbox"])
            band.x1 = edoc[tpage].rect.width
            excl_bbox |= band
        edoc.close()
        diff = _pixel_diff(pdf_bytes, edited, excl_bbox, tpage)

    return {"ok": True, "tier": ("extend" if extended_chars else ("remap" if is_cid else "clean")),
            "page": tpage, "case": loc_result["case"], "extended_chars": extended_chars,
            "reflowed": reflowed, "tracking": round(tracking, 4),
            "dekerned": bool(loc_result.get("dekerned")),
            "dropped_gaps": loc_result.get("dropped_gaps") or [],
            "wordspace": round(wordspace, 4), "glyph_scale": round(glyph_scale, 3),
            "diff_outside": diff["outside"], "diff_inside": diff["inside"],
            "guarantee": (diff["outside"] == 0) if verify else None,
            "pdf_b64": base64.b64encode(edited).decode()}


def _pixel_diff(orig: bytes, edited: bytes, bbox, page_no: int, zoom: float = 2.0):
    o = fitz.open(stream=orig, filetype="pdf")
    e = fitz.open(stream=edited, filetype="pdf")
    m = fitz.Matrix(zoom, zoom)
    po = o[page_no].get_pixmap(matrix=m, alpha=False)
    pe = e[page_no].get_pixmap(matrix=m, alpha=False)
    o.close(); e.close()
    if (po.width, po.height, po.n) != (pe.width, pe.height, pe.n):
        return {"outside": -1, "inside": -1}
    pad = 3
    bx0, by0 = int((bbox.x0 - pad) * zoom), int((bbox.y0 - pad) * zoom)
    bx1, by1 = int((bbox.x1 + pad) * zoom), int((bbox.y1 + pad) * zoom)
    w, h, n = po.width, po.height, po.n
    if _np is not None:
        a = _np.frombuffer(po.samples, dtype=_np.uint8).reshape(h, w, n)
        b = _np.frombuffer(pe.samples, dtype=_np.uint8).reshape(h, w, n)
        diff = _np.any(a != b, axis=2)
        mask = _np.zeros((h, w), dtype=bool)
        y0, y1 = max(0, by0), min(h, by1 + 1)
        x0, x1 = max(0, bx0), min(w, bx1 + 1)
        mask[y0:y1, x0:x1] = True
        return {"outside": int(diff[~mask].sum()), "inside": int(diff[mask].sum())}
    so, se = po.samples, pe.samples
    out = ins = 0
    for y in range(h):
        row = y * w * n
        iny = by0 <= y <= by1
        for x in range(w):
            i = row + x * n
            d = so[i] != se[i] or so[i + 1] != se[i + 1] or so[i + 2] != se[i + 2]
            if iny and bx0 <= x <= bx1:
                ins += d
            else:
                out += d
    return {"outside": out, "inside": ins}
