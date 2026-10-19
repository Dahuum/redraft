"""test_drawings.py — an edit may not run its text across a line, box or shaded area.

The layout checks compared words with words. On the IRS 1040 "Filing Status" -> "Filing
Category" ran 4pt into the shaded entry column and ended 2pt from a checkbox, and shipped. Now
the edit is refused when its new text crosses a drawn edge its field did not cross: at once over
a stroked line or box side, past 1.5pt for the border of a tint.

Fixtures are built here; nothing is downloaded.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))
warnings.filterwarnings("ignore")

import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def build():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # 1. a label with a stroked checkbox 20pt to its right
    page.insert_text((60, 100), "Status", fontsize=11, fontname="helv")
    page.draw_rect(fitz.Rect(115, 91, 123, 99), color=(0, 0, 0), width=0.5)
    # 2. a label beside a shaded panel (fill only, no stroke) starting at x=120
    page.draw_rect(fitz.Rect(120, 130, 400, 160), color=None, fill=(0.86, 1, 0.98))
    page.insert_text((60, 150), "Name", fontsize=11, fontname="helv")
    # 3. a table cell: text between two column rules at x=50 and x=130
    for x in (50, 130, 250):
        page.draw_line(fitz.Point(x, 180), fitz.Point(x, 210), color=(0, 0, 0), width=0.5)
    page.insert_text((55, 199), "Widget", fontsize=11, fontname="helv")
    page.insert_text((135, 199), "North", fontsize=11, fontname="helv")
    # 4. a value printed ACROSS a rule already (a stamp over a border): editing it is not new
    page.draw_line(fitz.Point(100, 225), fitz.Point(100, 250), color=(0, 0, 0), width=0.5)
    page.insert_text((80, 240), "APPROVED", fontsize=11, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


pdf = build()
spans = api.extract_spans(pdf)
by = {s["text"].strip(): s for s in spans}


def edit(old, new):
    out, rep = api.apply_replacements(pdf, [(by[old], new)], try_inplace=True)
    text = fitz.open(stream=out, filetype="pdf")[0].get_text()
    reasons = [r["reason"] for r in rep["in_place"]["refusals"]]
    return new in text, reasons, rep["warnings"]


ok, reasons, warns = edit("Status", "Status of filing")
check("a value running across a checkbox is refused", not ok, (reasons, warns))
check("...and the reason says so", "runs_into_drawing" in reasons or any("drawn beside it" in w for w in warns),
      (reasons, warns))

ok, reasons, warns = edit("Status", "State")
check("a value that still clears the checkbox is made", ok, (reasons, warns))

ok, reasons, warns = edit("Name", "Full legal name here")
check("a value running well into a shaded panel is refused", not ok, (reasons, warns))

ok, reasons, warns = edit("Name", "Names")
check("a value ending short of the panel is made", ok, (reasons, warns))

ok, reasons, warns = edit("Widget", "Widget deluxe edition")
check("a cell value running across the column rule is refused", not ok, (reasons, warns))

ok, reasons, warns = edit("Widget", "Gadget")
check("a cell value inside its rules is made", ok, (reasons, warns))

ok, reasons, warns = edit("APPROVED", "REJECTED")
check("a value that already crossed a rule can still be edited", ok, (reasons, warns))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
