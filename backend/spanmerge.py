"""spanmerge.py — present a line of text as phrases, not words.

MuPDF closes a span at every inter-word gap when the producer draws that gap as a
positioning number instead of a space glyph (pdfTeX/LaTeX, dvips: "(Python,)-333(Go,)"). A
LaTeX résumé therefore arrived as one "field" per word, and changing a job title meant
selecting and editing every word of it separately.

Consecutive spans of one line that share font, size, colour and baseline, and are separated by
no more than a word gap, are one phrase. A wider gap (a tab, a right-aligned date, a column) still
splits them.
"""
from __future__ import annotations

# The widest gap that is still "a space between words". A justified TeX line stretches a
# 0.33 em space to about 0.5 em; a column gutter or tab is several em.
WORD_GAP_EM = 0.8
# Below this the two pieces touch (a kerning nudge or a font-change seam): join without a space.
NO_SPACE_EM = 0.12


def show_groups(page) -> list:
    """One rectangle per text-showing operation on *page* (a whole TJ array is ONE of them).

    Words that were drawn by the same operation belong together (pdfTeX draws a whole line as
    one TJ with the word gaps as numbers). Two table cells, however drawn by two operations,
    are never merged whatever the gap between them: "1,200.00" and "4,800.00" sit 7pt apart,
    a distance no gap threshold can tell from a word space.
    """
    try:
        import fitz
        return [fitz.Rect(t["bbox"]) for t in page.get_texttrace()]
    except Exception:  # noqa: BLE001
        return []


def _same_show(groups: list, a, b, tol: float = 0.6) -> bool:
    for g in groups:
        if (g.x0 - tol <= min(a[0], b[0]) and max(a[2], b[2]) <= g.x1 + tol
                and g.y0 - tol <= min(a[1], b[1]) and max(a[3], b[3]) <= g.y1 + tol):
            return True
    return False


def merge_line_spans(spans: list, groups: list | None = None) -> list:
    """Merge the spans of ONE MuPDF line (dicts with text/bbox/origin/font/size/color/flags).

    *groups* (see show_groups) restricts merging to words drawn by one operation; without it
    nothing is merged.
    """
    if not groups:
        return [dict(s) for s in spans]
    out: list = []
    for s in spans:
        p = out[-1] if out else None
        if p is not None and _joinable(p, s) and _same_show(groups, p["bbox"], s["bbox"]):
            gap = s["bbox"][0] - p["bbox"][2]
            size = float(s.get("size") or 10.0)
            sep = " " if gap > NO_SPACE_EM * size and not p["text"].endswith(" ") \
                and not s["text"].startswith(" ") else ""
            p["text"] = p["text"] + sep + s["text"]
            p["bbox"] = (min(p["bbox"][0], s["bbox"][0]), min(p["bbox"][1], s["bbox"][1]),
                         max(p["bbox"][2], s["bbox"][2]), max(p["bbox"][3], s["bbox"][3]))
            p["merged"] = p.get("merged", 1) + 1
        else:
            out.append(dict(s))
    return out


def _joinable(p: dict, s: dict) -> bool:
    if p["font"] != s["font"] or p.get("color") != s.get("color") or p.get("flags") != s.get("flags"):
        return False
    if abs(float(p["size"]) - float(s["size"])) > 0.05:
        return False
    if abs(p["origin"][1] - s["origin"][1]) > 0.5:
        return False
    if p.get("alpha", 255) != s.get("alpha", 255):
        return False
    gap = s["bbox"][0] - p["bbox"][2]
    return -0.5 <= gap <= WORD_GAP_EM * float(s["size"])
