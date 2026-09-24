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
    """VISUAL lines with per-character style, left to right, top to bottom.

    A visual line is everything on one baseline. The extractor's own "lines"
    are not that: Word draws "Né le", "12/05/2001", " à " and "Essaouira" as
    four separate pieces of one line, and taking them one at a time lost
    words off the end of lines and broke every paragraph they belonged to.
    Pieces on the same baseline are merged and ordered by position; a piece
    far from the rest (another column) is not, since only pieces that abut
    within a word space are joined.
    """
    pieces = []
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
                pieces.append({"y": chars[0]["oy"], "x0": chars[0]["ox"],
                               "bbox": fitz.Rect(l["bbox"]), "chars": chars})
    pieces.sort(key=lambda l: (round(l["y"], 1), l["x0"]))
    out = []
    for pc in pieces:
        prev = out[-1] if out else None
        if prev and abs(prev["y"] - pc["y"]) <= 0.5 and \
                pc["x0"] - prev["bbox"].x1 <= 0.5 * pc["chars"][0]["size"]:
            prev["chars"] = sorted(prev["chars"] + pc["chars"], key=lambda c: c["ox"])
            prev["bbox"] |= pc["bbox"]
        else:
            out.append(dict(pc, bbox=fitz.Rect(pc["bbox"])))
    return out


def _covered(para, r):
    """The characters a horizontal mark at *r* sits under or over: on the one
    line whose glyphs it overlaps vertically, those it overlaps horizontally."""
    for l in para:
        size = l["chars"][0]["size"]
        if not (l["y"] - 0.9 * size <= (r.y0 + r.y1) / 2 <= l["y"] + 0.4 * size):
            continue
        run = [c for c in l["chars"] if c["x0"] < r.x1 - 0.3 and c["x1"] > r.x0 + 0.3]
        if run:          # another column's line may share the height
            return run
    return None


def underlines(page, band=None):
    """Every thin horizontal rule on *page* drawn under a run of glyphs —
    a link or <u> underline — as (rect, run text, drawing). A rule reaching
    more than 1pt past its run's glyphs (a table border, a signature line)
    is not an underline of that run and is not listed. *band*: only rules
    whose vertical middle lies in it."""
    lines = _lines(page)
    out = []
    for dr in page.get_drawings():
        r = fitz.Rect(dr["rect"])
        if r.width < 0.5 or r.height > 1.5:
            continue
        if not {it[0] for it in dr["items"]} <= {"l", "re", "qu"}:
            continue
        ym = (r.y0 + r.y1) / 2
        if band is not None and not (band[0] <= ym <= band[1]):
            continue
        run = _covered(lines, r)
        if not run or r.x0 < run[0]["x0"] - 1.0 or r.x1 > run[-1]["x1"] + 1.0:
            continue
        if ym < run[0]["oy"] - 0.05 * run[0]["size"]:
            continue        # a strike-through or overline: not handled here
        out.append((r, "".join(c["c"] for c in run), dr))
    return out


def _link_key(lk):
    return (lk.get("kind"), lk.get("uri"), lk.get("page"), lk.get("nameddest"),
            tuple(round(v, 1) for v in fitz.Rect(lk["from"])))


def _restore_links(page, before, moved=()):
    """Put back every link of *before* that a redaction deleted — MuPDF drops
    link annotations over a redacted area — at its moved rectangle where it
    followed its words (*moved*: [(old rect, new rect)])."""
    have = {_link_key(lk) for lk in page.get_links()}
    for lk in before:
        f = fitz.Rect(lk["from"])
        targets = [f]
        for old, new in moved:
            if abs(old.x0 - f.x0) < 0.05 and abs(old.y0 - f.y0) < 0.05:
                targets = [fitz.Rect(q) for q in (new if isinstance(new, list) else [new])]
                break
        if targets != [f]:
            # the original may still be there at its old place: drop it first
            for cur in page.get_links():
                if _link_key(cur) == _link_key(lk):
                    page.delete_link(cur)
                    break
        for t in targets:
            nl = {k: v for k, v in lk.items() if k not in ("xref", "id", "zoom")}
            nl["from"] = t
            if _link_key(nl) not in have:
                page.insert_link(nl)


def _find_runs(line, text):
    """Every run of *line*'s glyphs spelling *text*, compared on visible
    glyphs only: a stray space a redraw leaves on the line sorts into the
    middle of "contact@1337.ma" and must not hide it."""
    vis = [c for c in line["chars"] if c["c"].strip()]
    want = "".join(ch for ch in text if ch.strip())
    t = "".join(c["c"] for c in vis)
    out, k = [], t.find(want) if want else -1
    while k >= 0:
        out.append(vis[k:k + len(want)])
        k = t.find(want, k + 1)
    return out


def _link_shift(lb, la, f):
    """Where link area *f* goes: re-measured from where the words under it
    now are (visible glyphs), or None when they can't be found again."""
    run = [c for c in (_covered(lb, fitz.Rect(f.x0, (f.y0 + f.y1) / 2, f.x1, (f.y0 + f.y1) / 2))
                       or []) if c["c"].strip() and c["x0"] >= f.x0 - 0.5 and c["x1"] <= f.x1 + 0.5]
    if not run:
        return None
    text = "".join(c["c"] for c in run)
    best = None
    for l in la:
        if abs(l["y"] - run[0]["oy"]) > 2.0 * run[0]["size"]:
            continue
        for got in _find_runs(l, text):
            d_ = abs(got[0]["x0"] - run[0]["x0"]) + abs(got[0]["oy"] - run[0]["oy"])
            if best is None or d_ < best[0]:
                best = (d_, got)
    if best is None:
        return None
    g0, gN = best[1][0], best[1][-1]
    dy = g0["oy"] - run[0]["oy"]
    return fitz.Rect(g0["x0"] - (run[0]["x0"] - f.x0), f.y0 + dy,
                     gN["x1"] + (f.x1 - run[-1]["x1"]), f.y1 + dy)


