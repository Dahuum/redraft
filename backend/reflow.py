"""reflow.py — re-wrap a paragraph the way its author's software would.

When an edit makes a line too long, a word processor moves the overflowing
words to the next line, and if the paragraph gains a line, everything below
it moves down one line. The in-place engine cannot do that, so it refused;
the redraw engine shrank the text instead — a change of size anyone can see.

This does what the producer would have done, and nothing it cannot prove:

1. MODEL. The paragraph is the run of lines sharing the edited line's left
   edge at one constant leading. Line breaks are greedy against a right
   limit, measured with the document's OWN font metrics — the advances the
   page itself uses.

2. SELF-CHECK. Before anything is touched, the UNEDITED paragraph is
   re-broken with that model, and it must reproduce the original's line
   breaks exactly. If it does not — justified text, a different margin, a
   producer with another line-breaking rule — the model is wrong for this
   document and the edit is refused rather than given an invented layout.

3. EMIT. The old glyphs are deleted and the new lines written with the
   page's own font resources and codes, glyphs the subset lacks injected
   first (see inplace_spike). No font is added to the document.

4. SHIFT. If the paragraph gains lines, everything below it moves down by
   exactly that many leadings, drawn from a copy of the page clipped below
   the paragraph. Refused if anything crosses the cut or would leave the
   page, or if the page carries links or form fields below it.

5. VERIFY. The output is re-read: the paragraph's lines must be the predicted
   ones at the predicted baselines, every other word must be exactly where it
   was (or exactly one shift lower), and nothing may render as .notdef.

Validated against a producer twin (spikes/audit/audit_twin.py): the same
change made in the source and re-typeset by LibreOffice.

Scope, deliberately: left-aligned (ragged-right) paragraphs in simple fonts
(TrueType or Type1 with /Widths), horizontal text on an unrotated page. Every
other shape is refused with a reason.
"""
from __future__ import annotations

import io
import re

import fitz

import inplace_spike as S

# How far a measured position may differ from the model before the model is
# declared wrong for this document. LibreOffice rounds each glyph to device
# units (±3/1000 em in its TJ arrays), so a few hundredths of a point is the
# producer's own noise.
_POS_TOL = 0.6


def _refuse(reason: str, message: str) -> dict:
    return {"ok": False, "reason": reason, "message": message}


def _lines(page):
    """Visual lines with per-character style, left to right, top to bottom."""
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            if l.get("dir", (1, 0)) != (1, 0):
                continue
            chars = []
            for s in l["spans"]:
                for c in s["chars"]:
                    chars.append({"c": c["c"], "x0": c["bbox"][0], "x1": c["bbox"][2],
                                  "ox": c["origin"][0], "oy": c["origin"][1],
                                  "font": s["font"], "size": s["size"],
                                  "color": s["color"]})
            if chars:
                out.append({"y": chars[0]["oy"], "x0": chars[0]["ox"],
                            "bbox": fitz.Rect(l["bbox"]), "chars": chars})
    out.sort(key=lambda l: (round(l["y"], 1), l["x0"]))
    return out


def _paragraph(lines, target_bbox):
    """(paragraph lines, leading) around the line holding *target_bbox*."""
    tb = fitz.Rect(target_bbox)
    idx = next((i for i, l in enumerate(lines)
                if l["bbox"].intersects(tb) and abs(l["bbox"].y0 - tb.y0) < 2.0), None)
    if idx is None:
        return None, None
    x0 = lines[idx]["x0"]
    col = [l for l in lines if abs(l["x0"] - x0) <= 0.6]
    k = col.index(lines[idx])
    lead = None
    for j in (k + 1, k - 1):
        if 0 <= j < len(col):
            d = abs(col[j]["y"] - col[k]["y"])
            size = col[k]["chars"][0]["size"]
            if 0.9 * size < d < 2.2 * size:
                lead = d
                break
    if lead is None:
        return [col[k]], None        # a one-line paragraph
    lo = hi = k
    while lo - 1 >= 0 and abs((col[lo]["y"] - col[lo - 1]["y"]) - lead) <= 0.3:
        lo -= 1
    while hi + 1 < len(col) and abs((col[hi + 1]["y"] - col[hi]["y"]) - lead) <= 0.3:
        hi += 1
    return col[lo:hi + 1], lead


