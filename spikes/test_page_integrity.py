"""test_page_integrity.py — an edit must leave the REST of the page alone.

Every case here was a visible "this was edited" tell that passed every check
looking only at the edited text:

  * TIGHT LEADING. 7pt type on 6pt leading (the IRS 1040 column headers):
    the redraw erased the edited line's whole box and took "other" off the
    line beneath it.
  * WRONG BACKGROUND. The erase painted a sampled colour; on a pale-cyan cell
    the median sample landed in neighbouring ink and painted a grey patch.
    Nothing is painted now — the old glyphs are deleted, not covered.
  * STRANDED DECORATION. A link underline inside the field's box stayed put
    while the redrawn words moved; one reaching outside it (a table rule)
    must stay.
  * CROSSING A GUTTER. A lengthened sentence ran through the neighbouring
    column's caption, and the caption's next line was pushed off the page.
  * ANNEX LABEL. The total's widened cell reached into its own "Total HT"
    label, and every generated annex printed "Total H".
  * A SPACE LEFT BEHIND. A redraw pushes the words after a longer field
    along; a whitespace-only span (Word's trailing space) was no field, so it
    stayed — inside the pushed email — and the line extracted as
    "https://\n \nZoé…": copy and search read the edit.
  * DRAWN LAST. The redraw draws its text from a stream appended to the
    page, and MuPDF, Chrome and Firefox extract text in DRAWING order: the
    edited sentence read at the end of the page, below the signature.
  * INVISIBLE OCR TEXT. On a scan the text is a picture; the searchable layer
    over it is drawn invisibly. Editing it reported success, changed no pixel,
    and left the text layer contradicting the image. It is refused now.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
from annex_model import plan_edits  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def _reading_order_off(pdf, sd):
    """The same edit with the reading-order repair disabled: the pixels it
    must reproduce exactly."""
    import reading_order
    keep = reading_order.restore_order
    reading_order.restore_order = lambda b, a, p: a
    try:
        return api.apply_replacements(pdf, [(sd, "Est inscrit(e) dans notre éco")],
                                      preserve_size=True, try_inplace=True)[0]
    finally:
        reading_order.restore_order = keep


def _no_reorder(pdf, sd, new):
    import reading_order
    keep = reading_order.restore_order
    reading_order.restore_order = lambda b, a, p: a
    try:
        return api.apply_replacements(pdf, [(sd, new)], try_inplace=False)[0]
    finally:
        reading_order.restore_order = keep


def words(pdf, pno=0):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return d[pno].get_text("words")
    finally:
        d.close()


def span_of(pdf, text):
    return next(s for s in api.extract_spans(pdf) if s["text"].strip().startswith(text))


CYAN = (220 / 255, 1.0, 250 / 255)

# ── tight leading on a tinted cell ───────────────────────────────────────────
d = fitz.open()
p = d.new_page(width=300, height=200)
p.draw_rect(fitz.Rect(40, 40, 260, 120), color=None, fill=CYAN)
p.draw_line(fitz.Point(40, 120), fitz.Point(260, 120), width=0.8)
for i, t in enumerate(("Credit for", "other", "dependents")):
    p.insert_text((60, 70 + 6 * i), t, fontsize=7, fontname="helv")
tight = d.tobytes()
d.close()
sd = span_of(tight, "Credit for")
out, rep = api.apply_replacements(tight, [(sd, "Credit for all")], try_inplace=False)
after = [w[4] for w in words(out)]
check("tight leading: the line below survives a redraw of the line above",
      "other" in after and "dependents" in after, str(after))
check("tight leading: the edit itself is there", "all" in after, str(after))
d = fitz.open(stream=out, filetype="pdf")
pix = d[0].get_pixmap(dpi=144)
d.close()
# A point inside the erased box but clear of every glyph: the old text's
# right end, past the new glyph positions is not guaranteed, so sample the
# gap between "Credit" and "for" rows' left margin instead.
px = pix.pixel(int(52 * 2), int(66 * 2))
check("tinted cell: no painted patch where the old text was",
      abs(px[0] - 220) <= 3 and px[1] >= 252 and abs(px[2] - 250) <= 3, str(px))
n_before = len(fitz.open(stream=tight, filetype="pdf")[0].get_drawings())
n_after = len(fitz.open(stream=out, filetype="pdf")[0].get_drawings())
check("tinted cell: nothing is painted over the old text (no new filled shapes)",
      n_after <= n_before, f"{n_before} drawings before, {n_after} after")
check("tinted cell: the rule under the cell is still drawn",
      any(abs(dr["rect"].y0 - 120) < 1.5 for dr in fitz.open(stream=out, filetype="pdf")[0].get_drawings()
          if dr["rect"].width > 100))

# ── a field's own underline goes with it; a wider rule stays ────────────────
d = fitz.open()
p = d.new_page(width=300, height=200)
p.insert_text((60, 80), "see reflection.", fontsize=11, fontname="tiro")
w = fitz.get_text_length("see reflection.", "tiro", 11)
p.draw_rect(fitz.Rect(80, 81.2, 60 + w - 3, 81.9), color=None, fill=(0.6, 0.6, 0.6))   # underline
p.draw_rect(fitz.Rect(20, 130, 280, 130.8), color=None, fill=(0, 0, 0))              # table rule
deco = d.tobytes()
d.close()
sd = span_of(deco, "see reflection")
out, _ = api.apply_replacements(deco, [(sd, "see the reflection chapter")], try_inplace=False)
rects = [dr["rect"] for dr in fitz.open(stream=out, filetype="pdf")[0].get_drawings()]
check("decoration: the field's own underline is removed with it",
      not any(abs(r.y0 - 81.2) < 0.5 and r.width < 100 for r in rects), str(rects))
check("decoration: a rule reaching outside the field is kept",
      any(abs(r.y0 - 130) < 0.5 for r in rects), str(rects))

# ── two columns: an edit must not cross the gutter ──────────────────────────
d = fitz.open()
p = d.new_page(width=595, height=300)
p.insert_text((43, 100), "thereby allowing metaprogramming and reflection.", fontsize=12, fontname="tiro")
p.insert_text((368, 91), "The standard type hierarchy in", fontsize=10, fontname="helv")
p.insert_text((368, 104), "Python 3", fontsize=10, fontname="helv")
for yy in range(120, 240, 14):
    p.insert_text((43, yy), "Body text in the left column continues here.", fontsize=12, fontname="tiro")
    p.insert_text((368, yy), "Caption column text.", fontsize=10, fontname="helv")
cols = d.tobytes()
d.close()
sd = span_of(cols, "thereby allowing")
before = words(cols)
out, rep = api.apply_replacements(cols, [(sd, sd["text"].strip() + " Wxqzkj Wxqzkj Wxqzkj Wxqzkj Wxqzkj")],
                                  try_inplace=True)
aft = words(out)
cap = [w for w in before if w[0] >= 360]
moved = [w[4] for w in cap if not any(v[4] == w[4] and abs(v[0] - w[0]) < 0.6 and abs(v[1] - w[1]) < 0.6
                                      for v in aft)]
check("two columns: the neighbouring column's text does not move", not moved, str(moved))
d = fitz.open(stream=out, filetype="pdf")
acc = d[0].get_text("words", flags=getattr(fitz, "TEXT_ACCURATE_BBOXES", 0))
d.close()
cap_boxes = [fitz.Rect(w[:4]) for w in acc if w[0] >= 360]
left_boxes = [fitz.Rect(w[:4]) for w in acc if w[0] < 360]
hit = [(tuple(round(v) for v in a), tuple(round(v) for v in b))
       for a in left_boxes for b in cap_boxes if (a & b).get_area() > 0.15 * min(a.get_area(), b.get_area())]
check("two columns: nothing is printed over the neighbouring column", not hit, str(hit[:2]))

# ── annex: the total's widened cell stops at its own label ──────────────────
d = fitz.open()
p = d.new_page(width=595, height=842)
y = 190
for h, x in (("Designation", 70), ("Qte", 300), ("PU HT", 370), ("Montant HT", 460)):
    p.insert_text((x, y), h, fontsize=10, fontname="hebo")
y += 8
p.draw_line(fitz.Point(70, y), fitz.Point(540, y))
y += 20
items = [("Prestation de conseil", 1200.0, 4), ("Support technique", 950.0, 12),
         ("Formation equipe", 600.0, 2)]
tot = 0.0
for name, pu, q in items:
    p.insert_text((70, y), name, fontsize=10, fontname="helv")
    p.insert_text((300, y), str(q), fontsize=10, fontname="helv")
    p.insert_text((370, y), f"{pu:,.2f}", fontsize=10, fontname="helv")
    p.insert_text((460, y), f"{q * pu:,.2f}", fontsize=10, fontname="helv")
    tot += q * pu
    y += 22
y += 28
p.insert_text((370, y), "Total HT", fontsize=11, fontname="hebo")
p.insert_text((460, y), f"{tot:,.2f}", fontsize=11, fontname="hebo")
ann = d.tobytes()
d.close()
spans, model, prof = api._annex_spans_and_model(ann, None)
colmap = api._annex_colmap(model)
spec = {0: {"qty": 40.0}, 1: {"qty": 120.0}, 2: {"qty": 20.0}}
reps = [(api._relax_numeric(spans[sid], colmap.get(sid), prof, spans) if txt else spans[sid], txt)
        for sid, txt in plan_edits(model, spec)]
out, rep = api.apply_replacements(ann, reps)
txt = fitz.open(stream=out, filetype="pdf")[0].get_text()
want = f"{40 * 1200 + 120 * 950 + 20 * 600:,.2f}"
check("annex: the 'Total HT' label survives a longer total", "Total HT" in txt, txt[-80:])
check("annex: the recomputed total is printed", want in txt, want)
check("annex: no field was dropped", not [w for w in rep.get("warnings", []) if "left unchanged" in w],
      str([w[:80] for w in rep.get("warnings", []) if "left unchanged" in w]))

# --- invisible OCR layer over a scanned page ---------------------------------
pic = fitz.open()
pp = pic.new_page(width=300, height=120)
pp.insert_text((20, 60), "Nom : Sara Idrissi", fontsize=14)
img = pp.get_pixmap(dpi=100).tobytes("png")
scan = fitz.open()
sp_ = scan.new_page(width=300, height=120)
sp_.insert_image(sp_.rect, stream=img)
sp_.insert_text((20, 60), "Nom : Sara Idrissi", fontsize=14, render_mode=3)
scan_b = scan.tobytes()
ss = [s for s in api.extract_spans(scan_b) if "Sara" in s["text"]]
check("scan: the OCR layer is marked invisible", ss and ss[0].get("invisible"), str(ss[:1]))
out, rep = api.apply_replacements(scan_b, [(ss[0], "Nom : Salma B")], try_inplace=True)
check("scan: editing invisible text is refused, bytes untouched", out == scan_b)
check("scan: the refusal says why",
      [r["reason"] for r in rep["in_place"]["refusals"]] == ["invisible_text"], str(rep["in_place"]))

# --- a redraw's push takes the line's whitespace-only spans with it ----------
demo = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
wsd = next(s for s in api.extract_spans(demo) if s["text"].startswith("www.1337.ma"))
out, rep = api.apply_replacements(demo, [(wsd, "Zoé Ångström-Ñuñez " + wsd["text"])],
                                  preserve_size=True, try_inplace=True)
row = fitz.Rect(0, wsd["bbox"][1] + 1, 595, wsd["bbox"][3] - 1)
got = fitz.open(stream=out, filetype="pdf")[0].get_text(clip=row).strip()
check("pushed line: still reads as ONE line, in order",
      got == "IF : 26055313, Site web : https://Zoé Ångström-Ñuñez www.1337.ma , email : "
             "contact@1337.ma", repr(got))

# --- a redrawn field is read where it was, not at the end of the page --------
esd = next(s for s in api.extract_spans(demo) if s["text"].startswith("Est inscrit(e) dans"))
out, rep = api.apply_replacements(demo, [(esd, "Est inscrit(e) dans notre éco")],
                                  preserve_size=True, try_inplace=True)
full = fitz.open(stream=out, filetype="pdf")[0].get_text()
i_cin, i_new, i_form = full.find("SI125678"), full.find("Est inscrit(e) dans notre éco"), full.find("formation")
check("redrawn field (two fonts): the edit went through the redraw", rep["in_place"]["count"] == 0)
check("redrawn field: the page reads it in its place, not after the signature",
      0 <= i_cin < i_new < i_form, f"CIN at {i_cin}, edit at {i_new}, next line at {i_form}")
check("redrawn field: moving it changed no pixel of the page",
      fitz.open(stream=out, filetype="pdf")[0].get_pixmap(dpi=96).samples
      == fitz.open(stream=_reading_order_off(demo, esd), filetype="pdf")[0].get_pixmap(dpi=96).samples)

# --- mid-line, in a page drawn as ONE text object (pdfTeX) -------------------
tex = fitz.open()
tp = tex.new_page(width=400, height=200)
tp.insert_text((0, 0), " ", fontname="helv", fontsize=1)       # font resources
tp.insert_text((0, 0), " ", fontname="tiro", fontsize=1)
fn = {f[3]: f[4] for f in tp.get_fonts(full=True)}
he, ti = fn["Helvetica"], fn["Times-Roman"]
# "search" in a second font: its own span, in the MIDDLE of the line
tex.update_stream(tp.get_contents()[0], (
    "BT /%s 11 Tf 40 150 Td [(To)-278(complete)-278(our)-278] TJ /%s 11 Tf (search) Tj "
    "/%s 11 Tf [-278(algorithm,)-278(we)] TJ 0 -14 Td [(need)-278(to)-278(describe)-278(it.)] TJ ET"
    % (he, ti, he)).encode())
texb = tex.tobytes()
tsd = next(s for s in api.extract_spans(texb) if s["text"].strip() == "search")
out, rep = api.apply_replacements(texb, [(tsd, tsd["text"].replace("search", "sea"))],
                                  try_inplace=False)
got = " ".join(fitz.open(stream=out, filetype="pdf")[0].get_text().split())
check("one-text-object page: a mid-line redraw reads in its place",
      got == "To complete our sea algorithm, we need to describe it.", repr(got))
check("one-text-object page: the reorder moved no pixel",
      fitz.open(stream=out, filetype="pdf")[0].get_pixmap(dpi=96).samples
      == fitz.open(stream=_no_reorder(texb, tsd, tsd["text"].replace("search", "sea")),
                   filetype="pdf")[0].get_pixmap(dpi=96).samples)

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