def carry_underlines(before: bytes, after: bytes, pno: int, band, field=None,
                     edit=None) -> bytes:
    """Put every underline in *band* back under its words after an edit.

    The words may have been pushed along their line (the in-place engine,
    the redraw) or re-set a little smaller (the redraw's bounded resize).
    Each rule is re-measured from where its words' glyphs are NOW — the same
    offsets from their first and last glyph, the same height against their
    baseline — with any link over them. A rule already there is left; one
    still at its old place is replaced; one the redraw deleted with its field
    is drawn again while its words are still on the page (a URL that lost its
    underline reads as edited). Returns *after* itself when nothing changed.
    """
    b = fitz.open(stream=before, filetype="pdf")
    a = fitz.open(stream=after, filetype="pdf")
    try:
        page = a[pno]
        la = _lines(page)
        lb = _lines(b[pno])
        here = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
        todo = []
        for r, text, dr in underlines(b[pno], band):
            if _inside(r, field, text, edit):
                continue      # the edited field's own decoration goes with it
            old = [c for c in (_covered(lb, r) or []) if c["c"].strip()]
            if not old:
                continue
            o0, oN = old[0], old[-1]
            best = None
            for l in la:
                if abs(l["y"] - o0["oy"]) > 2.0 * o0["size"]:
                    continue
                for run in _find_runs(l, text):
                    d_ = abs(run[0]["x0"] - o0["x0"]) + abs(run[0]["oy"] - o0["oy"])
                    if best is None or d_ < best[0]:
                        best = (d_, run)
            if best is None:
                continue                  # its words were edited away
            run = best[1]
            c0, cN = run[0], run[-1]
            dy = c0["oy"] - o0["oy"]
            q = fitz.Rect(c0["x0"] + (r.x0 - o0["x0"]), r.y0 + dy,
                          cN["x1"] + (r.x1 - oN["x1"]), r.y1 + dy)
            if any(abs(h.x0 - q.x0) < 0.3 and abs(h.x1 - q.x1) < 0.3 and abs(h.y0 - q.y0) < 0.3
                   for h in here):
                continue                  # already under its words
            still = any(abs(h.x0 - r.x0) < 0.05 and abs(h.x1 - r.x1) < 0.05
                        and abs(h.y0 - r.y0) < 0.05 for h in here)
            todo.append((r, q, dr, still))
        # Links over this line as they were BEFORE the edit: the redraw's
        # redaction deletes a link over any text it erases, and the page it
        # hands back has already lost them.
        def in_band(lk):
            f = fitz.Rect(lk["from"])
            return band is None or (f.y1 >= band[0] - 2 and f.y0 <= band[1] + 2)
        links_before = [lk for lk in b[pno].get_links() if in_band(lk)]
        have = {_link_key(lk) for lk in page.get_links()}
        lost = any(_link_key(lk) not in have for lk in links_before)
        if not todo and not lost:
            return after
        if any(still for _, _, _, still in todo):
            for r, q, dr, still in todo:
                if still:
                    page.add_redact_annot(r + (-0.3, -0.3, 0.3, 0.3), cross_out=False, fill=False)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=fitz.PDF_REDACT_TEXT_NONE)
        for r, q, dr, still in todo:
            if dr.get("fill") is not None:
                page.draw_rect(q, color=None, fill=dr["fill"], width=0)
            else:
                ym = (q.y0 + q.y1) / 2
                page.draw_line((q.x0, ym), (q.x1, ym), color=dr.get("color") or (0, 0, 0),
                               width=dr.get("width") or 1.0)
        moved = []
        for lk in links_before:
            f = fitz.Rect(lk["from"])
            for r, q, dr, still in todo:
                if f.x0 - 0.5 <= r.x0 and r.x1 <= f.x1 + 0.5 and f.y0 - 2 <= r.y0 <= f.y1 + 2:
                    moved.append((f, fitz.Rect(q.x0 - (r.x0 - f.x0), f.y0 + (q.y0 - r.y0),
                                               q.x1 + (f.x1 - r.x1), f.y1 + (q.y0 - r.y0))))
                    break
        # A link that did not follow a rule still points at the same words:
        # it moves as they did, measured the same way.
        for lk in links_before:
            f = fitz.Rect(lk["from"])
            if any(abs(o.x0 - f.x0) < 0.05 and abs(o.y0 - f.y0) < 0.05 for o, _ in moved):
                continue
            mv = _link_shift(lb, la, f)
            if mv is not None:
                moved.append((f, mv))
        _restore_links(page, links_before, moved)
        return a.tobytes(garbage=1, deflate=True)
    finally:
        b.close()
        a.close()


def _inside(r, field, text="", edit=None):
    """Is rule *r* the edited field's own decoration — within its box, over
    characters the edit rewrote? Over text both versions share at the field's
    start or end ("Guido" in "The designer of Python, Guido" -> "The des of
    Python, Guido") the characters are the same ones, pushed along, and their
    underline follows them."""
    if field is None:
        return False
    f = fitz.Rect(field)
    if not (f.x0 - 0.5 <= r.x0 and r.x1 <= f.x1 + 0.5 and f.y0 - 1 <= (r.y0 + r.y1) / 2 <= f.y1 + 3):
        return False
    if edit:
        old, new = edit[0].rstrip(), edit[1].rstrip()
        p = 0
        while p < min(len(old), len(new)) and old[p] == new[p]:
            p += 1
        q = 0
        while q < min(len(old), len(new)) - p and old[-1 - q] == new[-1 - q]:
            q += 1
        t = text.strip()
        if t and (t in old[:p] or (q and t in old[len(old) - q:])):
            return False
    return True


