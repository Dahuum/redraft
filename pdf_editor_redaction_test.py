"""
pdf_editor_redaction_test.py — proves PDFEditor's "erase original" step
actually REMOVES the original text from the content stream, not just paints
a rectangle over it.

Found live: the prior implementation used `page.draw_rect(...)` to cover the
old text with a background-colored rectangle, but never removed the
underlying text-showing operators. The old text was still fully present and
extractable — by copy/paste, Ctrl+F, or any programmatic text extraction
(exactly like this test does) — underneath the visual cover, for EVERY edit
ever made through this engine, not just unusual cases. This is now fixed
with true redaction (`add_redact_annot` + `apply_redactions`, restricted to
text only — images/graphics are explicitly left untouched, matching this
step's original, narrower intent).

Self-contained: builds its own synthetic fixtures, no external PDF files
needed (unlike pdf_editor_test.py, which expects private local test PDFs).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import fitz  # noqa: E402
from pdf_editor import PDFEditor, get_spans  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def edit_via_pdfeditor(pdf_path, replacements_by_page):
    ed = PDFEditor(pdf_path)
    for pn, pairs in replacements_by_page.items():
        ed.replace_all(pairs, page_num=pn)
    out_path = pdf_path.replace(".pdf", "_edited.pdf")
    ed.save(out_path)
    return out_path


print("=== 1) simple single-Tj field: old text must be GONE, not just covered ===")
doc = fitz.open(); page = doc.new_page(width=400, height=150)
page.insert_text((40, 40), "Invoice Total: 1000", fontname="helv", fontsize=13)
src = "/tmp/redaction_test_simple.pdf"
doc.save(src)
spans = get_spans(fitz.open(src), 0)
out = edit_via_pdfeditor(src, {0: [(spans[0], "Invoice Total: 2500")]})
d = fitz.open(out)
txt = d[0].get_text()
d.close()
check("new text present", "Invoice Total: 2500" in txt, repr(txt))
check("old text NOT extractable", "1000" not in txt, repr(txt))

print("\n=== 2) TJ-array split field (real-world word-gap shape) — same check ===")
doc = fitz.open(); page = doc.new_page(width=400, height=150)
page.insert_text((40, 20), " ", fontname="helv", fontsize=13)
raw = b"q BT /helv 13 Tf 0 g 1 0 0 1 40 70 Tm [(Custo)-40(mer: Acme)]TJ ET Q"
xref = page.get_contents()[0]
doc.update_stream(xref, raw)
src2 = "/tmp/redaction_test_split.pdf"
doc.save(src2)
spans2 = get_spans(fitz.open(src2), 0)
out2 = edit_via_pdfeditor(src2, {0: [(spans2[0], "Customer: Beta")]})
d2 = fitz.open(out2)
txt2 = d2[0].get_text()
d2.close()
check("new text present", "Customer: Beta" in txt2, repr(txt2))
check("old text NOT extractable", "Acme" not in txt2, repr(txt2))

print("\n=== 3) multiple fields on the same page — each cleanly replaced, none leak ===")
doc = fitz.open(); page = doc.new_page(width=400, height=150)
page.insert_text((40, 40), "Invoice Total: 1000", fontname="helv", fontsize=13)
page.insert_text((40, 70), "Customer: Acme Corp", fontname="helv", fontsize=13)
src3 = "/tmp/redaction_test_multi.pdf"
doc.save(src3)
spans3 = get_spans(fitz.open(src3), 0)
out3 = edit_via_pdfeditor(src3, {0: [(spans3[0], "Invoice Total: 2500"),
                                     (spans3[1], "Customer: Beta LLC")]})
d3 = fitz.open(out3)
txt3 = d3[0].get_text()
d3.close()
check("both new texts present", "Invoice Total: 2500" in txt3 and "Customer: Beta LLC" in txt3, repr(txt3))
check("neither old text leaks", "1000" not in txt3 and "Acme" not in txt3, repr(txt3))

print("\n=== 4) an image + a decorative line overlapping the field are preserved ===")
doc = fitz.open(); page = doc.new_page(width=400, height=200)
pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
pix.set_rect(pix.irect, (255, 0, 0))
page.insert_image(fitz.Rect(300, 20, 350, 70), pixmap=pix)
page.insert_text((40, 40), "Invoice Total: 1000", fontname="helv", fontsize=13)
page.draw_line((38, 42), (150, 42), width=0.5)  # fully inside the text's own bbox
src4 = "/tmp/redaction_test_graphics.pdf"
doc.save(src4)
spans4 = get_spans(fitz.open(src4), 0)
out4 = edit_via_pdfeditor(src4, {0: [(spans4[0], "Invoice Total: 2500")]})
d4 = fitz.open(out4)
txt4 = d4[0].get_text()
n_images = len(d4[0].get_images())
n_drawings = len(d4[0].get_drawings())
d4.close()
check("new text present, old gone", "2500" in txt4 and "1000" not in txt4, repr(txt4))
check("image preserved", n_images == 1, f"images={n_images}")
check("decorative line preserved", n_drawings >= 1, f"drawings={n_drawings}")

for p in (src, src2, src3, src4, out, out2, out3, out4):
    try:
        os.remove(p)
    except OSError:
        pass

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