class _Metrics:
    """Advance widths in points, from the document's own fonts."""

    def __init__(self, doc):
        self.doc = doc
        self.cache: dict = {}

    def font(self, name):
        if name not in self.cache:
            refs = S._simple_font_refs(self.doc, name, require_truetype=False)
            parsed = S._parse_widths_array(self.doc, refs) if refs else None
            cm = S._lookup_by_name(S._simple_font_code_maps(self.doc), name)
            enc = S._lookup_by_name(S._simple_font_encodings(self.doc), name)
            self.cache[name] = {"refs": refs, "widths": parsed, "cm": cm, "enc": enc}
        return self.cache[name]

    def reset(self):
        self.cache.clear()

    def code(self, name, ch):
        f = self.font(name)
        if f["cm"]:
            codes = S._encode_simple_text(ch, f["cm"]["rev"], f["cm"]["max_len"])
            if codes:
                return codes[0]
        # A private-code font (no /Encoding) has no standard byte for
        # anything: Latin-1 would name 'M' as 77, which in this font is
        # nothing at all, or some other glyph.
        if f["refs"] and S._private_code_font(self.doc, f["refs"]):
            return None
        codes = S._encode_fallback(ch, f["enc"])
        return codes[0] if codes else None

    def advance(self, name, size, ch, code=None):
        f = self.font(name)
        if code is None:
            code = self.code(name, ch)
        if code is None or not f["widths"]:
            return None
        first, ws = f["widths"]
        if not 0 <= code - first < len(ws):
            return None
        return ws[code - first] * size / 1000.0


def _units(chars, m):
    """Group a styled char stream into GLYPH units, as the font encodes them.

    A ligature is one glyph standing for several characters: "certifie" on a
    LibreOffice letter is set with the font's own "fi" glyph, and text
    extraction splits it back into 'f' + 'i', each with a position of its
    own that no per-character model can reproduce (3.3pt off at that 'i').
    Encoding each same-style run longest-match-first through the font's
    /ToUnicode map recovers the glyphs the producer actually drew — and new
    text gets the same ligatures, as the producer's shaper would have done.
    Returns units {"t", "code", "font", "size", "color", "ox", "oy"}; code is
    None where the font has no code yet.
    """
    out, i = [], 0
    while i < len(chars):
        c = chars[i]
        f = m.font(c["font"])
        cm = f["cm"]
        took = None
        if cm:
            for L in range(min(cm["max_len"], len(chars) - i), 1, -1):
                seg = chars[i:i + L]
                if any(x["font"] != c["font"] or x["size"] != c["size"] for x in seg):
                    continue
                code = cm["rev"].get("".join(x["c"] for x in seg))
                if code is not None:
                    took = (L, code)
                    break
        if took is None:
            took = (1, m.code(c["font"], c["c"]))
        L, code = took
        out.append({"t": "".join(x["c"] for x in chars[i:i + L]), "code": code,
                    "font": c["font"], "size": c["size"], "color": c["color"],
                    "ox": c.get("ox"), "oy": c.get("oy")})
        i += L
    return out


def _words(stream):
    """Split a styled char stream into words, each keeping its trailing space."""
    words, cur = [], []
    for ch in stream:
        cur.append(ch)
        if ch["t"] == " ":
            words.append(cur)
            cur = []
    if cur:
        words.append(cur)
    return words


def _break(words, x0, limit, width):
    """Greedy: a word goes on the line if it fits without its trailing space."""
    lines, line, x = [], [], x0
    for w in words:
        wt = w[:-1] if w and w[-1]["t"] == " " else w
        if line and x + width(wt) > limit + 0.05:
            lines.append(line)
            line, x = [], x0
        line.append(w)
        x += width(w)
    if line:
        lines.append(line)
    return lines


def _text(line):
    return "".join(c["t"] for w in line for c in w)