def stranded_underlines(before: bytes, after: bytes, pno: int, band, field=None,
                        edit=None) -> list:
    """Underlines in *band* of the page whose run of text no longer sits over
    them after an edit — each (run text, old rect). A detector: shared by the
    engines' own guard and the audit."""
    b = fitz.open(stream=before, filetype="pdf")
    a = fitz.open(stream=after, filetype="pdf")
    try:
        la = _lines(a[pno])
        rules = []
        for dr in a[pno].get_drawings():
            q = fitz.Rect(dr["rect"])
            if q.width >= 0.5 and q.height <= 1.5:
                rules.append(q)
        bad = []
        for r, text, _ in underlines(b[pno], band):
            if _inside(r, field, text, edit):
                continue
            run = _covered(_lines(b[pno]), r)
            ok = False
            for dr_q in rules:
                if abs(dr_q.height - r.height) > 0.2 or abs(dr_q.width - r.width) > 1.0:
                    continue
                got = _covered(la, dr_q)
                if got and "".join(c["c"] for c in got if c["c"].strip()) == \
                        "".join(ch for ch in text if ch.strip()):
                    ok = True
                    break
            if not ok:
                # A re-wrap may split the run over two lines, each piece under
                # its own rule: every word must still be underlined somewhere
                # near its old place.
                words = text.split()
                ok = bool(words)
                for w in words:
                    found = False
                    for l in la:
                        for cs in _find_runs(l, w):
                            if found:
                                break
                            for q in rules:
                                # against the run's OWN baseline: a superscript
                                # "[97]" sits above the line it is merged into
                                if q.x0 <= cs[0]["x0"] + 1.0 and q.x1 >= cs[-1]["x1"] - 1.0 and \
                                        cs[0]["oy"] - 0.2 * cs[0]["size"] <= (q.y0 + q.y1) / 2 <= \
                                        cs[0]["oy"] + 0.4 * cs[0]["size"] and \
                                        abs(l["y"] - run[0]["oy"]) < 3 * cs[0]["size"]:
                                    found = True
                                    break
                        if found:
                            break
                    ok = ok and found
            if not ok:
                bad.append((text, r))
        return bad
    finally:
        b.close()
        a.close()


def _decorations(page, para):
    """Thin rules drawn under runs of the paragraph's characters, each with
    the run it belongs to. None when anything else is drawn over the words."""
    boxes = []
    for l in para:
        size = l["chars"][0]["size"]
        boxes.append(fitz.Rect(l["chars"][0]["x0"], l["y"] - 0.8 * size,
                               l["chars"][-1]["x1"], l["y"] + 0.35 * size))
    whole = fitz.Rect(boxes[0])
    for b_ in boxes[1:]:
        whole |= b_
    out = []
    for dr in page.get_drawings():
        r = fitz.Rect(dr["rect"])
        if not any(r.intersects(b) or (r.height == 0 and b.y0 <= r.y0 <= b.y1
                                       and r.x0 < b.x1 and r.x1 > b.x0) for b in boxes):
            continue
        if r.contains(whole) and dr.get("fill") is not None:
            continue    # a panel BEHIND the whole paragraph (a caption's box); the
            #             re-set lines stay inside it — _verify checks the band
        size = para[0]["chars"][0]["size"]
        if r.height > max(0.12 * size, 1.2) or r.width < 0.5:
            return None
        kinds = {it[0] for it in dr["items"]}
        if not kinds <= {"l", "re", "qu"}:
            return None
        run = _covered(para, r)
        if not run or r.x0 < run[0]["x0"] - 1.0 or r.x1 > run[-1]["x1"] + 1.0:
            return None      # not an underline of one run (a border): can't follow
        out.append({"rect": r, "run": run, "fill": dr.get("fill"),
                    "color": dr.get("color") or (0, 0, 0), "width": dr.get("width") or 1.0})
    return out


def _para_links(page, para):
    """Link areas over the paragraph's text, each with the run it covers."""
    boxes = [fitz.Rect(l["bbox"]) for l in para]
    out = []
    for lk in page.get_links():
        r = fitz.Rect(lk["from"])
        if not any(r.intersects(b) for b in boxes):
            continue
        run = _covered(para, fitz.Rect(r.x0, (r.y0 + r.y1) / 2, r.x1, (r.y0 + r.y1) / 2))
        if not run:
            return None
        out.append({"link": lk, "rect": r, "run": run})
    return out


def _follow(mark, placed):
    """Where *mark* (an underline or link over a run of glyphs) goes after the
    re-wrap: one rectangle per new line its run lands on — a browser draws an
    underline, and a link its area, line by line — or None when any glyph of
    the run was edited away. Spaces at a piece's ends are not covered, as a
    browser does not underline the space a line wraps at."""
    run, r = mark["run"], mark["rect"]
    pos, skip = [], 0
    for c in run:
        k = (round(c["ox"], 2), round(c["oy"], 2))
        if k in placed:
            x, y, n = placed[k]
            pos.append((c, x, y))
            skip = n - 1
        elif skip > 0 and pos:                  # inside a ligature glyph
            c_, x_, y_ = pos[-1]
            pos.append((c, x_ + (c["ox"] - c_["ox"]), y_))
            skip -= 1
        elif not c["c"].strip():
            continue                            # the space a line now wraps at
        else:
            return None
    groups = []
    for c, x, y in pos:
        if groups and abs(groups[-1][-1][2] - y) < 0.01:
            groups[-1].append((c, x, y))
        else:
            groups.append([(c, x, y)])
    rects = []
    for g in groups:
        g_ = [t for t in g]
        while g_ and not g_[0][0]["c"].strip():
            g_.pop(0)
        while g_ and not g_[-1][0]["c"].strip():
            g_.pop()
        if not g_:
            continue
        (c0, x0, y0), (c1, x1, _) = g_[0], g_[-1]
        left = x0 + (c0["x0"] - c0["ox"]) + ((r.x0 - run[0]["x0"]) if c0 is run[0] else 0.0)
        right = x1 + (c1["x1"] - c1["ox"]) + ((r.x1 - run[-1]["x1"]) if c1 is run[-1] else 0.0)
        dyl = y0 - c0["oy"]
        rects.append(fitz.Rect(left, r.y0 + dyl, right, r.y1 + dyl))
    return rects or None

def _blank(line):
    return not "".join(c["c"] for c in line["chars"]).strip()


