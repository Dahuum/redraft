"""
pdf_editor_alignment_test.py — proves _detect_alignments() no longer
misclassifies plainly left-aligned text as right/center-aligned from a
single coincidental shared edge with unrelated content elsewhere on the
page.

Found live on a real resume: a page-wide name header's right edge landed
within the 3pt tolerance of ONE unrelated right-aligned date field further
down the page — pure coincidence, nothing to do with either field's actual
layout intent. The old algorithm needed only one such match to call a span
"right-aligned"; on an edit this sends `ox` to `bbox.x1 - text_w` — for a
SHORTER replacement, that's nowhere near the original position. The result,
reproduced exactly here: the original text erased from its real (left)
position, and the new text drawn far to the right instead, on top of
whatever else was there — visually indistinguishable from "old text still
there, new text in the wrong place."

Fixed by requiring at least TWO independent corroborating spans before
calling something a column — a real repeating right-aligned column (dates,
prices, down the page) naturally has more than one row; a coincidence
doesn't. This file proves both directions: the false positive is gone, and
a genuine multi-row column is still detected correctly.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import fitz  # noqa: E402
from pdf_editor import _detect_alignments, get_spans, PDFEditor  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


print("=== 1) one coincidental shared right-edge must NOT trigger right-alignment ===")
TTF = "/System/Library/Fonts/Supplemental/Verdana.ttf"
font = fitz.Font(fontfile=TTF)
name_text = "ABDERRAHMAN CHAHROUR"
name_x1 = 72 + font.text_length(name_text, 20)
date_text = "February 2026"
date_x1 = name_x1 + 1.5   # within the 3pt tolerance -- the coincidence
date_x0 = date_x1 - font.text_length(date_text, 11)

spans = [
    {"text": name_text, "bbox": fitz.Rect(72, 40, name_x1, 64), "origin": (72, 60)},
    {"text": date_text, "bbox": fitz.Rect(date_x0, 99, date_x1, 112), "origin": (date_x0, 110)},
]
align = _detect_alignments(spans)
key = (round(72.0, 1), round(60.0, 1))
check("single coincidental match -> left, not right", align[key] == "left", repr(align))

print("\n=== 2) a genuine 3-row right-aligned date column is still detected ===")
spans2 = [
    {"text": "Job Title A", "bbox": fitz.Rect(72, 60, 200, 72), "origin": (72, 70)},
    {"text": "February 2026", "bbox": fitz.Rect(270, 60, 350, 72), "origin": (270, 70)},
    {"text": "Job Title B", "bbox": fitz.Rect(72, 100, 220, 112), "origin": (72, 110)},
    {"text": "March 2026", "bbox": fitz.Rect(280, 100, 351, 112), "origin": (280, 110)},
    {"text": "Job Title C", "bbox": fitz.Rect(72, 140, 190, 152), "origin": (72, 150)},
    {"text": "April 2026", "bbox": fitz.Rect(275, 140, 349, 152), "origin": (275, 150)},
]
align2 = _detect_alignments(spans2)
dates_right = all(align2[(round(s["origin"][0], 1), round(s["origin"][1], 1))] == "right"
                  for s in spans2 if "2026" in s["text"])
titles_left = all(align2[(round(s["origin"][0], 1), round(s["origin"][1], 1))] == "left"
                 for s in spans2 if "Job Title" in s["text"])
check("all 3 dates detected as right-aligned", dates_right, repr(align2))
check("all 3 titles stay left-aligned", titles_left, repr(align2))

print("\n=== 3) end-to-end: editing the header no longer jumps to the wrong position ===")
doc = fitz.open()
page = doc.new_page(width=595, height=200)
page.insert_font(fontname="F0", fontfile=TTF)
page.insert_text((72, 60), name_text, fontname="F0", fontsize=20)
page.insert_text((date_x0, 110), date_text, fontname="F0", fontsize=11)
page.insert_text((72, 110), "Some Job Title", fontname="F0", fontsize=11)
pdf_path = "/tmp/alignment_e2e_test.pdf"
doc.save(pdf_path)

spans3 = get_spans(fitz.open(pdf_path), 0)
ed = PDFEditor(pdf_path)
ed.replace_all([(spans3[0], "A CHAHROUR")], page_num=0)
out_path = "/tmp/alignment_e2e_test_edited.pdf"
ed.save(out_path)

d = fitz.open(out_path)
found_at_left = False
for b in d[0].get_text("dict")["blocks"]:
    if b["type"] != 0:
        continue
    for l in b["lines"]:
        for s in l["spans"]:
            if "CHAHROUR" in s["text"]:
                found_at_left = abs(s["bbox"][0] - 72.0) < 2.0
d.close()
check("edited name redraws at its original left position (x≈72), not jumped right",
     found_at_left)

for p in (pdf_path, out_path):
    try:
        os.remove(p)
    except OSError:
        pass

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
