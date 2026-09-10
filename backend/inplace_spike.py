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
def _simple_font_code_maps(doc):
    out, seen_xrefs = {}, set()
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            xref, subtype, name = f[0], f[2], f[3].split("+")[-1]
            if subtype == "Type0" or name in out or xref in seen_xrefs:
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
def _flatten(runs, font_name, refmap, is_cid):
    flat, loc = [], []
    for ri, run in enumerate(runs):
        ref = (run["font"] or b"").decode("latin-1").lstrip("/")
        if refmap.get(ref) != font_name:
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
def _locate(runs, refmap, font_name, old_codes, is_cid):
    flat, loc = _flatten(runs, font_name, refmap, is_cid)
    if not flat:
        return {"ok": False, "reason": "no_runs_for_font"}
    positions = _find_all(flat, old_codes)
    if not positions:
        return {"ok": False, "reason": "sequence_not_found"}
    p = positions[0]
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
                    "code_lo": ci0, "code_hi": ci1}
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
        if ci0 == 0 and ci1 == last_tok_len - 1 and boundaries_ok:
            return {"ok": True, "case": "multi_token", "run": r_first,
                    "tok_lo": ti0, "tok_hi": ti1}
        return {"ok": False, "reason": "kerning_split_within_run"}

    last_run = runs[r_last]
    last_tok_idx = len(last_run["str_toks"]) - 1
    last_code_idx = len(last_run["str_toks"][last_tok_idx]["codes"]) - 1
    starts_clean = (loc[p][1] == 0 and loc[p][2] == 0)
    ends_clean = (loc[p + L - 1][1] == last_tok_idx and loc[p + L - 1][2] == last_code_idx)
    if not (starts_clean and ends_clean):
        return {"ok": False, "reason": "ragged_multirun_boundary"}
    return {"ok": True, "case": "multi_run", "touched": touched}


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
    "kerning_split_within_run": ("This text has custom letter-spacing INSIDE a single drawing instruction "
                                "(a kerning-adjusted run) — splicing that safely needs spacing-aware "
                                "reconstruction, not built yet."),
    "ragged_multirun_boundary": ("This text starts or ends in the middle of a drawing instruction that "
                                "ALSO contains other, unrelated text — this test only edits runs that are "
                                "cleanly and entirely covered by the change."),
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
    m = re.search(r"/DescendantFonts\s*\[\s*(\d+)\s+0\s+R", obj)
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
            if f[3].split("+")[-1] == font_display_name:
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
    donor = font_extend.resolve_donor(font_display_name)
    if not donor:
        return None, "no_donor"
    subset_bytes = doc.xref_stream(refs["ff_xref"])
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


_SIMPLE_EXTEND_FAIL_MSG = {
    "no_font_ref": "Couldn't locate this font's own reference on the page.",
    "no_fontfile": "This font's outlines aren't embedded as a TrueType program.",
    "no_donor": ("No other cut of this family is embedded in the document, and no "
                 "open-source donor could be resolved for it."),
    "bad_widths": "This font's width table has an unexpected structure — skipped rather than risk misaligned text.",
    "inject_failed": "Couldn't merge the glyph into this font.",
}