def _paragraph(lines, target_bbox):
    """(paragraph lines, leading) around the line holding *target_bbox*."""
    tb = fitz.Rect(target_bbox)
    idx = next((i for i, l in enumerate(lines)
                if l["bbox"].intersects(tb) and abs(l["bbox"].y0 - tb.y0) < 2.0), None)
    if idx is None:
        return None, None
    x0 = lines[idx]["x0"]
    # A blank line is a paragraph break: Word separates paragraphs with
    # empty lines at the same leading, which otherwise glued thirteen lines
    # of four paragraphs into one.
    col = [l for l in lines if abs(l["x0"] - x0) <= 0.6 or _blank(l)]
    k0 = col.index(lines[idx])
    lo = k0
    while lo - 1 >= 0 and not _blank(col[lo - 1]):
        lo -= 1
    hi = k0
    while hi + 1 < len(col) and not _blank(col[hi + 1]):
        hi += 1
    col = [l for l in col[lo:hi + 1] if abs(l["x0"] - x0) <= 0.6]
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
    """Advance widths in points, from the document's own fonts — simple fonts
    through /Widths, CID fonts (Chrome, Skia, Google Docs) through /W — and
    the producer's kerning, when it kerns (see kerning.py)."""

    def __init__(self, doc, page=None):
        self.doc = doc
        self.page = page
        self.cache: dict = {}

    def _type0_xref(self, name):
        for pno in range(self.doc.page_count):
            for f in self.doc[pno].get_fonts(full=True):
                if f[3].split("+")[-1] == name and f[2] == "Type0":
                    return f[0]
        return None

    def font(self, name):
        if name not in self.cache:
            t0 = self._type0_xref(name)
            if t0:
                refs = S._font_stream_refs(self.doc, t0)
                wmap, dw = {}, 1000.0
                if isinstance(refs, dict):
                    wmap = S._cid_widths_map(self.doc, refs["cid_xref"])
                    kind, val = self.doc.xref_get_key(refs["cid_xref"], "DW")
                    if kind in ("int", "float") and val:
                        dw = float(val)
                cm = S._lookup_by_name(S._cid_code_maps(self.doc), name)
                f = {"cid": True, "refs": refs, "wmap": wmap, "dw": dw, "cm": cm}
            else:
                refs = S._simple_font_refs(self.doc, name, require_truetype=False)
                parsed = S._parse_widths_array(self.doc, refs) if refs else None
                cm = S._lookup_by_name(S._simple_font_code_maps(self.doc), name)
                enc = S._lookup_by_name(S._simple_font_encodings(self.doc), name)
                f = {"cid": False, "refs": refs, "widths": parsed, "cm": cm, "enc": enc,
                     "b14": None}
                if not parsed:
                    # A standard-14 font (Helvetica, Times, Courier…) is not
                    # embedded and carries no /Widths: every viewer uses the
                    # same built-in metrics, and so does this.
                    try:
                        from pdf_editor import _base14_builtin
                        alias = _base14_builtin(name)
                        if alias:
                            f["b14"] = fitz.Font(alias)
                            f["enc"] = f["enc"] or "WinAnsiEncoding"
                    except Exception:  # noqa: BLE001
                        pass
            f["kern"] = self._kerning(name)
            self.cache[name] = f
        return self.cache[name]

    def _kerning(self, name):
        if self.page is None:
            return None
        try:
            import kerning
            from pdf_editor import resolve_full_font
            k = kerning.font_kern(resolve_full_font(name))
            if k and kerning.producer_kerns(self.page, name, k,
                                            self.doc.metadata.get("producer", "")):
                return k
        except Exception:  # noqa: BLE001 — no kerning is the safe default
            pass
        return None

    def reset(self):
        self.cache.clear()

    def code(self, name, ch):
        f = self.font(name)
        if f["cm"]:
            codes = S._encode_simple_text(ch, f["cm"]["rev"], f["cm"]["max_len"])
            if codes:
                return codes[0]
        if f["cid"]:
            return None
        # A code with no /ToUnicode entry extracts as ITSELF: MuPDF passes the
        # raw byte through as a C0 control. Ghostscript's /ebook redistill
        # leaves the "fi" of "certifie" as exactly that (0x19), and the
        # paragraph could not be measured or re-set. The code is the byte;
        # re-emitting it draws the same glyph and extracts the same way.
        if len(ch) == 1 and ord(ch) < 32 and ch not in " \t\n\r":
            return ord(ch)
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
        if code is None:
            return None
        if f["cid"]:
            return f["wmap"].get(code, f["dw"]) * size / 1000.0
        if not f["widths"]:
            if f.get("b14") is not None and len(ch) == 1:
                return f["b14"].glyph_advance(ord(ch)) * size
            return None
        first, ws = f["widths"]
        if not 0 <= code - first < len(ws):
            return None
        return ws[code - first] * size / 1000.0

    def kern(self, a, b):
        """Kerning (points) between units *a* and *b*, if the producer kerns."""
        if a["font"] != b["font"] or not a["t"] or not b["t"]:
            return 0.0
        k = self.font(a["font"])["kern"]
        if k is None:
            return 0.0
        return k.char_pair(a["t"][-1], b["t"][0]) * a["size"] / k.upem


_LIGATURES = {"ff": "\ufb00", "fi": "\ufb01", "fl": "\ufb02", "ffi": "\ufb03",
              "ffl": "\ufb04", "st": "\ufb06"}


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
            for L in range(min(max(cm["max_len"], 3), len(chars) - i), 1, -1):
                seg = chars[i:i + L]
                if any(x["font"] != c["font"] or abs(x["size"] - c["size"]) > 0.01 * c["size"]
                       for x in seg):
                    continue
                txt = "".join(x["c"] for x in seg)
                # The ligature may be named by its letters ("fi", LibreOffice)
                # or by its own codepoint (U+FB01, Chrome) — which extraction
                # expands back into 'f' and a zero-width 'i'.
                code = cm["rev"].get(txt)
                actual = None
                if code is None and txt in _LIGATURES:
                    code = cm["rev"].get(_LIGATURES[txt])
                    actual = txt
                if code is not None:
                    took = (L, code, actual)
                    break
        if took is None:
            took = (1, m.code(c["font"], c["c"]), None)
        L, code, actual = took
        # `actual`: the glyph's /ToUnicode names a ligature CODEPOINT, so the
        # producer wrapped it in /ActualText to make it extract as letters;
        # the emitted line must do the same or "certifie" reads "certiﬁe".
        out.append({"t": "".join(x["c"] for x in chars[i:i + L]), "code": code,
                    "font": c["font"], "size": c["size"], "color": c["color"],
                    "ox": c.get("ox"), "oy": c.get("oy"), "actual": actual})
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


