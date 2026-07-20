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
            toks.append(("str_lit", j, i, data[j + 1:i - 1]))
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
            for tt in seq:
                if tt[0] in ("str_hex", "str_lit"):
                    str_toks.append({"start": tt[1], "end": tt[2], "raw": _gids_from_tok(tt)})
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


# ── flatten codes across runs of ONE font, in document order ────────────────
def _flatten(runs, font_name, refmap, is_cid):
    flat, loc = [], []
    for ri, run in enumerate(runs):
        ref = (run["font"] or b"").decode("latin-1").lstrip("/")
        if refmap.get(ref) != font_name:
            continue
        _decode_toks(run, is_cid)
        for ti, tok in enumerate(run["str_toks"]):
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
        if ti0 != ti1:
            return {"ok": False, "reason": "kerning_split_within_run"}
        return {"ok": True, "case": "single_token", "run": r_first, "tok": ti0,
                "code_lo": ci0, "code_hi": ci1}

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
                try:
                    codes = list(text.encode("latin-1"))
                except UnicodeEncodeError:
                    codes = None
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
                "page": pno, "text": text, "font": nm, "subtype": st,
                "simple": bool(is_simple),
                "bbox": [round(v, 1) for v in s["bbox"]],
                "editable": editable, "limitation": limitation,
            })
    n_pages = doc.page_count
    doc.close()
    return {"pages": n_pages, "count": len(fields), "simple": simple, "cid": cid, "fields": fields}


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
        return None
    m4 = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
    tu_xref = int(m4.group(1)) if m4 else None
    return {"cid_xref": cid_xref, "ff_xref": int(m3.group(1)), "tu_xref": tu_xref}


def _add_tounicode_entries(doc, tu_xref, gid_to_unicode: dict) -> bool:
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
        lines.append(f"<{gid:04X}> <{cp:04X}>")
    lines.append("endbfchar\n")
    block = ("\n".join(lines)).encode("latin-1")
    idx = data.rfind(b"endcmap")
    new_data = data[:idx] + block + data[idx:]
    doc.update_stream(tu_xref, new_data)
    return True


def _try_extend(doc, font_display_name, missing_chars):
    """Attempt to inject `missing_chars` into font_display_name's embedded
    subset. On success, mutates `doc` (new FontFile2 + extended /W array) and
    returns {char: gid}; on ANY failure returns None and leaves doc untouched
    (well-formed no-op — callers must not assume partial success)."""
    type0_xref = None
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[3].split("+")[-1] == font_display_name:
                type0_xref = f[0]
                break
        if type0_xref:
            break
    if type0_xref is None:
        return None
    refs = _font_stream_refs(doc, type0_xref)
    if not refs:
        return None
    donor = font_extend.resolve_donor(font_display_name)
    if not donor:
        return None
    subset_bytes = doc.xref_stream(refs["ff_xref"])
    try:
        result = font_extend.extend_font(subset_bytes, donor, missing_chars)
    except Exception:  # noqa: BLE001 — any failure -> honest refusal, not a guess
        return None

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
            return None
        w_xref = int(m.group(1))
        arr = doc.xref_object(w_xref, compressed=True)
        if not arr or not arr.strip().endswith("]"):
            return None
        doc.update_object(w_xref, arr.strip()[:-1] + additions + "]")
    else:
        return None  # unexpected /W shape — refuse rather than risk it
    doc.update_stream(refs["ff_xref"], result["font_bytes"])
    _add_tounicode_entries(doc, refs.get("tu_xref"),
                           {result["gid"][ch]: ord(ch) for ch in missing_chars})
    return result["gid"]


def edit(pdf_bytes: bytes, old: str, new: str) -> dict:
    """Attempt a true in-place swap of `old`->`new`. Returns a verdict and, when
    it succeeds, the edited PDF (base64) + a pixel-diff proof."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    subtype, used = _font_info(doc)
    old_n = _norm(old)
    target, tpage = None, 0
    for pno in range(doc.page_count):
        for s in _spans(doc[pno]):
            if old_n in _norm(s["text"]):
                target, tpage = s, pno
                break
        if target:
            break
    if not target:
        doc.close()
        return {"ok": False, "reason": "not_found", "message": _REASON_MSG["not_found"]}

    nm = target["font"].split("+")[-1]
    is_cid = subtype.get(nm) == "Type0"
    page = doc[tpage]

    extended_chars: list = []
    if is_cid:
        fm = _gid_maps(doc).get(nm, {})
        missing = sorted({ch for ch in new if not ch.isspace() and _norm(ch) not in fm})
        if missing:
            new_gids = _try_extend(doc, nm, missing)
            if new_gids is None:
                doc.close()
                return {"ok": False, "reason": "extend", "tier": "extend", "missing": missing,
                        "message": (f"This field's font is an embedded subset that doesn't contain "
                                    f"these characters yet: {missing}. Tried to add the glyph(s) from "
                                    f"an open-source donor font, but couldn't (unknown font family, or "
                                    f"the fetch/merge failed).")}
            fm = {**fm, **{_norm(ch): g for ch, g in new_gids.items()}}
            extended_chars = missing
        old_codes = [fm.get(_norm(ch)) for ch in old_n]
        new_codes = [fm.get(_norm(ch)) for ch in new]
        if any(c is None for c in old_codes) or any(c is None for c in new_codes):
            doc.close()
            return {"ok": False, "reason": "unmappable", "message": _REASON_MSG["unmappable"]}
    else:
        try:
            old_codes = list(old_n.encode("latin-1"))
            new_codes = list(new.encode("latin-1"))
        except UnicodeEncodeError:
            doc.close()
            return {"ok": False, "reason": "encoding", "message": _REASON_MSG["encoding"]}

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
    edited = doc.tobytes(garbage=4, deflate=True)
    doc.close()

    # The exclusion zone for "did anything ELSE on the page change" must cover
    # where the edited text NOW sits, not just its old extent — a longer/
    # shorter replacement legitimately occupies a different amount of space;
    # that alone isn't a violation, only something outside BOTH extents is.
    # Read this from a FRESH reopen of the serialized bytes, not the live
    # in-session `doc` — PyMuPDF can cache font/CMap interpretation per
    # session, so text extracted right after update_stream() on the same
    # object may not reflect the just-written data (e.g. a ToUnicode fix)
    # even though the bytes themselves are already correct.
    excl_bbox = fitz.Rect(target["bbox"])
    new_n = _norm(new)
    edoc = fitz.open(stream=edited, filetype="pdf")
    for s2 in _spans(edoc[tpage]):
        if new_n and new_n in _norm(s2["text"]):
            excl_bbox |= fitz.Rect(s2["bbox"])
            break
    edoc.close()

    diff = _pixel_diff(pdf_bytes, edited, excl_bbox, tpage)
    return {"ok": True, "tier": ("extend" if extended_chars else ("remap" if is_cid else "clean")),
            "page": tpage, "case": loc_result["case"], "extended_chars": extended_chars,
            "diff_outside": diff["outside"], "diff_inside": diff["inside"],
            "guarantee": diff["outside"] == 0,
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
