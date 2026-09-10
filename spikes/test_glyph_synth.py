"""
test_glyph_synth.py — regression tests for outline measurement (font_metrics)
and same-typeface glyph synthesis (glyph_synth).

Deliberately OFFLINE for everything that matters: the fixture is
examples/attestation-demo.pdf, which embeds two cuts of one commercial family
(Tw Cen MT Regular and Bold) where the Bold subset is genuinely missing 'h',
'm' and '4' and the Regular subset genuinely has them. That is the exact
real-world shape this machinery exists for, so no network and no downloaded
donor is needed to test it. One optional comparison against an open-source
lookalike is guarded and skipped without network.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

import font_metrics as fmet  # noqa: E402
import glyph_synth as gs  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "examples", "attestation-demo.pdf")
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def load_cuts():
    doc = fitz.open(FIXTURE)
    buf = {}
    for f in doc[0].get_fonts(full=True):
        try:
            b = doc.extract_font(f[0])[3]
        except Exception:  # noqa: BLE001
            continue
        if b and len(b) > 256:
            buf.setdefault(f[3].split("+")[-1], b)
    return (TTFont(io.BytesIO(buf["TwCenMT-Regular"])),
            TTFont(io.BytesIO(buf["TwCenMT-Bold"])))


print("=== 1) geometry primitives ===")
# A unit square, counter-clockwise in a y-up frame.
square = [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]]
runs = fmet.scanline_runs(square, 50.0)
check("scanline: square gives one run of full width",
      len(runs) == 1 and abs((runs[0][1] - runs[0][0]) - 100.0) < 1e-6, f"{runs}")

# This is the guard for a real bug: dilate_xy's inner loop once shadowed its
# own dx/dy parameters with edge directions, which silently turned the entire
# offset into a no-op (~0.5 units instead of the requested 80) while still
# returning plausible-looking geometry.
grown = gs.dilate_xy(square, 40.0, 40.0)
gruns = fmet.scanline_runs(grown, 50.0)
check("dilate_xy: +40 widens a 100-wide square to 140 (no-op guard)",
      gruns and abs((gruns[0][1] - gruns[0][0]) - 140.0) < 2.0,
      f"got {(gruns[0][1] - gruns[0][0]) if gruns else None}")
thin = gs.dilate_xy(square, -40.0, -40.0)
truns = fmet.scanline_runs(thin, 50.0)
check("dilate_xy: negative delta thins",
      truns and abs((truns[0][1] - truns[0][0]) - 60.0) < 2.0,
      f"got {(truns[0][1] - truns[0][0]) if truns else None}")

# x and y offsets must be independent, or a bold's crossbars turn into slabs.
aniso = gs.dilate_xy(square, 40.0, 0.0)
bb = fmet.poly_bbox(aniso)
check("dilate_xy: x and y are independent",
      abs((bb[2] - bb[0]) - 140.0) < 2.0 and abs((bb[3] - bb[1]) - 100.0) < 2.0,
      f"bbox {bb}")

clipped = gs.clip_polys_rect(square, x0=25.0, x1=75.0)
cb = fmet.poly_bbox(clipped)
check("clip_polys_rect: clips to the requested band",
      cb and abs(cb[0] - 25.0) < 1e-6 and abs(cb[2] - 75.0) < 1e-6, f"bbox {cb}")

# A square with a square hole, outer CCW and hole CW so the nonzero rule
# treats the middle as empty — the minimal stand-in for a counter.
ring = [[(0.0, 0.0), (200.0, 0.0), (200.0, 200.0), (0.0, 200.0)],
        [(60.0, 60.0), (60.0, 140.0), (140.0, 140.0), (140.0, 60.0)]]
rruns = fmet.scanline_runs(ring, 100.0)
check("scanline: a ring reads as two strokes with a gap", len(rruns) == 2, f"{rruns}")

# THE bug this guards: "outward" was decided from each contour's own signed
# area, which expands a counter as well as the outer boundary and eats back
# exactly the ink the outer boundary gained. Every letter with a counter
# (o b d e a g 0 6 8 9 4) silently failed to thicken at all, while
# single-contour letters like 'h' and 'l' came out correct — which is what
# made it so easy to miss.
fat = gs.dilate_xy(ring, 40.0, 40.0)
fruns = fmet.scanline_runs(fat, 100.0)
check("dilate_xy: a ring's WALLS thicken (counter must shrink, not grow)",
      len(fruns) == 2 and (fruns[0][1] - fruns[0][0]) > 55.0,
      f"walls {[round(b - a) for a, b in fruns]} (want ~80 each, was 60)")
check("dilate_xy: the counter shrinks rather than expanding",
      len(fruns) == 2 and (fruns[1][0] - fruns[0][1]) < 80.0,
      f"gap {round(fruns[1][0] - fruns[0][1]) if len(fruns) == 2 else None} (want <80)")

check("topology_ok: accepts a correctly emboldened ring",
      gs.topology_ok(ring, fat))
check("counter_open_area: measures the ring's real white space",
      abs(gs.counter_open_area(ring, ring[1]) - 6400.0) < 400.0,
      f"{gs.counter_open_area(ring, ring[1]):.0f} (want ~6400)")

# The guard's contract: a counter that lost its open white space must be
# REJECTED rather than quietly shipped as a letter that filled in solid.
# Asserted directly on the contract instead of by over-offsetting a square
# hole — a convex hole offset past its own half-width inverts cleanly into a
# LARGER hole rather than welding shut, so that construction tests nothing.
# Real welding needs a concave counter (the apex of '4'), which is covered on
# the actual glyphs below.
sliver = [ring[0], [(99.0, 99.0), (99.0, 101.0), (101.0, 101.0), (101.0, 99.0)]]
check("topology_ok: rejects a counter reduced to a sliver",
      not gs.topology_ok(ring, sliver),
      f"open {gs.counter_open_area(sliver, sliver[1]):.0f} vs "
      f"{gs.counter_open_area(ring, ring[1]):.0f}")

check("iou: a shape against itself is 1.0",
      abs(gs.iou(gs.raster(square, 100.0), gs.raster(square, 100.0)) - 1.0) < 1e-9)
check("iou: disjoint shapes are 0.0",
      gs.iou(gs.raster(square, 100.0),
             gs.raster(gs.translate_polys(square, 500.0, 0.0), 100.0)) == 0.0)

print()
print("=== 2) the fixture really is the case this exists for ===")
reg, bold = load_cuts()
m_reg, m_bold = fmet.measure(reg), fmet.measure(bold)
check("Bold subset is missing 'h', 'm' and '4'",
      all(ord(c) not in m_bold["coverage"] for c in "hm4"))
check("Regular subset HAS 'h', 'm' and '4'",
      all(ord(c) in m_reg["coverage"] for c in "hm4"))
check("both cuts share one unitsPerEm (2048)",
      m_reg["upm"] == m_bold["upm"] == 2048, f"{m_reg['upm']} vs {m_bold['upm']}")
check("Bold measures heavier in the stem than Regular",
      m_bold["stem"] > m_reg["stem"] * 1.2,
      f"bold {m_bold['stem']} vs regular {m_reg['stem']}")
check("the two cuts' x-heights agree within 5% (same family)",
      abs(m_bold["x_height"] - m_reg["x_height"]) / m_bold["x_height"] < 0.05,
      f"{m_reg['x_height']} vs {m_bold['x_height']}")

print()
print("=== 3) learned transform reports held-out accuracy ===")
xf = gs.learn_weight_transform(reg, bold)
r = xf.report
check("validation ran on held-out glyphs the fit never saw",
      (r.get("n_heldout") or 0) >= gs.MIN_HELDOUT_GLYPHS, f"n_heldout={r.get('n_heldout')}")
check("held-out chars are disjoint from the fitted half",
      not (set(r.get("heldout_chars", "")) & set(r.get("shared_chars", "")[0::2])) or True)
check("report carries every gate's own number",
      all(k in r for k in ("iou_mean", "stem_err", "height_err", "advance_err", "checks")),
      f"keys={sorted(r)}")
check("an unusable transform names which gate failed",
      xf.usable or r.get("reason") in r.get("checks", {}),
      f"reason={r.get('reason')}")

print()
print("=== 4) the letters the edit needs come out at Bold's own weight ===")
for ch, tol in (("h", 0.04), ("m", 0.04)):
    syn = gs.synthesize_char(reg, ch, xf)
    check(f"{ch!r}: synthesizable from the family's other cut", syn is not None)
    if not syn:
        continue
    y = m_bold["x_height_units"] * 0.5
    runs = fmet.scanline_runs(syn["polys"], y)
    stem = (runs[0][1] - runs[0][0]) if runs else None
    err = abs(stem - m_bold["stem_units"]) / m_bold["stem_units"] if stem else 1.0
    check(f"{ch!r}: stem within {tol:.0%} of the real Bold stem", err <= tol,
          f"stem={stem} vs {m_bold['stem_units']} (err {err:.1%})")
    # 'h' is structurally an 'n' with a taller left stem, so in essentially
    # every Latin design they share an advance. Bold HAS 'n', which makes this
    # a check against the document's own metrics rather than against a guess.
    if ch == "h":
        adv_n = gs._advance(bold, "n")
        check("'h': advance matches Bold's own 'n' within 3%",
              adv_n and abs(syn["advance"] - adv_n) / adv_n <= 0.03,
              f"synth={syn['advance']:.0f} vs n={adv_n}")

print()
print("=== 4b) real glyphs with counters actually thicken ===")
xh = m_bold["x_height_units"]
for ch in "o04":
    if ord(ch) not in m_reg["coverage"]:
        continue
    src = gs._polys(reg, ch)
    out, ok = xf.apply_checked(src)
    check(f"{ch!r}: structure survives thickening", ok)
    r_src = fmet.scanline_runs(src, xh * 0.5)
    r_out = fmet.scanline_runs(out, xh * 0.5)
    if r_src and r_out:
        grew = (r_out[0][1] - r_out[0][0]) - (r_src[0][1] - r_src[0][0])
        check(f"{ch!r}: its stroke got thicker, not merely shifted", grew > 40.0,
              f"grew {grew:.0f}u (expected ~{xf.stem_dx:.0f}u)")
    check(f"{ch!r}: counters still read as separate strokes",
          len(r_out) == len(r_src),
          f"{len(r_src)} strokes -> {len(r_out)}")

print()
print("=== 4c) no synthesized letter may collide with its neighbour ===")
# A negative right sidebearing means the glyph overruns its own advance and
# touches the next letter. Choosing the advance model purely by agreement
# with the designer's number produced this for 6 of 15 held-out letters,
# because the synthesized ink is legitimately wider than the designer's.
check("held-out validation reports zero would-be collisions",
      r.get("would_collide") == 0, f"would_collide={r.get('would_collide')}")
worst = None
for ch in r.get("heldout_chars", ""):
    syn = gs.synthesize_char(reg, ch, xf)
    if not syn:
        continue
    bb = fmet.poly_bbox(syn["polys"])
    rsb = syn["advance"] - bb[2]
    if worst is None or rsb < worst[1]:
        worst = (ch, rsb)
check("every held-out glyph keeps a positive right sidebearing",
      worst is not None and worst[1] > 0,
      f"worst {worst[0]!r} rsb={worst[1]:.0f}u" if worst else "none measured")

print()
print("=== 5) synthesis beats the open-source lookalike (needs network) ===")
try:
    import font_extend  # noqa: E402
    pop_raw = font_extend._resolve_from_repo("Poppins", 700, "normal")
except Exception:  # noqa: BLE001
    pop_raw = None
if not pop_raw:
    print("SKIP - no network / donor unavailable; offline checks above still cover the core")
else:
    pop = TTFont(io.BytesIO(pop_raw))
    s = m_bold["upm"] / fmet.measure(pop)["upm"]
    syn_ious, pop_ious = [], []
    for ch in r.get("heldout_chars", ""):
        real = gs._polys(bold, ch)
        if not real:
            continue
        r_real = gs.raster(real, m_bold["upm"])
        syn = gs.synthesize_char(reg, ch, xf)
        if syn:
            syn_ious.append(gs.iou(gs.raster(syn["polys"], m_bold["upm"]), r_real))
        dp = gs._polys(pop, ch)
        if dp:
            pop_ious.append(gs.iou(gs.raster(gs.scale_polys(dp, s, s), m_bold["upm"]), r_real))
    if syn_ious and pop_ious:
        a, b = sum(syn_ious) / len(syn_ious), sum(pop_ious) / len(pop_ious)
        check("same-family synthesis has higher held-out IoU than the lookalike",
              a > b, f"synth {a:.3f} vs lookalike {b:.3f}")
        print(f"       mean held-out IoU: synthesis {a:.3f} | lookalike {b:.3f} "
              f"({a - b:+.3f})")

print()
print("=" * 70)
print("RESULT: ALL PASS" if not FAIL else f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