def reflow(pdf_bytes: bytes, span: dict, new_text: str, multiline_only: bool = False,
           also=()) -> dict:
    """Replace *span*'s text with *new_text*, re-wrapping its paragraph.

    *also* is more (span, new_text) edits in the SAME paragraph, re-wrapped
    together: a phrase that wraps ("…by Atlas Consulting / SARL for…") is
    changed by editing both lines, and only one re-wrap over both edits can
    pull up the words the producer pulls up. Every one must lie in the
    paragraph, or the whole call is refused.

    Returns {"ok": True, "pdf": bytes, "lines": n_before -> n_after} or a
    refusal dict with a reason.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return _refuse("unreadable", "The PDF could not be opened.")
    try:
        return _reflow(doc, span, new_text, multiline_only, tuple(also))
    finally:
        doc.close()


def same_paragraph(pdf_bytes: bytes, span: dict, others) -> list:
    """Which of *others* (span dicts) lie in *span*'s paragraph, as the
    re-wrap itself reads paragraphs."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return []
    try:
        pno = span.get("page", 0)
        para, _ = _paragraph(_lines(doc[pno]), span["bbox"])
        if not para:
            return []
        out = []
        for o in others:
            if o.get("page", 0) != pno:
                continue
            for l in para:
                c0 = l["chars"][0]
                if abs(c0["oy"] - o["origin"][1]) <= 0.6 and \
                        c0["ox"] - 0.6 <= o["origin"][0] <= l["chars"][-1]["ox"] + 0.6:
                    out.append(o)
                    break
        return out
    finally:
        doc.close()


