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
HARD = ("cannot_render", "runs_off_the_page", "cannot_place", "overlaps_neighbour")

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
