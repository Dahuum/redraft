"""Generate examples/subset-font-demo.pdf.

A synthetic "attestation"-style PDF that reproduces the exact font condition
behind the inplace_spike.py missing-glyph bug (see backend/inplace_spike.py,
`_simple_font_glyph_coverage`): an embedded Bold TrueType font, declared with
/Encoding /WinAnsiEncoding (a "simple" font, not Type0/CID), whose subset
never includes the letter 'h' or the digit '4' — because none of the
bold-styled demo text below uses them, the same way a real document's
embedded subset only ever contains the glyphs its own original text needed.

All names/IDs on the page are fabricated for this demo — not a real person's
document. `set_simple=True` on insert_font is what makes PyMuPDF emit a
classic simple/WinAnsiEncoding font instead of its default Type0/CID
embedding; without it this fixture would exercise a different code path
(inplace_spike.py's CID branch, which already had missing-glyph detection
before this fix).

Regenerate with: python3 examples/make_subset_font_demo.py
"""
import os

import fitz

OUT_PATH = os.path.join(os.path.dirname(__file__), "subset-font-demo.pdf")
REGULAR_FONT = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
BOLD_FONT = "/usr/share/fonts/noto/NotoSans-Bold.ttf"

doc = fitz.open()
page = doc.new_page(width=595, height=842)
page.insert_font(fontname="DemoReg", fontfile=REGULAR_FONT, set_simple=True)
page.insert_font(fontname="DemoBold", fontfile=BOLD_FONT, set_simple=True)


def reg(pt, text, size=11, color=(0.1, 0.1, 0.1)):
    page.insert_text(pt, text, fontname="DemoReg", fontsize=size, color=color)


def bold_text(pt, text, size=11, color=(0.1, 0.1, 0.1)):
    page.insert_text(pt, text, fontname="DemoBold", fontsize=size, color=color)


reg((72, 60), "DEMO FIXTURE — fabricated data, not a real document", size=9, color=(0.6, 0.1, 0.1))

bold_text((72, 110), "Attestation de Scolarite (demo)", size=20)

reg((72, 150), "Ref : DEMO-0001", size=11)

reg((72, 190), "La Direction certifie que :", size=11)

reg((72, 220), "M /Mme", size=11)
bold_text((120, 220), "Sara Idrissi,", size=13)

reg((72, 245), "Ne(e) le", size=11)
bold_text((125, 245), "12/03/2003", size=13)
reg((225, 245), "a", size=11)
bold_text((235, 245), "Rabat,", size=13)

reg((72, 270), "CIN/Passeport n :", size=11)
bold_text((190, 270), "XX000000", size=13)

reg((72, 310), "Fait pour servir et valoir ce que de droit.", size=11)

# Shrink each embedded font to only the glyphs actually used above — this is
# what leaves the Bold subset without 'h'/'4', reproducing real-world
# subsetting behavior instead of asserting it by hand.
doc.subset_fonts()
doc.save(OUT_PATH, garbage=4, deflate=True)
doc.close()

# Self-check: fail loudly if a future edit to the demo text above
# accidentally adds 'h' or '4' to the bold field and silently stops
# reproducing the bug this fixture exists to demonstrate.
doc = fitz.open(OUT_PATH)
for f in doc[0].get_fonts(full=True):
    if "Bold" not in f[3]:
        continue
    from fontTools.ttLib import TTFont
    import io

    _, _ext, _ftype, buf = doc.extract_font(f[0])
    cmap = TTFont(io.BytesIO(buf)).getBestCmap()
    assert ord("h") not in cmap, "fixture no longer reproduces the bug: Bold subset has 'h'"
    assert ord("4") not in cmap, "fixture no longer reproduces the bug: Bold subset has '4'"
doc.close()

print(f"saved {OUT_PATH}")
