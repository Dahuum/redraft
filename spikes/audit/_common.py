"""Shared detectors for the audits. Read the rendered output, never the report.

Deliberately small: every function here answers one question, and each was
wrong at least once before it was right (see README).
"""
import io
import os
import sys

BACKEND = os.environ.get(
    "RD_BACKEND",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
sys.path.insert(0, BACKEND)

import fitz  # noqa: E402

CORPUS = os.environ.get(
    "RD_CORPUS", os.path.join(os.path.expanduser("~"), ".cache", "redraft-audit", "corpus"))
EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples")
API = os.environ.get("RD_API", "http://localhost:8000")
HARD = ("cannot_render", "runs_off_the_page", "cannot_place", "overlaps_neighbour",
        "moves_column", "invisible_text", "crowds_neighbour")

# The composer sets real ligatures, and PDFs are full of typographic
# lookalikes. Comparing raw characters calls correct output "lost text".
_FOLD = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
         " ": " ", " ": " ", "’": "'", "‘": "'", "­": "-",
         "‐": "-", "‑": "-"}


def fold(t: str) -> str:
    for k, v in _FOLD.items():
        t = t.replace(k, v)
    return t


def norm_ws(t: str) -> str:
    return " ".join(fold(t).split())


def notdef_glyphs(doc, page: int, probe: str) -> list:
    """Characters the RENDERER resolves to glyph 0 (.notdef) in a run matching *probe*.

    The only honest answer to "will this draw". Reading the text cannot give it
    — /ToUnicode reports the right characters even when the viewer paints a box
    — and reading the font's cmap is the wrong question for a simple font,
    which renders by code through /Encoding /Differences and need not have a
    cmap entry for an injected glyph at all.
    """
    bad = []
    for sp in doc[page].get_texttrace():
        try:
            txt = "".join(chr(c[0]) for c in sp["chars"])
        except Exception:  # noqa: BLE001 — exotic span, nothing to say about it
            continue
        if probe and probe not in txt:
            continue
        for c in sp["chars"]:
            ch = chr(c[0])
            if c[1] == 0 and not ch.isspace():
                bad.append(ch)
    return sorted(set(bad))


def line_gaps(boxes, tol: float = 2.0) -> dict:
    """Smallest gap between horizontally adjacent runs, per baseline band."""
    lines = {}
    for b in boxes:
        lines.setdefault(round(b[3] / tol), []).append(b)
    out = {}
    for k, bs in lines.items():
        bs.sort(key=lambda r: r[0])
        g = min((bs[i][0] - bs[i - 1][2] for i in range(1, len(bs))), default=None)
        if g is not None:
            out[k] = g
    return out