def _reflow(doc, span, new_text, multiline_only=False, also=()):
    pno = span.get("page", 0)
    page = doc[pno]
    if page.rotation or page.mediabox.x0 or page.mediabox.y0 \
            or page.cropbox != page.mediabox:
        return _refuse("page_geometry", "This page is rotated or offset.")
    lines = _lines(page)
    para, lead = _paragraph(lines, span["bbox"])
    if not para:
        return _refuse("no_paragraph", "The field's line could not be found.")
    if multiline_only and len(para) < 2:
        # Asked only whether a re-wrap would CHANGE an edit that already fit:
        # a one-line paragraph whose new text fit its line has nothing to
        # re-break. Answering it the long way (searching the page for a
        # margin to borrow) made every IRS 1040 edit five times slower.
        return _refuse("single_line", "One line: nothing to re-break.")
    m = _Metrics(doc, page)

    def width(units):
        tot = 0.0
        for i, u in enumerate(units):
            a = m.advance(u["font"], u["size"], u["t"], u["code"])
            if a is None:
                raise KeyError(u["t"])
            tot += a
            if i + 1 < len(units):
                tot += m.kern(u, units[i + 1])
        return tot

    # The stream of the whole paragraph, lines joined. A line that does not
    # end in a space was broken at one the producer did not draw.
    stream = []
    for i, l in enumerate(para):
        stream += l["chars"]
        if i < len(para) - 1 and l["chars"][-1]["c"] not in (" ", "-", "­"):
            last = dict(l["chars"][-1])
            last["c"] = " "
            last["ox"] = last["oy"] = None     # drawn by no one: nothing to follow
            stream.append(last)
    # Sizes agree to 1%: Word reports one line of a 14.04pt paragraph as
    # 14.064pt, from a scale folded into its text matrix.
    for c in stream:
        if abs(c["size"] - stream[0]["size"]) > 0.01 * stream[0]["size"]:
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

    # ── justified? Measured from the glyphs themselves: each full line's
    # spaces stretched by one uniform amount, the last line natural. The
    # stretch is part of the model the self-check below must reproduce.
    def _stretch(l):
        """(extra per inner space, number of inner spaces) — or extra 0.0 when
        the spaces are NOT uniformly changed. Justifying or squeezing changes
        every space by the same amount; LibreOffice's rounding of glyphs to
        its grid drifts unevenly by a tenth of a point or two, and reading
        that noise as a squeeze moved "Madame" to the next line."""
        us = _units(l["chars"], m)
        vis = [i for i, u in enumerate(us) if u["t"].strip()]
        if not vis:
            return 0.0, 0
        last = vis[-1]
        nat, n_inner = l["x0"], 0
        per_space, prev_dev = [], 0.0
        for i in range(last + 1):
            if us[i]["t"].strip():
                dev = us[i]["ox"] - nat
                if i and not us[i - 1]["t"].strip():
                    per_space.append(dev - prev_dev)
                prev_dev = dev
            if i < last and not us[i]["t"].strip():
                n_inner += 1
            if i < last:
                nat += m.advance(us[i]["font"], us[i]["size"], us[i]["t"], us[i]["code"])
                nat += m.kern(us[i], us[i + 1])
        actual = us[last]["ox"]
        extra = (actual - nat) / n_inner if n_inner else 0.0
        # Uniform RELATIVE to the gap: LibreOffice justifies with per-space
        # gaps of 0.77-3.43pt carrying 0.06-0.08pt of its own rounding, while
        # its ragged letter's "gap" is a few hundredths buried in 0.2pt of it.
        if per_space and (max(per_space) - min(per_space)) > 0.03 + 0.1 * abs(extra):
            return 0.0, n_inner          # uneven: rounding, not justification
        return extra, n_inner

    try:
        stretches = [_stretch(l) for l in para]
    except TypeError:
        return _refuse("unmeasurable", "A glyph's width is not in the font's own table.")
    # Three behaviours, told apart by the original's own spacing:
    #   ragged     — every line natural (LibreOffice, Chrome);
    #   justified  — every full line STRETCHED to the margin (fpdf2);
    #   squeeze    — lines natural, except one that would overshoot the margin
    #                is SQUEEZED onto it (ReportLab: -0.087pt a space); a new
    #                line that fits stays ragged. Stretching those to the margin
    #                was wrong against ReportLab's own re-print.
    full = [(e, n) for e, n in stretches[:-1] if n]
    last_natural = abs(stretches[-1][0]) < 0.05
    justified = (len(para) >= 2 and full and last_natural
                 and all(e > 0.02 for e, n in full))
    squeeze = (len(para) >= 2 and full and last_natural and not justified
               and all(e <= 0.02 for e, n in full) and any(e < -0.02 for e, n in full))
    margin = None
    if justified or squeeze:
        ends = []
        for (e, n), l in zip(stretches[:-1], para[:-1]):
            if justified or e < -0.02:          # squeeze: only the squeezed lines
                vis = [c for c in l["chars"] if c["c"].strip()]
                ends.append(vis[-1]["x1"])
        if not ends or max(ends) - min(ends) > 0.5:
            justified = squeeze = False
        else:
            margin = sum(ends) / len(ends)

    band = {}

    def established(p_lines):
        pst = []
        for i, l in enumerate(p_lines):
            pst += l["chars"]
            if i < len(p_lines) - 1 and l["chars"][-1]["c"] not in (" ", "-", "\u00ad"):
                pst.append(dict(l["chars"][-1], c=" "))
        want = ["".join(c["c"] for c in l["chars"]).rstrip() for l in p_lines]
        ws = _words(_units(pst, m))
        # A justifying producer may also break a line that overshoots the
        # margin a little at natural spacing and SQUEEZE it (ReportLab: 1.3pt
        # over, -0.087pt per space). The largest overshoot the paragraph's own
        # lines show is a candidate too; the break check below decides.
        over = 0.0
        if justified or squeeze:
            for (e, n) in stretches[:-1]:
                if n and e < 0:
                    over = max(over, -e * n)
        # LibreOffice draws each justified line's trailing space, and the line
        # box INCLUDING it ends on the text-area edge; a word fits if it and a
        # space fit there. Several limits can reproduce the original breaks,
        # so this structural one goes first when the lines show it: without
        # it a shorter name pulled one word fewer up than LibreOffice does.
        struct = []
        if justified:
            tails = [l for l in para[:-1] if l["chars"][-1]["c"] == " "]
            if tails and len(tails) == len(para) - 1:
                edge = [l["chars"][-1]["x1"] for l in tails]
                if max(edge) - min(edge) <= 0.3:
                    sp = tails[0]["chars"][-1]
                    sw = m.advance(sp["font"], sp["size"], " ")
                    if sw:
                        struct = [sum(edge) / len(edge) - sw]
        cands = ((*struct, margin, margin + over + 0.01) if (justified or squeeze) else
                 (page.rect.width - x0, max(l["chars"][-1]["x1"] for l in lines)))
        for cand in cands:
            if [_text(g).rstrip() for g in _break(ws, x0, cand, width)] == want:
                return cand
        if justified or squeeze:
            return None
        # A ragged paragraph set to a margin that is neither the page's edge
        # nor any line's end (ReportLab's Frame: 524pt on a 595pt page). The
        # original's breaks pin that margin to an interval: every line fits
        # within it, and every break was FORCED — the next word would have
        # overshot it. Hard line breaks (an address block) leave room for the
        # next word, so their interval is empty and they are never merged.
        # Any margin in the interval reproduces the original; the re-wrap is
        # then accepted only if it comes out the same at BOTH ends of it.
        groups, cur = [], []
        for w in ws:
            cur.append(w)
            if len(groups) < len(want) and _text(cur).rstrip() == want[len(groups)]:
                groups.append(cur)
                cur = []
        if cur or len(groups) != len(want) or len(groups) < 2:
            return None

        def strip(w):
            return w[:-1] if w and w[-1]["t"] == " " else w
        lo = max(x0 + sum(width(w) for w in g[:-1]) + width(strip(g[-1])) for g in groups)
        hi = min(x0 + sum(width(w) for w in g) + width(strip(nx[0]))
                 for g, nx in zip(groups, groups[1:]))
        a_, b_ = lo - 0.05 + 0.001, hi - 0.05 - 0.001
        if a_ >= b_:
            return None
        for cand in (a_, b_):
            if [_text(g).rstrip() for g in _break(ws, x0, cand, width)] != want:
                return None
        band["hi"] = b_
        return a_

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
    for li, l in enumerate(para):
        x = x0
        us = _units(l["chars"], m)
        extra = stretches[li][0] if (justified or squeeze) and li < len(para) - 1 else 0.0
        for i, u in enumerate(us):
            # A space's own origin is not judged: a producer may put the
            # justification gap before the space glyph (fpdf2) or after it,
            # and every VISIBLE glyph lands in the same place either way.
            if u["t"].strip() and abs(u["ox"] - x) > _POS_TOL:
                return _refuse("unknown_layout",
                               "This paragraph's glyph positions don't follow the font's "
                               "own advances (kerning or justification), so it can't be "
                               "re-set exactly.")
            x += m.advance(u["font"], u["size"], u["t"], u["code"])
            if not u["t"].strip():
                x += extra
            if i + 1 < len(us):
                x += m.kern(u, us[i + 1])

    # ── the edits, in the stream ──
    joined = "".join(c["c"] for c in stream)
    edits = []
    for sp, nt in ((span, new_text),) + tuple(also):
        old = sp["text"]
        at = None
        for mt in re.finditer(re.escape(old), joined):
            ch = stream[mt.start()]
            if abs(ch["ox"] - sp["origin"][0]) <= 0.6 and abs(ch["oy"] - sp["origin"][1]) <= 0.6:
                at = mt.start()
                break
        if at is None:
            return _refuse("not_in_paragraph", "The field could not be located in its paragraph.")
        edits.append((at, len(old), nt))
    edits.sort()
    if any(a + n > b for (a, n, _), (b, _, _) in zip(edits, edits[1:])):
        return _refuse("overlapping_edits", "Two edits cover the same text.")
    stream2 = list(stream)
    for at, n, nt in reversed(edits):          # right to left: offsets stay valid
        # Only what differs is new: the unchanged head and tail keep their
        # own glyphs — their style, and their identity, which an underline or
        # link over them follows to their new place.
        ot = "".join(c["c"] for c in stream[at:at + n])
        p_ = 0
        while p_ < min(n, len(nt)) and ot[p_] == nt[p_]:
            p_ += 1
        q_ = 0
        while q_ < min(n, len(nt)) - p_ and ot[n - 1 - q_] == nt[len(nt) - 1 - q_]:
            q_ += 1
        style = stream[at + min(p_, n - 1)] if n else stream[at]
        mid = [dict(style, c=c, ox=None, oy=None) for c in nt[p_:len(nt) - q_]]
        stream2[at:at + n] = stream[at:at + p_] + mid + stream[at + n - q_:at + n]

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
        f = m.font(fname)
        if f["cid"]:
            gids, why = S._try_extend(doc, fname, chars)
            if gids is None:
                return _refuse("missing_glyph", f"Couldn't add {chars}: {why}.")
            m.reset()
            continue
        refs = S._simple_font_refs(doc, fname)
        if not refs or not f["cm"] or not S._private_code_font(doc, refs):
            return _refuse("missing_glyph", f"The font lacks {chars} and can't be extended here.")
        alloc = S._allocate_private_codes(doc, refs, f["cm"]["rev"], chars)
        if not alloc:
            return _refuse("missing_glyph", f"No free codes for {chars}.")
        gids, why = S._try_extend_simple(doc, fname, chars, code_for=alloc)
        if gids is None:
            return _refuse("missing_glyph", f"Couldn't add {chars}: {why}.")
        m.reset()
        # The ToUnicode map now names the new codes; re-read it.
    try:
        new_lines = _break(_words(_units(stream2, m)), x0, limit, width)
        if "hi" in band and [_text(g) for g in _break(_words(_units(stream2, m)), x0,
                                                        band["hi"], width)] != \
                [_text(g) for g in new_lines]:
            return _refuse("ambiguous_margin",
                           "The original's line breaks allow more than one margin, and "
                           "the new text would wrap differently under them.")
    except KeyError as e:
        return _refuse("unmeasurable", f"No width for {e.args[0]!r}.")

    # Whether a wrapped line DRAWS its final space is the producer's own
    # convention: LibreOffice draws it, Chrome does not. Learned from the
    # paragraph's own wrapped lines (or kept, with nothing to learn from).
    if len(para) >= 2 and not any(l["chars"][-1]["c"] == " " for l in para[:-1]):
        for nl in new_lines[:-1]:
            while nl and nl[-1] and nl[-1][-1]["t"] == " ":
                nl[-1] = nl[-1][:-1]
                if not nl[-1]:
                    nl.pop()

    # Lines gained push what follows down; lines LOST pull it up, as the
    # producer does: a shorter name that took a LibreOffice paragraph from 4
    # lines to 3 moved the next paragraph up one leading in its own re-print,
    # where leaving the gap was a visible hole.
    grow = len(new_lines) - len(para)
    dy = grow * lead

    # ── what the page must not have below the cut, if anything moves ──
    cut = para[-1]["bbox"].y1 + 0.5
    if dy:
        # Only a single-column flow can be pushed down wholesale. Text below
        # the paragraph outside its column (x0..limit) belongs to another
        # column, which a word processor would not move.
        for l in lines:
            if l["bbox"].y0 >= cut and (l["bbox"].x0 > limit + 1.0 or l["bbox"].x1 < x0 - 1.0):
                return _refuse("multi_column",
                               "Re-wrapping adds a line, and the page has another column "
                               "beside this one that must not move.")
        if any(fitz.Rect(lk["from"]).y0 > cut - 1 for lk in page.get_links()):
            return _refuse("links_below", "Links below this paragraph would have to move.")
        if any(True for _ in page.widgets()):
            return _refuse("form_fields", "This page has form fields.")
        for dr in page.get_drawings():
            r = dr["rect"]
            if r.y0 < cut < r.y1:
                return _refuse("crosses_cut", "A rule or box spans the paragraph's end.")
        if dy < 0:
            # What follows slides UP into the band the paragraph gave back;
            # anything else in that band would end up underneath it.
            band = fitz.Rect(page.rect.x0, cut + dy, page.rect.x1, cut)
            para_ys = {round(l["y"], 1) for l in para}
            for l in lines:
                if round(l["y"], 1) in para_ys and abs(l["x0"] - x0) <= 0.6:
                    continue
                if l["bbox"].intersects(band):
                    return _refuse("band_occupied",
                                   "The paragraph gets shorter, and something beside it "
                                   "would be covered when what follows moves up.")
            for dr in page.get_drawings():
                if dr["rect"].intersects(band) and dr["rect"].y1 <= cut:
                    return _refuse("band_occupied",
                                   "The paragraph gets shorter, and a rule or box beside it "
                                   "would be covered when what follows moves up.")
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

    # ── decorations drawn over the paragraph's words ──
    # A link underline is line art, not text: deleting and re-setting the
    # words left Wikipedia's caption underlines where "Guido", "Rossum" and
    # "PyCon" USED to be, under the wrong letters. Each thin rule under a run
    # of characters follows that run to its new place; anything else drawn
    # over the words (a highlight, a box) — or a rule whose run is edited or
    # split by the new breaks — and the paragraph is refused.
    decs = _decorations(page, para)
    if decs is None:
        return _refuse("decorated", "Something is drawn over this paragraph's words (a "
                                    "highlight or box) that can't follow them to new lines.")
    links = _para_links(page, para)
    if links is None:
        return _refuse("decorated", "A link here covers text that can't be followed "
                                    "to its new place.")

    # The page's font resources BEFORE anything is deleted: when the
    # paragraph is the only text in a font, the redaction prunes that font
    # from /Resources, and there is then nothing to set the new lines in.
    fonts_before = {f[4]: (f[0], f[3].split("+")[-1]) for f in page.get_fonts(full=True)
                    if f[4]}

    links_before = page.get_links()

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
    if decs:
        for d_ in decs:
            page.add_redact_annot(d_["rect"] + (-0.3, -0.3, 0.3, 0.3), cross_out=False, fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=fitz.PDF_REDACT_TEXT_NONE)
        for dr in page.get_drawings():
            if any(abs(dr["rect"].x0 - d_["rect"].x0) < 0.1 and abs(dr["rect"].y0 - d_["rect"].y0) < 0.1
                   and abs(dr["rect"].x1 - d_["rect"].x1) < 0.1 for d_ in decs):
                return _refuse("delete_failed", "An underline could not be removed cleanly.")

    # ── emit the new lines through the page's own font resources ──
    have = {f[4] for f in page.get_fonts(full=True)}
    for res, (xref, _nm) in fonts_before.items():
        if res in have:
            continue
        # /Font may be inline in /Resources, or an indirect object of its own
        # (fpdf2) — and /Resources itself may be indirect. Restore wherever it
        # lives; a missing font makes a viewer fall back to StandardEncoding,
        # which drew "é" as "Ø".
        kind, val = doc.xref_get_key(page.xref, "Resources")
        holder = int(val.split()[0]) if kind == "xref" else page.xref
        prefix = "" if kind == "xref" else "Resources/"
        fk, fv = doc.xref_get_key(holder, prefix + "Font")
        if fk == "xref":
            doc.xref_set_key(int(fv.split()[0]), res, f"{xref} 0 R")
        else:
            doc.xref_set_key(holder, f"{prefix}Font/{res}", f"{xref} 0 R")
    refmap = {nm: res for res, (xref, nm) in fonts_before.items()}
    refmap.update({v: k for k, v in S._page_font_refmap(page).items()})
    ops = []
    placed = {}          # original glyph origin -> (new x, new baseline)
    y0 = para[0]["y"]
    for i, line in enumerate(new_lines):
        y = y0 + i * lead
        x = x0
        run, run_style = [], None
        chars = [u for w in line for u in w]
        # A justified paragraph's full lines end on the margin: their inner
        # spaces take the slack, as the producer set them. The last line,
        # and every line of a ragged paragraph, is set natural.
        line_extra = 0.0
        if (justified or squeeze) and i < len(new_lines) - 1:
            vis = [k for k, u in enumerate(chars) if u["t"].strip()]
            if vis:
                inner = [k for k in range(vis[-1]) if not chars[k]["t"].strip()]
                if inner:
                    line_extra = (margin - (x0 + width(chars[:vis[-1] + 1]))) / len(inner)
                    if squeeze:
                        line_extra = min(0.0, line_extra)   # only an overshoot
                    last_vis = vis[-1]

        def flush():
            if not run:
                return
            f, size, color = run_style
            res = refmap.get(f)
            if res is None:
                raise KeyError(f)
            rgb = ((color >> 16) & 255, (color >> 8) & 255, color & 255)
            fmt = "%04X" if m.font(f)["cid"] else "%02X"
            shows, parts, cur, lead = [], [], "", None
            for cd, adj, act in zip(run[1], run[2], run[3]):
                if act:
                    if cur:
                        parts.append("<%s>" % cur)
                        cur = ""
                    if parts:
                        shows.append("[%s] TJ" % " ".join(parts))
                        parts = []
                    shows.append("/Span <</ActualText (%s)>> BDC <%s> Tj EMC" % (act, fmt % cd))
                    if adj:
                        parts.append("%g" % adj)
                    continue
                cur += fmt % cd
                if adj:
                    parts.append("<%s> %g" % (cur, adj))
                    cur = ""
            if cur:
                parts.append("<%s>" % cur)
            if parts:
                shows.append("[%s] TJ" % " ".join(parts))
            ops.append("BT /%s %.4g Tf %.4g %.4g %.4g rg 1 0 0 1 %.3f %.3f Tm %s ET"
                       % (res, size, rgb[0] / 255, rgb[1] / 255, rgb[2] / 255,
                          run[0], page.rect.height - y, " ".join(shows)))

        for i, c in enumerate(chars):
            st = (c["font"], c["size"], c["color"])
            if st != run_style:
                flush()
                run = [x, [], [], []]
                run_style = st
            run[1].append(c["code"])
            if c.get("ox") is not None:
                placed[(round(c["ox"], 2), round(c["oy"], 2))] = (x, y, len(c["t"]))
            x += m.advance(c["font"], c["size"], c["t"], c["code"])
            kp = m.kern(c, chars[i + 1]) if i + 1 < len(chars) else 0.0
            if line_extra and not c["t"].strip() and i < last_vis:
                kp += line_extra
            x += kp
            # TJ units: thousandths of the font size, positive moves left.
            run[2].append(round(-kp / c["size"] * 1000.0, 3) if kp else 0)
            run[3].append(c.get("actual"))
        flush()
    H = page.rect.height
    for d_ in decs or ():
        rs = _follow(d_, placed)
        if rs is None:
            return _refuse("decorated", "An underlined phrase would be changed by the re-wrap.")
        for r in rs:
            if d_["fill"] is not None:
                ops.append("q %.4g %.4g %.4g rg %.3f %.3f %.3f %.3f re f Q"
                           % (*d_["fill"], r.x0, H - r.y1, r.width, r.height))
            else:
                ym = H - (r.y0 + r.y1) / 2
                ops.append("q %.4g %.4g %.4g RG %.3f w %.3f %.3f m %.3f %.3f l S Q"
                           % (*d_["color"], d_["width"], r.x0, ym, r.x1, ym))
    moved_links = []
    for lk in links or ():
        rs = _follow(lk, placed)
        if rs is None:
            return _refuse("decorated", "A linked phrase would be changed by the re-wrap.")
        moved_links.append((lk["link"], rs))
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
    below = fitz.Rect(full.x0, cut, full.x1, min(full.y1, full.y1 - dy))
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
    _restore_links(page, links_before, [(fitz.Rect(lk["from"]), rs) for lk, rs in moved_links])

    out = doc.tobytes(garbage=3, deflate=True)
    ok = _verify(out, pno, para, new_lines, lead, cut, dy, lines)
    if ok is not True:
        return _refuse("verify_failed", ok)
    # Did the breaks change? If every line but the edited one reads as before,
    # an in-place edit would have produced the same layout; the caller may
    # then prefer it. If not, only a re-wrap matches what the producer does.
    before_lines = ["".join(c["c"] for c in l["chars"]).rstrip() for l in para]
    expect = list(before_lines)
    for sp, nt in ((span, new_text),) + tuple(also):
        for i, t in enumerate(expect):
            if abs(para[i]["chars"][0]["oy"] - sp["origin"][1]) <= 0.6 and sp["text"].rstrip() in t:
                expect[i] = t.replace(sp["text"].rstrip(), nt.rstrip(), 1)
                break
    changed = [_text(nl).rstrip() for nl in new_lines] != expect
    return {"ok": True, "pdf": out, "lines": (len(para), len(new_lines)), "shift": dy,
            "cut": cut, "top": para[0]["bbox"].y0, "breaks_changed": changed,
            "justified": bool(justified or squeeze)}


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
