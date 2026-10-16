"""test_font_gaps.py — new letters for ITC Franklin Gothic and condensed cuts on the IRS forms.

Before this, any edit on these fields that needed a letter outside the embedded subset was
refused ("only embedded as a subset"): the W-4 and W-9 titles (ITC Franklin Gothic Demi, which has
no open-source relative) and the 1040's condensed headings and notes (Helvetica Neue LT Std
-BdCn / -Cn, whose width word left the family unparsed). The letters now come from measured
substitutes: Libre Franklin at the weight whose stems match, and Roboto Condensed or Archivo
Narrow, whichever fits the cut's stems and widths.

Uses the audit corpus (spikes/audit/build_corpus.py); skips if it is not built.
"""
import io
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import fitz  # noqa: E402
import api  # noqa: E402
import font_extend as fe  # noqa: E402
import inplace_spike as ip  # noqa: E402
from pdf_editor import _split_width  # noqa: E402

FAIL = []
CORPUS = os.path.expanduser("~/.cache/redraft-audit/corpus")


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── the width word ───────────────────────────────────────────────────────────
for name, want in [("HelveticaNeueLTStd-BdCn", ("HelveticaNeueLTStd-Bd", True)),
                   ("HelveticaNeueLTStd-Cn", ("HelveticaNeueLTStd", True)),
                   ("HelveticaNeueLTStd-BlkCn", ("HelveticaNeueLTStd-Blk", True)),
                   ("Univers-Condensed", ("Univers", True)),
                   ("HelveticaNeueLTStd-Bd", ("HelveticaNeueLTStd-Bd", False)),
                   ("RobotoCondensed-Bold", ("RobotoCondensed-Bold", False))]:
    check(f"width word: {name}", _split_width(name) == want, _split_width(name))

if not os.path.isdir(CORPUS):
    print("SKIP - the audit corpus is not built (spikes/audit/build_corpus.py)")
    print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
    sys.exit(1 if FAIL else 0)


def subset_of(doc_name, font):
    doc = fitz.open(os.path.join(CORPUS, doc_name))
    return doc.xref_stream(ip._simple_cff_refs(doc, font)["ff_xref"])


def stem_error(subset, donor):
    """Stem ("l") of *donor* at the uniform scale extend_cff_font uses, against the subset's."""
    from fontTools.cffLib import CFFFontSet
    from fontTools.ttLib import TTFont
    from fontTools.pens.boundsPen import BoundsPen
    import math
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(subset), fe._cff_stub())
    cs = cff[cff.fontNames[0]].CharStrings
    cap, xh = fe._cff_landmarks(cs)
    pen = BoundsPen(cs)
    cs["l"].draw(pen)
    own = pen.bounds[2] - pen.bounds[0]
    f = TTFont(io.BytesIO(donor))
    sc = 1000.0 / f["head"].unitsPerEm
    dc, dx = fe._tt_landmarks(f, sc)
    rs = [a / b for a, b in ((cap, dc), (xh, dx)) if a and b]
    sc *= math.prod(rs) ** (1 / len(rs))
    gs = f.getGlyphSet()
    pen = BoundsPen(gs)
    gs[f.getBestCmap()[ord("l")]].draw(pen)
    return abs((pen.bounds[2] - pen.bounds[0]) * sc - own) / own


def edit(doc_name, pick, old, new):
    data = open(os.path.join(CORPUS, doc_name), "rb").read()
    api._ingest_embedded_fonts(data, "")
    sd = next(s for s in api.extract_spans(data) if pick(s) and old in s["text"])
    nt = sd["text"].replace(old, new, 1)
    out, rep = api.apply_replacements(data, [(sd, nt)], try_inplace=True)
    page = fitz.open(stream=out, filetype="pdf")[sd["page"]]
    drawn = [s for s in api.extract_spans(out)
             if s["page"] == sd["page"] and abs(s["origin"][1] - sd["origin"][1]) < 1 and new in s["text"]]
    return sd, out, rep, page, drawn


# ── ITC Franklin Gothic Demi -> Libre Franklin, at the weight whose stems match ──
sub = subset_of("irs-w4.pdf", "ITCFranklinGothicStd-Demi")
named, kind = fe.resolve_donor_detailed("ITCFranklinGothicStd-Demi")
check("Franklin: a substitute is found", bool(named) and kind == "substitute", kind)
if named:
    chosen = fe.pick_substitute(sub, "ITCFranklinGothicStd-Demi", named)
    check("Franklin: the chosen weight's stems are within 5% of Demi's",
          stem_error(sub, chosen) < 0.05, round(stem_error(sub, chosen), 3))
    check("Franklin: ...which the named weight (SemiBold) is not", stem_error(sub, named) > 0.10,
          round(stem_error(sub, named), 3))

for doc_name, old, new in [("irs-w4.pdf", "Employee’s Withholding", "Employer’s Tax Withholding"),
                           ("irs-w9.pdf", "Request", "Demand")]:
    sd, out, rep, page, drawn = edit(doc_name, lambda s: s["font"].startswith("ITCFranklin"), old, new)
    check(f"{doc_name} title '{new}': made in place", rep["in_place"]["count"] == 1, rep["in_place"]["refusals"])
    check(f"{doc_name} title '{new}': reads back", new in page.get_text())
    check(f"{doc_name} title '{new}': in the document's own font", any(s["font"] == sd["font"] for s in drawn),
          [s["font"] for s in drawn])

# ── condensed cuts ───────────────────────────────────────────────────────────
# letters the subsets lack: M (BdCn has no M), b and v (Cn has neither)
for font, old, new in [("HelveticaNeueLTStd-Cn", "more than half", "above half"),
                       ("HelveticaNeueLTStd-BdCn", "Filing Status", "Marital Status")]:
    sd, out, rep, page, drawn = edit("irs-1040.pdf", lambda s, f=font: s["font"] == f, old, new)
    dropped = [w for w in rep["warnings"] if "left unchanged" in w]
    check(f"1040 {font} '{new}': made in place", rep["in_place"]["count"] == 1, rep["in_place"]["refusals"])
    check(f"1040 {font} '{new}': applied", not dropped and new in page.get_text(), dropped or rep["in_place"])
    check(f"1040 {font} '{new}': in the document's own font", any(s["font"] == font for s in drawn),
          [s["font"] for s in drawn])
    check(f"1040 {font} '{new}': nothing drawn as a box", not any("�" in s["text"] for s in drawn))

# a regular-width substitute is held off a condensed cut by the width guard
sub = subset_of("irs-1040.pdf", "HelveticaNeueLTStd-BdCn")
arimo, _ = fe.resolve_donor_detailed("HelveticaNeueLTStd-Bd")
try:
    fe.extend_cff_font(sub, arimo, ["C"], substitute=True)
    check("a regular-width substitute is refused for a condensed cut", False, "accepted")
except ValueError as exc:
    check("a regular-width substitute is refused for a condensed cut", "widths are off" in str(exc), exc)

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