def page_facts(blob: bytes, page: int) -> dict:
    """What a page IS, so a later reading can be compared against it.

    Every check must be a delta: Ghostscript's /ebook distill leaves a 0x1e in
    the file before anything is edited, and documents legitimately set text
    past their own media box.
    """
    d = fitz.open(stream=blob, filetype="pdf")
    try:
        pg = d[page]
        boxes, nd, right, left, bottom = [], 0, 0.0, 1e9, 0.0
        for blk in pg.get_text("rawdict")["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    for c in sp["chars"]:
                        ch = c["c"]
                        if ch == "�" or (ch and ord(ch) < 32 and ch not in " \t\n\r"):
                            nd += 1
                    if any(c["c"].strip() for c in sp["chars"]):
                        bb = sp["bbox"]
                        boxes.append(bb)
                        right = max(right, bb[2]); left = min(left, bb[0]); bottom = max(bottom, bb[3])
        return {"notdef": nd, "right": right, "left": left, "bottom": bottom,
                "gaps": line_gaps(boxes), "text": norm_ws(pg.get_text()),
                "w": pg.rect.width, "h": pg.rect.height}
    finally:
        d.close()


def output_defects(out_pdf: bytes, page: int, new_text: str, base: dict) -> list:
    """Everything a user would call a problem, as a delta against *base*."""
    found = []
    after = page_facts(out_pdf, page)
    if after["notdef"] > base["notdef"]:
        found.append(f"{after['notdef'] - base['notdef']} new notdef")
    if after["right"] > base["w"] + 0.5 and after["right"] > base["right"] + 0.5:
        found.append(f"x={after['right']:.0f}>page {base['w']:.0f}")
    if after["left"] < -0.5 and after["left"] < base["left"] - 0.5:
        found.append(f"x={after['left']:.0f}<0")
    if after["bottom"] > base["h"] + 0.5 and after["bottom"] > base["bottom"] + 0.5:
        found.append(f"y={after['bottom']:.0f}>page {base['h']:.0f}")
    for k, g in after["gaps"].items():
        b0 = base["gaps"].get(k)
        if b0 is not None and b0 >= -0.2 and g < -1.0:
            found.append(f"overdraw {g:.0f}pt")
            break
    probe = norm_ws(new_text.strip())[:18]
    if probe and probe not in after["text"]:
        found.append("text not readable back")
    d = fitz.open(stream=out_pdf, filetype="pdf")
    try:
        bad = notdef_glyphs(d, page, norm_ws(new_text.strip())[:6])
        if bad:
            found.append(f"renders .notdef for {bad[:4]}")
    finally:
        d.close()
    return found


def edit_tells(before_pdf: bytes, out_pdf: bytes, page: int, sd: dict, new: str,
               redrawn: bool = True) -> list:
    """What a reader would notice about the edited text itself, judged on the rendering.

    The other checks ask whether the page is intact. This asks whether the NEW TEXT looks like
    the old: (1) size — the redraw shrinks a value that does not fit, and a field set 40%
    smaller than its neighbours is a tell; (2) typeface — a word the edit left alone must keep
    its width at the same size, or a different face was drawn; (3) ruling — the new text must
    not sit across a table line the old text did not touch.
    """
    found = []
    db = fitz.open(stream=before_pdf, filetype="pdf")
    da = fitz.open(stream=out_pdf, filetype="pdf")
    try:
        acc = getattr(fitz, "TEXT_ACCURATE_BBOXES", 0)
        x0, y0, x1, y1 = sd["bbox"]

        def band(w):
            return min(w[3], y1 + 2) - max(w[1], y0 - 2) > 0.5 * (w[3] - w[1])

        wb = [w for w in db[page].get_text("words", flags=acc) if band(w)]
        wa = [w for w in da[page].get_text("words", flags=acc) if band(w)]
        old_words = {w[4]: w for w in wb if w[2] > x0 - 1 and w[0] < x1 + 1}
        keep = []
        for t, o in old_words.items():
            if len(t) < 3:
                continue
            near = [w for w in wa if w[4] == t and abs(w[0] - o[0]) < 0.6 * max(60.0, x1 - x0) + 40]
            if near:
                keep.append((o, min(near, key=lambda w: abs(w[0] - o[0]) + abs(w[1] - o[1]))))
        # (1)+(2) a word the edit left alone, before vs after
        for o, w in keep[:2]:
            rb, ra = o[2] - o[0], w[2] - w[0]
            hb, ha = o[3] - o[1], w[3] - w[1]
            if rb > 4 and hb > 2 and ha > 0:
                k = (ra / rb) / (ha / hb)                        # width at equal height
                if redrawn and abs(k - 1.0) > 0.05:      # an in-place edit keeps its font by construction
                    found.append("typeface/tracking of %r changed %+.0f%%" % (w[4][:12], (k - 1) * 100))
                    break
                if ha / hb < 0.85:
                    found.append("text shrunk to %.0f%% of its size" % (100 * ha / hb))
                    break
        # (3) ruling across the new text
        fresh = [w for w in wa if not any(abs(w[0] - v[0]) < 0.6 and abs(w[1] - v[1]) < 0.6 and w[4] == v[4]
                                          for v in wb)]
        if fresh:
            r = fitz.Rect(min(w[0] for w in fresh), min(w[1] for w in fresh),
                          max(w[2] for w in fresh), max(w[3] for w in fresh))
            inner = fitz.Rect(r.x0, r.y0 + 0.2 * r.height, r.x1, r.y1 - 0.2 * r.height)

            def lines(doc):
                out = []
                for d in doc[page].get_drawings():
                    for it in d["items"]:
                        if it[0] == "l":
                            a, b = it[1], it[2]
                            rr = fitz.Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
                        elif it[0] == "re":
                            rr = fitz.Rect(it[1])
                        else:
                            continue
                        if (rr.width < 1.6 and rr.height > 4) or (rr.height < 1.6 and rr.width > 4):
                            rr = fitz.Rect(rr.x0 - 0.2, rr.y0 - 0.2, rr.x1 + 0.2, rr.y1 + 0.2)   # zero-width lines
                            out.append(rr)
                return out
            was = {tuple(round(v) for v in b) for b in lines(db) if b.intersects(fitz.Rect(x0 - 3, y0 - 3, x1 + 3, y1 + 3))}
            for b in lines(da):
                if b.intersects(inner) and tuple(round(v) for v in b) not in was:
                    found.append("new text crosses a rule at x=%.0f,y=%.0f" % (b.x0, b.y0))
                    break
    finally:
        db.close()
        da.close()
    return found


def stranded(before_pdf: bytes, out_pdf: bytes, page: int, field=None, edit=None) -> list:
    """Underlines left behind: the words a rule underlined are still on the
    page but no longer over it (the Wikipedia caption: "Guido" slid left and
    its link underline stayed, under "o" and blank paper)."""
    import reflow
    bad = reflow.stranded_underlines(before_pdf, out_pdf, page, None, field=field, edit=edit)
    if not bad:
        return []
    d = fitz.open(stream=out_pdf, filetype="pdf")
    txt = d[page].get_text()
    d.close()
    return [f"underline left behind {t.strip()!r}" for t, _ in bad if t.strip() and t.strip() in txt]


def line_reads(before_pdf: bytes, out_pdf: bytes, page: int, span: dict, new: str) -> list:
    """The edited line must extract as the original line with only the
    replacement made — in order, on one line. A redraw that left a space
    glyph inside the pushed words read "https://\n \nZoé…": invisible on
    screen, plain to copy, search and anything that reads the text layer."""
    import re as _re
    x0, y0, x1, y1 = span["bbox"]
    # The line's own box, 1pt in: a narrower row drops a whitespace span from
    # the extraction — and with it the very defect (measured on the 1337 line).
    inset = min(1.0, 0.1 * (y1 - y0))
    row = fitz.Rect(-1e4, y0 + inset, 1e4, y1 - inset)
    b = fitz.open(stream=before_pdf, filetype="pdf")
    a = fitz.open(stream=out_pdf, filetype="pdf")
    try:
        norm = lambda t: _re.sub(r"\s+", " ", t).strip()
        want = norm(b[page].get_text(clip=row).replace(span["text"].strip(), new.strip(), 1))
        got = norm(a[page].get_text(clip=row))
        if want == got:
            return []
        # the line as a sequence of words is what a reader sees; line breaks
        # inside it are the defect
        if "\n" in a[page].get_text(clip=row).strip() and \
                "\n" not in b[page].get_text(clip=row).strip():
            return [f"line now extracts broken: {got[:60]!r}"]
        return []
    finally:
        b.close()
        a.close()


def moved_text(before_pdf: bytes, out_pdf: bytes, page: int, span: dict,
               tol: float = 0.6, reflowed: dict = None) -> list:
    """Words the user did not touch that are no longer where they were.

    None of the other checks look at text outside the edit, so a table whose
    columns slid 39pt left after a quantity edit passed as clean. Allowed to
    move: prose right after the field on its own line (a word processor
    pushes it). Everything else — other lines, text before the field, and
    anything on the line past a column gutter wider than two em — must stay
    within *tol* points.
    """
    x0, y0, x1, y1 = span["bbox"]
    em = 2.0 * float(span.get("size") or 10.0)
    b = fitz.open(stream=before_pdf, filetype="pdf")
    a = fitz.open(stream=out_pdf, filetype="pdf")
    try:
        wb = b[page].get_text("words")
        wa = a[page].get_text("words")
    finally:
        b.close(); a.close()

    def on_line(w):
        ov = min(w[3], y1) - max(w[1], y0)
        return ov > 0.5 * min(w[3] - w[1], y1 - y0)

    # Followers on the edited line, left to right: prose until the first
    # gutter wider than two em, pinned from there on.
    follow = sorted((w for w in wb if on_line(w) and w[0] >= x1 - tol), key=lambda w: w[0])
    rows = {}
    for w in wb:
        rows.setdefault(round((w[1] + w[3]) / 2), []).append(w)
    gut = []
    for ws in rows.values():
        ws.sort()
        gut += [ws[j] for j in range(1, len(ws)) if ws[j][0] - ws[j - 1][2] > 0.6 * (ws[j][3] - ws[j][1])]

    def is_cell(w):
        # Lines up, after a gutter, with gutter-separated words on 2+ other
        # rows — written independently of the engine's own test on purpose.
        return w in gut and len({round(v[1]) for v in gut if abs(v[1] - w[1]) >= 2
                                 and (abs(v[0] - w[0]) <= 0.5 or abs(v[2] - w[2]) <= 0.5)}) >= 2

    pinned_from, cur = None, x1
    for w in follow:
        if w[0] - cur > em or is_cell(w):
            pinned_from = w[0]
            break
        cur = max(cur, w[2])

    if reflowed:
        # A re-wrapped paragraph: its own words may re-flow (the edit's
        # readback and the engine's verification cover them); everything
        # below the cut must have moved by EXACTLY the reported shift, and
        # everything else not at all.
        top, cut, dy = reflowed["top"], reflowed["cut"], reflowed["shift"]
        lost = []
        pool = list(wa)
        for w in wb:
            if top - tol <= w[1] < cut:
                continue                               # the paragraph's own band
            ty = w[1] + (dy if w[1] >= cut else 0.0)
            k = next((i for i, v in enumerate(pool) if v[4] == w[4]
                      and abs(v[0] - w[0]) <= tol and abs(v[1] - ty) <= tol), None)
            if k is None:
                lost.append(w)
            else:
                pool.pop(k)
        return [f"{w[4][:14]!r}@{w[0]:.0f},{w[1]:.0f} moved/lost (reflow)" for w in lost[:3]]

    must = []
    for w in wb:
        if on_line(w):
            if w[2] > x0 + tol and w[0] < x1 - tol:
                continue                                   # the edit itself
            if w[0] >= x1 - tol and (pinned_from is None or w[0] < pinned_from - tol):
                continue                                   # prose that may flow
        must.append(w)
    pool = list(wa)
    lost = []
    for w in must:
        k = next((i for i, v in enumerate(pool) if v[4] == w[4]
                  and abs(v[0] - w[0]) <= tol and abs(v[1] - w[1]) <= tol), None)
        if k is None:
            lost.append(w)
        else:
            pool.pop(k)
    return [f"{w[4][:14]!r}@{w[0]:.0f},{w[1]:.0f} moved/lost" for w in lost[:3]] + (
        [f"+{len(lost) - 3} more"] if len(lost) > 3 else [])


def corpus_docs(limit=None):
    """(label, path) for the example plus every corpus document present."""
    import glob
    docs = []
    demo = os.path.join(EXAMPLES, "attestation-demo.pdf")
    if os.path.exists(demo):
        docs.append(("attestation-demo", demo))
    docs += [(os.path.basename(f)[:-4], f) for f in sorted(glob.glob(os.path.join(CORPUS, "*.pdf")))]
    return docs[:limit] if limit else docs


def first_inplace_span(data: bytes, spans, tries: int = 30):
    """A span that ACTUALLY edits in place under a benign replacement.

    Taking the first span made a whole adversarial run come back "refused" for
    reasons having nothing to do with the text under test — a wall of identical
    refusals that looks like robustness and proves nothing.
    """
    from api import apply_replacements
    for cand in spans[:tries]:
        t = cand["text"].strip()
        try:
            _o, rep = apply_replacements(data, [(cand, t[:max(3, len(t) // 2)])],
                                         preserve_size=True, try_inplace=True)
        except Exception:  # noqa: BLE001
            continue
        if rep["in_place"]["count"]:
            return cand
    return None
