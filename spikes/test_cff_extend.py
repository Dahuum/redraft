"""test_cff_extend.py — injecting a glyph into a CFF (Type1C) subset.

The TrueType path cannot do this at all. A PDF's /FontFile3 holds a BARE CFF
program with no SFNT wrapper, so TTFont() rejects it with "bad sfntVersion",
and font_extend.extend_font refuses anything that is not glyf. Every
Adobe-produced document embeds its fonts that way — the whole IRS form set,
and most of what Distiller and InDesign emit — so an edit on one of those
files that needs a character outside the subset could only ever be refused.
Measured on a 23-document corpus, that is 17 of 69 refusals.

The donor cannot be trusted unmeasured. correct_external_donor, which the
TrueType paths use, needs glyf outlines on BOTH sides and returns None for a
CFF target, so extend_cff_font measures the donor against landmarks read from
the subset's own glyphs and refuses when they disagree. Injecting unmeasured
is how a Tw Cen MT field came to be drawn with Poppins letterforms 33% too
tall; the point of this tier is to be unable to repeat that.

Downloads the IRS 1040 rather than committing it, consistent with this repo's
convention. Skips cleanly if the network is unavailable.
"""
import glob
import io
import os
import random
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
import font_extend as fe  # noqa: E402
from fontTools.cffLib import CFFFontSet  # noqa: E402
from fontTools.pens.boundsPen import BoundsPen  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


try:
    req = urllib.request.Request("https://www.irs.gov/pub/irs-pdf/f1040.pdf",
                                 headers={"User-Agent": "redraft-test/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        pdf = r.read()
except Exception as e:  # noqa: BLE001
    print(f"SKIP — couldn't download the IRS 1040 ({e}).")
    sys.exit(0)

subset = None
base_name = ""
d = fitz.open(stream=pdf, filetype="pdf")
for pno in range(min(3, d.page_count)):
    for f in d[pno].get_fonts(full=True):
        if f[1] == "cff":
            subset = d.extract_font(f[0])[3]
            base_name = f[3]
            break
    if subset:
        break
d.close()
check("the 1040 embeds a bare CFF subset", subset is not None, "no cff font found")
if not subset:
    print("RESULT: 1 FAILED"); sys.exit(1)
print(f"  subset: {base_name} ({len(subset)} bytes)")

# It really is unreadable as SFNT — this is why a separate path exists.
from fontTools.ttLib import TTFont, TTLibError  # noqa: E402
try:
    TTFont(io.BytesIO(subset))
    check("a bare CFF is not loadable as SFNT", False, "TTFont accepted it")
except TTLibError:
    check("a bare CFF is not loadable as SFNT", True)

cff = CFFFontSet()
cff.decompile(io.BytesIO(subset), fe._cff_stub())
cs = cff[cff.fontNames[0]].CharStrings
cap, xh = fe._cff_landmarks(cs)
print(f"  subset landmarks: cap {cap}, x-height {xh}")
check("landmarks readable from the subset's own glyphs", bool(cap and xh), f"{cap}, {xh}")

ACCENTS = ["é", "Å", "ñ", "ö"]
check("the accented glyphs really are absent from the subset",
      not any(fe._glyph_name_for(c) in cs for c in ACCENTS))

donor, kind = fe.resolve_donor_detailed(base_name.split("+")[-1])
check("a donor resolves for this font", donor is not None, f"kind={kind}")

if donor:
    r = fe.extend_cff_font(subset, donor, ACCENTS)
    print(f"  injected via {kind} donor: worst {r['landmark_checked']} error "
          f"{r['landmark_error'] * 100:.1f}%")
    check("the donor passes the size gate", r["landmark_error"] <= fe._CFF_MAX_LANDMARK_ERR,
          f"{r['landmark_error']:.3f}")
    check("extending grows the program", len(r["font_bytes"]) > len(subset))

    chk = CFFFontSet()
    chk.decompile(io.BytesIO(r["font_bytes"]), fe._cff_stub())
    ccs = chk[chk.fontNames[0]].CharStrings
    for ch in ACCENTS:
        nm = r["names"][ch]
        present = nm in ccs
        bounds = None
        if present:
            bp = BoundsPen(None)
            ccs[nm].draw(bp)
            bounds = bp.bounds
        check(f"{ch!r} reads back with an outline", present and bounds and bounds[3] > 0,
              f"{nm}: {bounds}")
        # A letter drawn at the wrong scale is the failure this tier exists to
        # prevent, so assert the SIZE, not merely the presence.
        if bounds:
            check(f"{ch!r} is the right height for this font",
                  bounds[3] <= cap * 1.35 and bounds[3] > xh * 0.7,
                  f"top {bounds[3]} against cap {cap} / x-height {xh}")
    check("widths come back for every char",
          all(r["width_1000"].get(c, 0) > 0 for c in ACCENTS), repr(r["width_1000"]))

    # A glyph the subset ALREADY has must not be re-injected, and its width
    # must come from the subset rather than the donor.
    r2 = fe.extend_cff_font(subset, donor, ["A"])
    a_bounds = fe._cff_bounds(cs, "A")
    check("a glyph already present is left alone",
          abs(r2["width_1000"]["A"] - fe._cff_advance(cs, "A")) < 0.5,
          f"{r2['width_1000']['A']} vs {fe._cff_advance(cs, 'A')}")

# The gate has to be able to say no, or it is not a gate. Wrong-shaped
# typefaces — serif, monospace, ultra-expanded — must not get through.
acc = ref = 0
examples = []
fonts = sorted(glob.glob("/usr/share/fonts/**/*.ttf", recursive=True))
random.Random(3).shuffle(fonts)
for p in fonts[:40]:
    try:
        fe.extend_cff_font(subset, open(p, "rb").read(), ["é"])
        acc += 1
    except ValueError as exc:
        ref += 1
        if "off by" in str(exc):
            examples.append(os.path.basename(p))
print(f"  of 40 installed fonts as donor: {acc} accepted, {ref} refused")
if fonts:
    check("the size gate refuses most arbitrary donors", ref > acc,
          f"{acc} accepted vs {ref} refused")
    check("at least one refusal was on size", bool(examples), "none cited size")

for bad, why in ((b"\x00\x01\x00\x00not-a-cff", "a non-CFF input"),):
    try:
        fe.extend_cff_font(bad, donor or b"", ["é"])
        check(f"{why} raises", False, "no exception")
    except ValueError:
        check(f"{why} raises", True)
    except Exception as exc:  # noqa: BLE001
        check(f"{why} raises ValueError", False, type(exc).__name__)

if donor:
    try:
        fe.extend_cff_font(subset, donor, ["字"])
        check("a char with no Adobe glyph name raises", False, "no exception")
    except ValueError:
        check("a char with no Adobe glyph name raises", True)

print(f"\n{'=' * 70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