def reflow(pdf_bytes: bytes, span: dict, new_text: str) -> dict:
    """Replace *span*'s text with *new_text*, re-wrapping its paragraph.

    Returns {"ok": True, "pdf": bytes, "lines": n_before -> n_after} or a
    refusal dict with a reason.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return _refuse("unreadable", "The PDF could not be opened.")
    try:
        return _reflow(doc, span, new_text)
    finally:
        doc.close()


def _reflow(doc, span, new_text):
    pno = span.get("page", 0)
    page = doc[pno]
    if page.rotation or page.mediabox.x0 or page.mediabox.y0 \
            or page.cropbox != page.mediabox:
        return _refuse("page_geometry", "This page is rotated or offset.")
    lines = _lines(page)
    para, lead = _paragraph(lines, span["bbox"])
    if not para:
        return _refuse("no_paragraph", "The field's line could not be found.")
    m = _Metrics(doc)

    def width(units):
        tot = 0.0
        for u in units:
            a = m.advance(u["font"], u["size"], u["t"], u["code"])
            if a is None:
                raise KeyError(u["t"])
            tot += a
        return tot

    # The stream of the whole paragraph, lines joined. A line that does not
    # end in a space was broken at one the producer did not draw.
    stream = []
    for i, l in enumerate(para):
        stream += l["chars"]
        if i < len(para) - 1 and l["chars"][-1]["c"] not in (" ", "-", "­"):
            last = dict(l["chars"][-1])
            last["c"] = " "
            stream.append(last)
    for c in stream:
        if c["size"] != stream[0]["size"]:
            return _refuse("mixed_size", "The paragraph mixes type sizes.")

    x0 = para[0]["x0"]
    if any(abs(l["x0"] - x0) > 0.6 for l in para):
        return _refuse("indented", "The paragraph's lines do not share a left edge.")

    # ── self-check: the model must reproduce the original's line breaks ──
    # A break can only be checked where there IS one. A one-line paragraph
    # reproduces under any margin at all, so it proves nothing: on the IRS
    # 1040 "Line 3a" passed that way and was re-set straight across the form
    # beside it. Such a paragraph borrows its margin and leading from a
    # multi-line paragraph at the same left edge and size on the same page,
    # whose breaks WERE reproduced — or is refused.
    size0 = stream[0]["size"]

    def established(p_lines):
        pst = []
        for i, l in enumerate(p_lines):
            pst += l["chars"]
            if i < len(p_lines) - 1 and l["chars"][-1]["c"] not in (" ", "-", "\u00ad"):
                pst.append(dict(l["chars"][-1], c=" "))
        want = ["".join(c["c"] for c in l["chars"]).rstrip() for l in p_lines]
        ws = _words(_units(pst, m))
        for cand in (page.rect.width - x0, max(l["chars"][-1]["x1"] for l in lines)):
            if [_text(g).rstrip() for g in _break(ws, x0, cand, width)] == want:
                return cand
        return None

    try:
        limit = None
        if len(para) >= 2:
            limit = established(para)
        else:
            seen = set()
            for l in lines:
                if abs(l["x0"] - x0) > 0.6 or l["chars"][0]["size"] != size0:
                    continue
                other, olead = _paragraph(lines, l["bbox"])
                if not other or len(other) < 2 or id(other[0]) in seen:
                    continue
                seen.add(id(other[0]))
                limit = established(other)
                if limit is not None:
                    lead = olead
                    break
    except KeyError:
        return _refuse("unmeasurable", "A glyph's width is not in the font's own table.")
    if limit is None or lead is None:
        return _refuse("unknown_layout",
                       "This paragraph's line breaks don't follow a rule Redraft can "
                       "reproduce (it may be justified, set to another margin, or stand "
                       "alone with nothing to measure against), so it can't be re-wrapped "
                       "without inventing a layout.")
    # And every original glyph must sit where the model puts it.
    for l in para:
        x = x0
        for u in _units(l["chars"], m):
            if abs(u["ox"] - x) > _POS_TOL:
                return _refuse("unknown_layout",
                               "This paragraph's glyph positions don't follow the font's "
                               "own advances (kerning or justification), so it can't be "
                               "re-set exactly.")
            x += m.advance(u["font"], u["size"], u["t"], u["code"])

    # ── the edit, in the stream ──
    old = span["text"]
    joined = "".join(c["c"] for c in stream)
    at = None
    for mt in re.finditer(re.escape(old), joined):
        ch = stream[mt.start()]
        if abs(ch["ox"] - span["origin"][0]) <= 0.6 and abs(ch["oy"] - span["origin"][1]) <= 0.6:
            at = mt.start()
            break
    if at is None:
        return _refuse("not_in_paragraph", "The field could not be located in its paragraph.")
    style = stream[at]
    new_chars = [dict(style, c=c) for c in new_text]
    stream2 = stream[:at] + new_chars + stream[at + len(old):]

    # Glyphs the fonts lack: inject them into the document's own subsets,
    # exactly as the in-place engine does, under codes it can then encode.
    need: dict = {}
    for c in stream2:
        if not c["c"].isspace() and m.code(c["font"], c["c"]) is None:
            need.setdefault(c["font"], []).append(c["c"])
    if any(c["c"] == " " and m.code(c["font"], " ") is None for c in stream2):
        return _refuse("missing_glyph", "A font here has no space character.")
    for fname, chars in need.items():
        chars = sorted(set(chars))
        refs = S._simple_font_refs(doc, fname)
        f = m.font(fname)
        if not refs or not f["cm"] or not S._private_code_font(doc, refs):
            return _refuse("missing_glyph", f"The font lacks {chars} and can't be extended here.")
        alloc = S._allocate_private_codes(doc, refs, f["cm"]["rev"], chars)
        if not alloc:
            return _refuse("missing_glyph", f"No free codes for {chars}.")
        gids, why = S._try_extend_simple(doc, fname, chars, code_for=alloc)
        if gids is None:
            return _refuse("missing_glyph", f"Couldn't add {chars}: {why}.")
        S._SIMPLE_GLYPH_MEMO.pop(fname, None)
        m.reset()
        # The ToUnicode map now names the new codes; re-read it.
    try:
        new_lines = _break(_words(_units(stream2, m)), x0, limit, width)
    except KeyError as e:
        return _refuse("unmeasurable", f"No width for {e.args[0]!r}.")

    grow = len(new_lines) - len(para)
    if grow < 0:
        grow = 0          # a shorter paragraph leaves its last line(s) empty
    dy = grow * lead

    # ── what the page must not have below the cut, if anything moves ──
    cut = para[-1]["bbox"].y1 + 0.5
    if dy:
        if any(fitz.Rect(lk["from"]).y0 > cut - 1 for lk in page.get_links()):
            return _refuse("links_below", "Links below this paragraph would have to move.")
        if any(True for _ in page.widgets()):
            return _refuse("form_fields", "This page has form fields.")
        for dr in page.get_drawings():
            r = dr["rect"]
            if r.y0 < cut < r.y1:
                return _refuse("crosses_cut", "A rule or box spans the paragraph's end.")
        for img in page.get_image_info():
            r = fitz.Rect(img["bbox"])
            if r.y0 < cut < r.y1:
                return _refuse("crosses_cut", "An image spans the paragraph's end.")
        lowest = max([l["bbox"].y1 for l in lines] +
                     [dr["rect"].y1 for dr in page.get_drawings()] + [cut])
        bottom_margin = page.rect.height - lowest
        if lowest + dy > page.rect.height - max(page.rect.height - lowest - dy, 0) and \
                lowest + dy > page.rect.height - min(x0, 36.0):
            return _refuse("runs_off_the_page",
                           "Re-wrapping adds a line, and the text below would run off the page.")
        del bottom_margin

    # ── delete the paragraph's glyphs ──
    for l in para:
        r = fitz.Rect(l["bbox"])
        size = l["chars"][0]["size"]
        r.y0 = max(r.y0, l["y"] - 0.8 * size)
        r.y1 = min(r.y1, l["y"] + 0.1 * size)
        page.add_redact_annot(r, cross_out=False, fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    for l in para:
        left = page.get_text("text", clip=l["bbox"]).strip()
        if left:
            return _refuse("delete_failed", "The old text could not be removed cleanly.")

    # ── emit the new lines through the page's own font resources ──
    refmap = {v: k for k, v in S._page_font_refmap(page).items()}
    ops = []
    y0 = para[0]["y"]
    for i, line in enumerate(new_lines):
        y = y0 + i * lead
        x = x0
        run, run_style = [], None
        chars = [u for w in line for u in w]

        def flush():
            if not run:
                return
            f, size, color = run_style
            res = refmap.get(f)
            if res is None:
                raise KeyError(f)
            rgb = ((color >> 16) & 255, (color >> 8) & 255, color & 255)
            hexs = "".join("%02X" % cd for cd in run[1])
            ops.append("BT /%s %.4g Tf %.4g %.4g %.4g rg 1 0 0 1 %.3f %.3f Tm <%s> Tj ET"
                       % (res, size, rgb[0] / 255, rgb[1] / 255, rgb[2] / 255,
                          run[0], page.rect.height - y, hexs))

        for c in chars:
            st = (c["font"], c["size"], c["color"])
            if st != run_style:
                flush()
                run = [x, []]
                run_style = st
            run[1].append(c["code"])
            x += m.advance(c["font"], c["size"], c["t"], c["code"])
        flush()
    data = ("q " + " ".join(ops) + " Q").encode("latin-1")

    # ── rebuild the page IN READING ORDER ──
    # Everything down to the paragraph's end, then the paragraph, then what
    # lies below it (moved down by the lines it gained, or not at all).
    # Appending the new lines last put "Son salaire..." BEFORE the paragraph
    # it follows in the extracted text — copy, search and screen readers all
    # read the page out of order, which is a trace of the edit in itself.
    snap = fitz.open()
    snap.insert_pdf(doc, from_page=pno, to_page=pno)
    full = page.rect
    for x in page.get_contents():
        doc.update_stream(x, b"")
    above = fitz.Rect(full.x0, full.y0, full.x1, cut)
    below = fitz.Rect(full.x0, cut, full.x1, full.y1 - dy)
    page.show_pdf_page(above, snap, 0, clip=above)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, data)
    conts = page.get_contents()
    doc.xref_set_key(page.xref, "Contents",
                     "[" + " ".join(f"{c_} 0 R" for c_ in conts + [xref]) + "]")
    if below.height > 0:
        page.show_pdf_page(below + (0, dy, 0, dy), snap, 0, clip=below)
    snap.close()

    out = doc.tobytes(garbage=3, deflate=True)
    ok = _verify(out, pno, para, new_lines, lead, cut, dy, lines)
    if ok is not True:
        return _refuse("verify_failed", ok)
    return {"ok": True, "pdf": out, "lines": (len(para), len(new_lines)), "shift": dy,
            "cut": cut, "top": para[0]["bbox"].y0}


def _verify(out, pno, para, new_lines, lead, cut, dy, lines_before):
    d = fitz.open(stream=out, filetype="pdf")
    try:
        page = d[pno]
        after = _lines(page)
        x0 = para[0]["x0"]
        for i, nl in enumerate(new_lines):
            y = para[0]["y"] + i * lead
            got = [l for l in after if abs(l["y"] - y) < 0.5 and abs(l["x0"] - x0) < 1.0]
            want = _text(nl).rstrip()
            have = "".join("".join(c["c"] for c in g["chars"]) for g in got).rstrip()
            if have != want:
                return f"line {i + 1} reads {have!r}, expected {want!r}"
        # Every other line: same place, or exactly dy lower if below the cut.
        para_ys = {round(l["y"], 1) for l in para}
        for l in lines_before:
            if round(l["y"], 1) in para_ys and abs(l["x0"] - x0) < 1.0:
                continue
            ty = l["y"] + (dy if l["bbox"].y0 >= cut else 0.0)
            t = "".join(c["c"] for c in l["chars"]).strip()
            if not any(abs(a["y"] - ty) < 0.5 and abs(a["x0"] - l["x0"]) < 0.5
                       and "".join(c["c"] for c in a["chars"]).strip() == t for a in after):
                return f"{t[:30]!r} is not where it should be"
        for sp in page.get_texttrace():
            if any(g[1] == 0 and not chr(g[0]).isspace() for g in sp["chars"]):
                return "a glyph renders as .notdef"
        # Nothing the paragraph now draws may land on anything else's ink.
        acc = getattr(fitz, "TEXT_ACCURATE_BBOXES", 0)
        ws = page.get_text("words", flags=acc)
        band = [fitz.Rect(w[:4]) for w in ws
                if para[0]["bbox"].y0 - 0.5 <= w[1] <= para[0]["y"] + (len(new_lines) - 1) * lead
                and w[0] >= x0 - 0.5]
        mine = [r for r in band if any(abs(r.y1 - (para[0]["y"] + i * lead)) < 0.6 * para[0]["chars"][0]["size"]
                                       for i in range(len(new_lines)))]
        others = [fitz.Rect(w[:4]) for w in ws if fitz.Rect(w[:4]) not in mine]
        for r in mine:
            for o in others:
                ov = r & o
                if ov.is_valid and ov.get_area() > 0.15 * min(r.get_area(), o.get_area()):
                    return "the re-wrapped text would print over other text"
        return True
    finally:
        d.close()
