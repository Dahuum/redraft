"""test_tail_per_glyph.py — a word edited inside a line a producer drew ONE GLYPH PER `Td` must
leave the rest of that line exactly as the producer spaced it.

Chrome prints do this: every glyph has its own `dx 0 Td`, the offsets are rounded, and the line
is a few percent wider than the font's own advances would make it. The engine edited the whole
span, and everything from the first changed glyph to the end of the span was re-set at the
font's natural advances: the tail came out visibly tighter than the words before the edited
one, and because the width model (natural advances) was not what had been drawn, the text that
follows the line on the same baseline was pulled left by too much and printed over it.

Fixture: built here from a base-14 font, fictional text, so no document is needed.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


SIZE, X0, Y = 9.2, 40.0, 100.0
LINE = "Voici une description de chaque partie du cursus et de la position actuelle :"
HEAD_TITLE = "Le Tronc Commun"
AFTER = "Le tronc 42"
FONT = fitz.Font("helv")


def adv(ch, size=SIZE):
    return FONT.glyph_advance(ord(ch)) * size


def grid(ch):
    """The producer's rounded, slightly generous advance: 5% over natural, to 0.1pt."""
    return round(adv(ch) * 1.05, 1)


def build():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((10, 10), " ", fontname="helv", fontsize=SIZE)        # registers /helv
    ops = [f"BT /helv {SIZE} Tf 1 0 0 1 {X0} {842 - Y} Tm"]
    for i, ch in enumerate(LINE):
        if i:
            ops.append(f"{grid(LINE[i - 1])} 0 Td")
        esc = ch.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({esc}) Tj")
    ops.append("ET")
    end = X0 + sum(grid(c) for c in LINE[:-1]) + adv(LINE[-1])
    bx = end + 0.2
    ops.append(f"BT /helv 10.8 Tf 1 0 0 1 {bx:.2f} {842 - Y} Tm ({HEAD_TITLE}) Tj ET")
    bend = bx + sum(adv(c, 10.8) for c in HEAD_TITLE)
    cx = bend + 0.4
    ops.append(f"BT /helv {SIZE} Tf 1 0 0 1 {cx:.2f} {842 - Y} Tm ({AFTER}) Tj ET")
    xref = page.get_contents()[0]
    doc.update_stream(xref, ("\n".join(ops)).encode())
    return doc.tobytes()


def glyphs(pdf, startswith):
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    for b in pg.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = "".join(c["c"] for c in s["chars"])
                if t.startswith(startswith):
                    return [(c["c"], c["origin"][0], c["bbox"]) for c in s["chars"]]
    return None


def span_edges(pdf):
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    return {s["text"]: s["bbox"] for s in api.extract_spans(pdf) if s["page"] == 0}, pg


def overlaps(pdf):
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    cs = []
    for b in pg.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for k, s in enumerate(l["spans"]):
                for c in s["chars"]:
                    if c["c"].strip():
                        cs.append((fitz.Rect(c["bbox"]), id(s)))
    n = 0
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            if cs[i][1] != cs[j][1]:
                inter = cs[i][0] & cs[j][0]
                if not inter.is_empty and inter.get_area() > .35 * min(cs[i][0].get_area(), cs[j][0].get_area()):
                    n += 1
    return n


PDF = build()
spans = api.extract_spans(PDF)
A = next((s for s in spans if s["text"].startswith("Voici une description")), None)
check("fixture: the line reads back as one span", A is not None and A["text"] == LINE,
      repr(A and A["text"]))
if A is None:
    sys.exit(1)
orig_g = glyphs(PDF, "Voici une description")
orig_B = glyphs(PDF, "Le Tronc Commun")[0][2][0]
orig_gap = orig_B - A["bbox"][2]
check("fixture: nothing overlaps before the edit", overlaps(PDF) == 0)
check("fixture: the line really is wider than the font's advances",
      (A["bbox"][2] - A["bbox"][0]) > sum(adv(c) for c in LINE) * 1.03)

for new_word, label in (("team", "shorter word"), ("formation", "longer word")):
    new_text = LINE.replace("cursus", new_word)
    out, rep = api.apply_replacements(PDF, [(A, new_text)], try_inplace=True)
    g = glyphs(out, "Voici une description")
    check(f"{label}: the edit reads back", g is not None and "".join(c[0] for c in g) == new_text,
          "".join(c[0] for c in g) if g else "line missing")
    if g is None or "".join(c[0] for c in g) != new_text:
        continue
    i = LINE.index("cursus")
    j = i                                              # same index: the head is unchanged
    tail_o = orig_g[i + 6:]
    tail_n = g[j + len(new_word):]
    check(f"{label}: the words before the edited one did not move",
          all(abs(a[1] - b[1]) < 0.02 for a, b in zip(orig_g[:i], g[:j])))
    gaps_o = [round(b[1] - a[1], 2) for a, b in zip(tail_o, tail_o[1:])]
    gaps_n = [round(b[1] - a[1], 2) for a, b in zip(tail_n, tail_n[1:])]
    worst = max((abs(a - b) for a, b in zip(gaps_o, gaps_n)), default=0)
    check(f"{label}: the tail keeps the producer's own letter spacing", worst < 0.06,
          f"largest gap difference {worst:.2f}pt")
    shift = tail_n[0][1] - tail_o[0][1]
    check(f"{label}: the whole tail moved by one amount ({shift:+.1f}pt)",
          all(abs((b[1] - a[1]) - shift) < 0.06 for a, b in zip(tail_o, tail_n)))
    edges, _ = span_edges(out)
    b_new = glyphs(out, "Le Tronc Commun")[0][2][0]
    a_end = max(bb[2] for t, bb in edges.items() if t.startswith("Voici une description"))
    check(f"{label}: the text after the line keeps its gap from it",
          abs((b_new - a_end) - orig_gap) < 0.6, f"{b_new - a_end:.2f} vs {orig_gap:.2f}")
    check(f"{label}: nothing on the line overlaps", overlaps(out) == 0, f"{overlaps(out)} overlaps")

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
