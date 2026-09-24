"""test_table_edits.py — editing one cell must not disturb the rest of the row.

Three bugs, all invisible to every check that only looks at the edited text:

  * REDRAW OVERPRINT. The redraw engine found which cells were being edited
    by id() of dicts from two different extractions — it never matched, so
    with a quantity AND an amount edited on one row, the amount was taken for
    an untouched neighbour, pushed right, and redrawn with its OLD value
    beside the new one. An annex row read "1,0000000".
  * IN-PLACE PULL. `_push = _dx - slack`, unclamped: a quantity going 1 -> 10
    (+5.6pt against 44pt of gutter) moved the price and amount columns 38.9pt
    LEFT; 10 -> 1 moved them 5.6pt.
  * TIGHT TABLES. A spreadsheet row "Widget A | North | 1420" has a gutter
    under two em, so "North" was treated as prose and the whole row followed
    a shortened product name. Cells are now recognised by lining up with
    other rows (inplace_spike._is_column_cell).

And one capability: a private-code simple TrueType font (LibreOffice, Skia)
had no code for a letter the document never used, so "Widget A" could never
become "Widget Ap". Codes are now allocated and the glyph injected under them.
"""
import base64
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, os.path.join(HERE, "audit"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
import inplace_spike as S  # noqa: E402
from _common import moved_text  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def table_pdf():
    d = fitz.open()
    p = d.new_page(width=595, height=842)
    y = 200
    for q, price in ((1, 100.0), (10, 100.0), (1, 100.0), (100, 100.0)):
        p.insert_text((70, y), f"Item{y}", fontsize=10, fontname="helv")
        p.insert_text((300, y), str(q), fontsize=10, fontname="helv")
        p.insert_text((370, y), f"{price:,.2f}", fontsize=10, fontname="helv")
        p.insert_text((460, y), f"{q * price:,.2f}", fontsize=10, fontname="helv")
        y += 24
    return d.tobytes()


def row_xs(pdf, y):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return sorted((round(w[0], 1), w[4]) for w in d[0].get_text("words")
                      if abs(w[1] - y) < 3)
    finally:
        d.close()


raw = table_pdf()
spans = api.extract_spans(raw)


def cell(y, x0, x1):
    return next(i for i, s in enumerate(spans)
                if abs(s["bbox"][1] - y) < 3 and x0 < s["bbox"][0] < x1)


rows = sorted({round(s["bbox"][1], 1) for s in spans})
r0, r1 = rows[0], rows[1]

for label, reps_spec in (
        ("qty grows 1 -> 10", [(r0, 295, 320, "10")]),
        ("qty shrinks 10 -> 1", [(r1, 295, 320, "1")]),
        ("qty and amount both grow", [(r0, 295, 320, "10"), (r0, 450, 560, "1,000.00")])):
    reps = [(spans[cell(y, a, b)], t) for y, a, b, t in reps_spec]
    y = reps_spec[0][0]
    for inplace in (True, False):
        out, _ = api.apply_replacements(raw, reps, try_inplace=inplace)
        xs = [x for x, _ in row_xs(out, y)]
        engine = "in-place" if inplace else "redraw"
        check(f"{engine}: {label} keeps every column where it was",
              xs == [70.0, 300.0, 370.0, 460.0], str(row_xs(out, y)))
        want = {x: t for (yy, a, b, t) in reps_spec for x in (300.0, 460.0) if a < x < b}
        got = dict(row_xs(out, y))
        check(f"{engine}: {label} applies every edit",
              all(got.get(x) == t for x, t in want.items()), str(got))
        check(f"{engine}: {label} leaves exactly one value per cell",
              len(xs) == 4, str(row_xs(out, y)))

# Prose must still flow: a longer name pushes the comma after it along.
d = fitz.open()
p = d.new_page()
p.insert_text((72, 100), "Signed by ", fontsize=11, fontname="helv")
p.insert_text((125.5, 100), "Ann", fontsize=11, fontname="hebo")
p.insert_text((148.5, 100), ", on 12 May", fontsize=11, fontname="helv")
prose = d.tobytes()
d.close()
ps = api.extract_spans(prose)
k = next(i for i, s in enumerate(ps) if s["text"].strip() == "Ann")
for new in ("Annabelle Whitford", "Al"):
    out, rep = api.apply_replacements(prose, [(ps[k], new)], try_inplace=True)
    d = fitz.open(stream=out, filetype="pdf")
    sp_ = [s for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", [])
           for s in l["spans"] if abs(s["bbox"][1] - ps[k]["bbox"][1]) < 3]
    d.close()
    name = next(s for s in sp_ if s["text"].strip() == new)
    comma = next(s for s in sp_ if s["text"].startswith(","))
    gap = comma["bbox"][0] - name["bbox"][2]
    check(f"prose: {new!r} keeps the comma's gap (word-processor flow)",
          0.5 < gap < 4.0, f"gap {gap:.2f}")

# Column-cell recognition: tables yes, prose no.
d = fitz.open(stream=raw, filetype="pdf")
check("a table's price column is recognised as a column cell",
      S._is_column_cell(d[0], spans[cell(r0, 295, 320)]["bbox"], 370.0))
d.close()
d = fitz.open(stream=prose, filetype="pdf")
check("the text after a name in a sentence is not a column cell",
      not S._is_column_cell(d[0], ps[k]["bbox"], 148.5))
d.close()

# The real LibreOffice sheet, when the audit corpus is present.
sales = os.path.expanduser("~/.cache/redraft-audit/corpus/sales.pdf")
if os.path.exists(sales):
    sraw = open(sales, "rb").read()
    ss = api.extract_spans(sraw)
    w = next(i for i, s in enumerate(ss) if s["text"].strip() == "Widget A")
    for new in ("Widget", "Widget AB", "Widget Alpha Beta Gamma"):
        for inplace in (True, False):
            out, _ = api.apply_replacements(sraw, [(ss[w], new)], try_inplace=inplace)
            check(f"sales ({'in-place' if inplace else 'redraw'}): {new!r} moves no other cell",
                  not moved_text(sraw, out, 0, ss[w]),
                  str(moved_text(sraw, out, 0, ss[w])))
    r = S.edit(sraw, "Widget A", "Widget Ap")
    check("private-code font: a letter the document never used is typed in place",
          r.get("ok") and r.get("tier") == "extend", str(r.get("reason")))
    if r.get("ok"):
        out = base64.b64decode(r["pdf_b64"])
        d = fitz.open(stream=out, filetype="pdf")
        txt = d[0].get_text()
        nd = sum(1 for sp in d[0].get_texttrace() for g in sp["chars"] if g[1] == 0)
        d.close()
        check("  ...reads back, draws no .notdef, disturbs no other cell",
              "Widget Ap" in txt and nd == 0 and not moved_text(sraw, out, 0, ss[w]))
else:
    print("SKIP - sales.pdf not in the audit corpus (run spikes/audit/build_corpus.py)")

# ── right-aligned number columns keep their right edge; left ones their left ──
def column_pdf(right):
    d = fitz.open()
    pg = d.new_page(width=400, height=300)
    fnt = fitz.Font("helv")
    for i, v in enumerate(("850.00", "1,020.00", "12,400.00", "96.50")):
        x = 300 - fnt.text_length(v, 10) if right else 200
        pg.insert_text((x, 100 + 18 * i), v, fontsize=10, fontname="helv")
        pg.insert_text((40, 100 + 18 * i), f"Line {i + 1}", fontsize=10, fontname="helv")
    return d.tobytes()


for right in (True, False):
    col = column_pdf(right)
    cs = api.extract_spans(col)
    tgt = next(s for s in cs if s["text"].strip() == "850.00")
    out, rep = api.apply_replacements(col, [(tgt, "9,850.00")], try_inplace=True)
    w = next(x for x in fitz.open(stream=out, filetype="pdf")[0].get_text("words")
             if x[4] == "9,850.00")
    if right:
        check("right-aligned column: a longer amount keeps its right edge",
              rep["in_place"]["count"] == 1 and abs(w[2] - tgt["bbox"][2]) < 0.1,
              f"x1 {tgt['bbox'][2]:.2f} -> {w[2]:.2f}")
    else:
        check("left-aligned column: the amount keeps its LEFT edge (no guessing)",
              rep["in_place"]["count"] == 1 and abs(w[0] - tgt["bbox"][0]) < 0.1,
              f"x0 {tgt['bbox'][0]:.2f} -> {w[0]:.2f}")


# ── a LONE amount flush against the text block's right margin (fpdf2's
#    cell(0, h, amount, align="R")) keeps its right edge; a lone amount that is
#    merely the rightmost thing on a sparse page keeps its left edge ─────────
def lone_pdf(margin_line):
    d = fitz.open()
    pg = d.new_page(width=400, height=300)
    fnt = fitz.Font("helv")
    pg.insert_text((40, 100), "Implementation support", fontsize=10, fontname="helv")
    pg.insert_text((340 - fnt.text_length("11,400.00", 10), 100), "11,400.00",
                   fontsize=10, fontname="helv")
    if margin_line:          # a justified line ending exactly on the margin
        words, y = "This statement confirms that every invoice was settled".split(), 140
        gap = (300 - sum(fnt.text_length(w, 10) for w in words)) / (len(words) - 1)
        x = 40
        for w in words:
            pg.insert_text((x, y), w, fontsize=10, fontname="helv")
            x += fnt.text_length(w, 10) + gap
    else:
        pg.insert_text((40, 140), "Paid in full.", fontsize=10, fontname="helv")
    return d.tobytes()


for margin_line in (True, False):
    lp = lone_pdf(margin_line)
    tgt = next(s for s in api.extract_spans(lp) if s["text"].strip() == "11,400.00")
    out, rep = api.apply_replacements(lp, [(tgt, "211,400.00")], try_inplace=True)
    w = next(x for x in fitz.open(stream=out, filetype="pdf")[0].get_text("words")
             if x[4] == "211,400.00")
    if margin_line:
        check("lone amount on the right margin: a longer amount keeps its right edge",
              rep["in_place"]["count"] == 1 and abs(w[2] - tgt["bbox"][2]) < 0.1,
              f"x1 {tgt['bbox'][2]:.2f} -> {w[2]:.2f}")
    else:
        check("lone amount, no margin evidence: keeps its LEFT edge",
              rep["in_place"]["count"] == 1 and abs(w[0] - tgt["bbox"][0]) < 0.1,
              f"x0 {tgt['bbox'][0]:.2f} -> {w[0]:.2f}")

w4 = os.path.expanduser("~/.cache/redraft-audit/corpus/irs-w4.pdf")
if os.path.exists(w4):
    raw4 = open(w4, "rb").read()
    t4 = next(s for s in api.extract_spans(raw4) if s["text"].strip() == "$850")
    out, rep = api.apply_replacements(raw4, [(t4, t4["text"].replace("$850", "$12,850"))],
                                      try_inplace=True)
    d4 = fitz.open(stream=out, filetype="pdf")
    w = next(x for x in d4[t4.get("page", 0)].get_text("words")
             if x[4] == "$12,850" and abs(x[1] - t4["bbox"][1]) < 2)
    check("IRS W-4 table: '$850' -> '$12,850' stays flush right in its cell",
          abs(w[2] - t4["bbox"][2]) < 0.1, f"{w[2]:.2f} vs {t4['bbox'][2]:.2f}")

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
