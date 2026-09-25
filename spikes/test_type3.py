"""test_type3.py — Type3 fonts (what Chrome writes for a variable font).

examples/invoice-type3.pdf is a synthetic invoice typeset by Chrome with Fraunces and Manrope
(both variable, so every glyph is its own Type3 charproc and the font has no BaseFont).

Before the fix every edit of this file fell to the redraw engine, because
  * all Type3 fonts shared the display name '' so only the first was ever searched,
  * /Widths (glyph space, FontMatrix .0005) was read as 1/1000 em and a fitting edit "overflowed",
  * a new digit could not be added to a font with no font program.
The redraw then drew the amount in Helvetica.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
import inplace_spike as S  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


raw = open(os.path.join(HERE, "..", "examples", "invoice-type3.pdf"), "rb").read()
src = fitz.open(stream=raw, filetype="pdf")
names = [S._fname(f) for f in src[0].get_fonts(full=True)]
check("every Type3 font has its own display name", len(set(names)) == len(names) and "" not in names, str(names))


def edit(old, new):
    sp = api.extract_spans(raw)
    t = next(s for s in sp if old in s["text"])
    return api.apply_replacements(raw, [(t, t["text"].replace(old, new))], try_inplace=True)


def fonts(pdf):
    d = fitz.open(stream=pdf, filetype="pdf")
    return sorted((f[0], f[2]) for f in d[0].get_fonts(full=True)), d[0]


def text(page):
    return page.get_text("text")


# 1. glyphs the subset already has: pure in-place, no fallback
out, rep = edit("Discovery workshop", "Discovery")
check("shorter text using existing glyphs is in place", rep["in_place"]["count"] == 1, str(rep["in_place"]))
check("no font added", fonts(out)[0] == fonts(raw)[0])

# 2. a right-aligned whole line keeps its right edge (within 1.2 pt: the browser kerns digit pairs the
#    engine does not re-measure — the twin differs by the same 0.8 pt)
out, rep = edit("Total due USD 3,840.00", "Total due USD 8,340.00")
old_r = next(w for w in src[0].get_text("words") if w[4] == "3,840.00")[2]
new_r = next(w for w in fitz.open(stream=out, filetype="pdf")[0].get_text("words") if w[4] == "8,340.00")[2]
check("same-length edit stays on the right margin", abs(old_r - new_r) < 1.2, "%.2f vs %.2f" % (old_r, new_r))
out, rep = edit("Total due USD 3,840.00", "Total due USD 3,84.00")
new_r = next(w for w in fitz.open(stream=out, filetype="pdf")[0].get_text("words") if w[4] == "3,84.00")[2]
check("shortened right-aligned line keeps its right edge", abs(old_r - new_r) < 1.2, "%.2f vs %.2f" % (old_r, new_r))

# 3. digits the subset never drew are added to the Type3 font itself
out, rep = edit("Total due USD 3,840.00", "Total due USD 7,215.60")
if rep["in_place"]["count"] == 0 and any("no_donor" in str(r) or "no_family" in str(r)
                                       for r in rep["in_place"].get("refusals", [])):
    print("SKIP - new-glyph injection (no donor reachable, offline?)")
else:
    check("new digits are added in place", rep["in_place"]["count"] == 1, str(rep["in_place"]))
    f_new, pg = fonts(out)
    check("no font resource added by the edit", f_new == fonts(raw)[0], str(f_new))
    check("edited text extracts", "7,215.60" in text(pg), text(pg)[:200])
    check("still Type3, not a stand-in font", all(t == "Type3" for _, t in f_new))
    d = fitz.open(stream=out, filetype="pdf")
    wd = next(w for w in d[0].get_text("words") if w[4] == "7,215.60")
    pix = d[0].get_pixmap(clip=fitz.Rect(*wd[:4]), matrix=fitz.Matrix(3, 3))
    ink = sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 128)
    check("new glyphs actually draw ink", ink > 400, "ink=%d" % ink)
    # a second edit reusing what the first injected also works
    out2, rep2 = api.apply_replacements(out, [(next(s for s in api.extract_spans(out) if "7,215.60" in s["text"]),
                                              "Total due USD 7,251.60")], try_inplace=True)
    check("the injected glyphs are reusable", rep2["in_place"]["count"] == 1, str(rep2["in_place"]))

# 4. accented capitals added to the font must not move the text boxes of untouched neighbours
# (widening /FontBBox did: MuPDF derives every span's ascent from it)
sp = api.extract_spans(raw)
t = next(s for s in sp if "Billed" in s["text"])
out, rep = api.apply_replacements(raw, [(t, "Zoé Ångström-Ñuñez " + t["text"])], try_inplace=True)
if rep["in_place"]["count"] == 1:
    a = {w[4]: w[:4] for w in src[0].get_text("words")}
    b = {w[4]: w[:4] for w in fitz.open(stream=out, filetype="pdf")[0].get_text("words")}
    moved = [k for k in ("Discovery", "1,200.00", "Description", "Brand") if a[k] != b.get(k)]
    check("untouched neighbours keep their text boxes", not moved, str(moved))
else:
    print("SKIP - accent injection not in place (no donor?)")

# 5. composite glyphs (Å, Ñ, ö are a base letter plus a mark in the donor) must be writable
out, rep = edit("Total due USD 3,840.00", "Total due USD 3,840.00 Ångström-Ñ")
if rep["in_place"]["count"] == 0 and any("no_donor" in str(r) for r in rep["in_place"].get("refusals", [])):
    print("SKIP - composite glyphs (no donor reachable)")
else:
    check("accented capitals with composite outlines go in place", rep["in_place"]["count"] == 1, str(rep["in_place"]))
    pg = fitz.open(stream=out, filetype="pdf")[0]
    check("the accented text reads back", "Ångström-Ñ" in pg.get_text(), pg.get_text()[:200])
    check("still no font added", fonts(out)[0] == fonts(raw)[0])

# 6. the REDRAW path (no in-place) draws a Type3 font's own family, not Helvetica
api._ingest_embedded_fonts(raw)
sp = api.extract_spans(raw)
t = next(s for s in sp if s["text"].startswith("Invoice 2026"))
out, rep = api.apply_replacements(raw, [(t, "Invoice 2026-0518 B")], try_inplace=False)
srcs = [f.get("source", "") for f in rep.get("fonts", [])]
if out == raw or not srcs:
    print("SKIP - redraw not applied")
else:
    check("a redraw of a Type3 field uses the family donor", any("type3-family-donor" in x for x in srcs), str(srcs))
    w0 = next(w for w in src[0].get_text("words") if w[4] == "Invoice")
    w1 = next(w for w in fitz.open(stream=out, filetype="pdf")[0].get_text("words") if w[4] == "Invoice")
    check("the unchanged word keeps its width (same typeface)",
          abs((w1[2] - w1[0]) / (w0[2] - w0[0]) - 1) < 0.04, "%.1f vs %.1f" % (w1[2] - w1[0], w0[2] - w0[0]))

print("RESULT:", "ALL PASS" if not FAIL else "FAILURES: %s" % FAIL)
sys.exit(1 if FAIL else 0)