def _simple_font_refs(doc, font_display_name: str):
    """Locate a SIMPLE (non-CID) TrueType font by display name.

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
            if "/Subtype/TrueType" not in obj.replace(" ", ""):
                continue
            m = re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", obj)
            if not m:
                return None
            fd_xref = int(m.group(1))
            fd = doc.xref_object(fd_xref, compressed=True)
            m2 = re.search(r"/FontFile2\s+(\d+)\s+0\s+R", fd)
            if not m2:
                return None
            fc = re.search(r"/FirstChar\s+(\d+)", obj)
            lc = re.search(r"/LastChar\s+(\d+)", obj)
            tu = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
            kind, val = doc.xref_get_key(xref, "Widths")
            return {"font_xref": xref, "fd_xref": fd_xref,
                    "ff_xref": int(m2.group(1)),
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


def _try_extend_simple(doc, font_display_name, missing_chars):
    """Inject `missing_chars` into a SIMPLE font's embedded subset, in place.

    This is what keeps an edit from leaving a trace. Without it the caller
    refuses, the request falls through to the redraw engine, and that engine
    rewrites the page: it paints over the original text with a sampled
    background rectangle, stamps replacement text from a NEWLY EMBEDDED font,
    and leaves the document carrying a font resource its producer never
    wrote. Here nothing new is added to the page at all — the original font
    object keeps its name and its identity and simply gains the glyphs it was
    missing.

    Returns ({char: gid}, None) or (None, reason).
    """
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

    # 2. An open-source donor, as before.
    if result is None:
        try:
            raw = font_extend.resolve_donor(font_display_name)
            if not raw:
                return None, "no_donor"
            result = font_extend.extend_font(subset_bytes, raw, missing_chars)
            provenance = "open-source donor font"
        except Exception:  # noqa: BLE001
            return None, "inject_failed"

    if not _set_simple_widths(doc, refs,
                              {ord(ch): result["width_1000"][ch] for ch in missing_chars}):
        return None, "bad_widths"

    doc.update_stream(refs["ff_xref"], result["font_bytes"])
    # A simple font's /ToUnicode is keyed by character CODE, not by glyph id.
    _add_tounicode_entries(doc, refs.get("tu_xref"),
                           {ord(ch): ord(ch) for ch in missing_chars},
                           hex_digits=2)
    return result["gid"], None


_TM_RE = re.compile(
    rb"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+Tm")
# Operators that move the pen WITHIN a text object. A block containing one of
# these draws at more than one position, so shifting its Tm would drag every
# one of them — including lines on other baselines.
_MULTIPOS_RE = re.compile(rb"(?:^|[\s\]>)])(?:Td|TD|T\*|'|\")(?=[\s/\[(<]|$)")


def _reflow_same_line(doc, page, base_y: float, from_x: float, dx: float,
                      tol: float = 0.6):
    """Shift text that follows the edited field on the SAME line by *dx*.

    Replacing a field with longer text makes it run into whatever came after
    it — on the attestation fixture the name overran the comma that follows
    it by 66pt, because each span is positioned by its own ABSOLUTE text
    matrix and therefore does not move when the text before it grows.

    A word processor would push that comma along, and so does this: for every
    text object on the same baseline whose origin is at or right of the edited
    field's original end, the x translation of its Tm is increased by *dx*.
    Nothing else about those objects changes — same font, same size, same
    string, same y — so the gaps between them are preserved exactly.

    Refuses (returns None) rather than guessing when any affected block
    positions text more than once (a Td/TD/T*/quote inside the block), since
    shifting that block's matrix would move text on other lines too.

    Returns the number of runs shifted, or None if reflow isn't safe here.
    """
    if abs(dx) < 0.01:
        return 0
    edits, shifted = [], 0
    for st in _content_streams(doc, page):
        data = st["data"]
        out = bytearray()
        last = 0
        changed = False
        for m in _TM_RE.finditer(data):
            try:
                a, b, c, d, e, f = (float(m.group(i)) for i in range(1, 7))
            except ValueError:
                continue
            if abs(b) > 1e-6 or abs(c) > 1e-6:
                continue          # rotated/skewed text: not this pass's business
            if abs(f - base_y) > tol or e < from_x - tol:
                continue
            bt = data.rfind(b"BT", 0, m.start())
            et = data.find(b"ET", m.end())
            if bt == -1 or et == -1:
                return None
            if _MULTIPOS_RE.search(data[bt:et]):
                return None       # block draws at several positions — refuse
            new_tm = (f"{a:g} {b:g} {c:g} {d:g} {e + dx:.4f} {f:g} Tm").encode("latin-1")
            out += data[last:m.start()] + new_tm
            last = m.end()
            changed = True
            shifted += 1
        if changed:
            out += data[last:]
            edits.append((st["xref"], bytes(out)))
    for xref, payload in edits:
        doc.update_stream(xref, payload)
    return shifted


def _tm_origin_before(doc, page, needle: bytes):
    """(x, y) of the absolute Tm that positions the run containing *needle*,
    or None. Used to learn the edited field's own baseline so reflow can tell
    which following runs are on the same line."""
    for st in _content_streams(doc, page):
        data = st["data"]
        idx = data.find(needle)
        if idx < 0:
            continue
        best = None
        for m in _TM_RE.finditer(data, 0, idx):
            best = m
        if best is None:
            continue
        try:
            e, f = float(best.group(5)), float(best.group(6))
        except ValueError:
            return None
        return (e, f)
    return None


# Typographic compression budget before an edit is refused as not fitting.
# Tracking (per-character spacing) is the least visible way to reclaim width —
# it is what a typesetter reaches for first, and at a fiftieth of an em it is
# invisible in running text. Glyph scaling distorts letterforms and font-size
# changes are a visible change of identity, so neither is done silently here.
MAX_TRACK_EM = 0.02


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


def _line_extent(page, base_y_topdown: float, tol: float = 0.6):
    """(leftmost x0, rightmost x1) of the text sharing this baseline."""
    lo, hi = None, None
    for blk in page.get_text("dict")["blocks"]:
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                if abs(span["origin"][1] - base_y_topdown) > tol:
                    continue
                if not span.get("text", "").strip():
                    continue
                lo = span["bbox"][0] if lo is None else min(lo, span["bbox"][0])
                hi = span["bbox"][2] if hi is None else max(hi, span["bbox"][2])
    return lo, hi


def _apply_tracking(doc, page, base_y_pdf: float, at_x: float, tc: float,
                    tol: float = 0.6) -> bool:
    """Set character spacing (Tc) on the one text object at (at_x, base_y).

    Tc is a real PDF text-state operator: it adds `tc` to every glyph's
    advance, so a small negative value tightens the run. It is scoped by
    resetting it to 0 before the block's ET, so it cannot leak into any text
    drawn afterwards even in a document that does not wrap its runs in q/Q.
    """
    for st in _content_streams(doc, page):
        data = st["data"]
        for m in _TM_RE.finditer(data):
            try:
                b, c, e, f = (float(m.group(i)) for i in (2, 3, 5, 6))
            except ValueError:
                continue
            if abs(b) > 1e-6 or abs(c) > 1e-6:
                continue
            if abs(f - base_y_pdf) > tol or abs(e - at_x) > tol:
                continue
            et = data.find(b"ET", m.end())
            if et == -1:
                return False
            payload = (data[:m.end()] + f" {tc:.4f} Tc".encode("latin-1")
                       + data[m.end():et] + b" 0 Tc " + data[et:])
            doc.update_stream(st["xref"], payload)
            return True
    return False


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
        refs = _simple_font_refs(doc, font_display_name)
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
    is_cid = subtype.get(nm) == "Type0"
    page = doc[tpage]

    extended_chars: list = []
    if is_cid:
        fm = _gid_maps(doc).get(nm, {})
        missing = sorted({ch for ch in new if not ch.isspace() and _norm(ch) not in fm})
        if missing:
            new_gids, fail_reason = _try_extend(doc, nm, missing)
            if new_gids is None:
                doc.close()
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
            doc.close()
            return {"ok": False, "reason": "unmappable", "message": _REASON_MSG["unmappable"]}
    else:
        cm = _simple_font_code_maps(doc).get(nm)
        enc_name = _simple_font_encodings(doc).get(nm)
        old_codes = _encode_simple_text(old_n, cm["rev"], cm["max_len"]) if cm else None
        new_codes = _encode_simple_text(new, cm["rev"], cm["max_len"]) if cm else None
        if old_codes is None:
            old_codes = _encode_fallback(old_n, enc_name)
            if old_codes is None:
                doc.close()
                return {"ok": False, "reason": "encoding", "message": _REASON_MSG["encoding"]}
        if new_codes is None:
            new_codes = _encode_fallback(new, enc_name)
            if new_codes is None:
                doc.close()
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
                    new_gids, fail_reason = _try_extend_simple(doc, nm, missing)
                    if new_gids is None:
                        doc.close()
                        why = _SIMPLE_EXTEND_FAIL_MSG.get(
                            fail_reason, "Couldn't add the missing glyph(s).")
                        return {"ok": False, "reason": "missing_glyph",
                                "missing": missing, "extend_reason": fail_reason,
                                "message": (f"This field's font is an embedded subset that "
                                            f"doesn't contain these characters yet: "
                                            f"{missing}. {why}")}
                    extended_chars = missing

    streams = _content_streams(doc, page)
    if not streams:
        doc.close()
        return {"ok": False, "reason": "no_content_stream", "message": _REASON_MSG["no_content_stream"]}
    runs = _all_runs(streams)
    refmap = _page_font_refmap(page)

    loc_result = _locate(runs, refmap, nm, old_codes, is_cid)
    if not loc_result["ok"]:
        doc.close()
        reason = loc_result["reason"]
        return {"ok": False, "reason": reason, "message": _REASON_MSG.get(reason, reason)}

    _apply(doc, runs, loc_result, new_codes, is_cid)
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
            for s2 in _spans(d[tpage]):
                if new_n and new_n in _norm(s2["text"]):
                    return fitz.Rect(s2["bbox"]).x1
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
        _, line_end_old = _line_extent(odoc[tpage], target["origin"][1])
    finally:
        odoc.close()
    trailing = max(0.0, (line_end_old or old_x1) - old_x1)

    # When the extent can't be established at all, the overflow check and the
    # reflow are both skipped and the edit proceeds exactly as it did before
    # either existed — an unmeasurable line is a reason to add nothing, not a
    # reason to throw away a good edit.
    new_x1 = _field_x1()
    if new_x1 is not None:
        overflow = (new_x1 + trailing) - right_limit
        if overflow > 0.05:
            # Tighten the run's tracking, the least visible way to reclaim
            # width. Refuse if that isn't enough rather than either running
            # past the margin or silently changing the font size — the caller
            # then falls back to the redraw engine, which resizes visibly but
            # at least keeps the text on the page.
            n_chars = max(len(new), 1)
            tc = -overflow / n_chars
            if abs(tc) <= MAX_TRACK_EM * target["size"]:
                tdoc = fitz.open(stream=edited, filetype="pdf")
                try:
                    if _apply_tracking(tdoc, tdoc[tpage], base_y_pdf, old_x0, tc):
                        edited = tdoc.tobytes(garbage=4, deflate=True)
                        tracking = tc
                        new_x1 = _field_x1(tc) or new_x1
                finally:
                    tdoc.close()
            if (new_x1 + trailing) - right_limit > 0.05:
                return {"ok": False, "reason": "would_overflow",
                        "message": (f"The replacement is too long for this line: it would "
                                    f"reach x={new_x1 + trailing:.0f} past the page's text "
                                    f"margin at x={right_limit:.0f}, and tightening the "
                                    f"spacing within an invisible range isn't enough to "
                                    f"recover it.")}

    rdoc = fitz.open(stream=edited, filetype="pdf")
    try:
        if new_x1 is not None and abs(new_x1 - old_x1) > 0.05:
            n = _reflow_same_line(rdoc, rdoc[tpage], base_y_pdf,
                                  old_x1, new_x1 - old_x1)
            if n:
                reflowed = n
                edited = rdoc.tobytes(garbage=4, deflate=True)
    except Exception:  # noqa: BLE001 — reflow is an improvement, never a
        reflowed = 0   # reason to lose an otherwise-good edit
    finally:
        rdoc.close()

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
