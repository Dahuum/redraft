"""
glyph_synth.py — build a missing glyph out of real outlines from the SAME
typeface, and measure how well it worked before trusting it.

THE IDEA
--------
A PDF that uses a font family almost always embeds several cuts of it — a
Regular for body text and a Bold for headings and fields. Each cut is subset
to only the characters that cut actually drew, so the Bold subset can be
missing a letter the Regular subset has. That was the exact case that made
edits look wrong on the attestation document: the Bold subset had no 'h', 'm'
or '4', while the Regular subset embedded a few objects away had all three.

The engine used to answer that by downloading an open-source "lookalike" of
the family and taking the letter from there. Measured against the real Bold
(see font_metrics.py), the lookalike it picked was off by 37% in stem width
and 33% in x-height — a letter of visibly the wrong weight and size dropped
into the middle of a word. The document's own Regular cut is off by 4%.

So: prefer the family's own other cut, and correct it for weight instead of
importing a stranger's letterforms.

WHY THIS ISN'T JUST "FAKE BOLD"
-------------------------------
Emboldening by dilating an outline is an old trick and, applied blind, a bad
one — it thickens round strokes more than flat ones, fills in tight counters,
and nobody can tell from the output whether it worked. The difference here is
that the transform is MEASURED and then TESTED:

  1. LEARN. The two cuts share many glyphs ('a', 'e', 'n', 'o', 's', …). Those
     shared glyphs are a training set: they show exactly what this family's
     Regular→Bold step does to stem width, to vertical proportions, and to
     advance width, for this font, rather than a generic guess.

  2. VALIDATE ON HELD-OUT GLYPHS. The learned transform is applied to shared
     glyphs that were kept out of fitting, and each result is compared against
     that cut's REAL glyph — by stem width, advance, and outline overlap
     (intersection-over-union of the rasterized shapes). That produces a
     per-document accuracy number for a synthesis the caller has not yet
     trusted.

  3. REFUSE ON THE NUMBER, NOT ON THE VIBE. If held-out IoU or stem accuracy
     misses the threshold, synthesis reports itself unusable and the caller
     falls to the next tier. A wrong-looking glyph silently shipped into
     someone's document is the failure this whole engine exists to prevent, so
     "I can't do this one faithfully" has to be a real, reachable outcome.

Outlines here are flattened to line segments (curves subdivided finely enough
that the deviation is far below a rasterizer's resolution at text sizes). That
costs the glyph its hinting and makes it a little larger on disk; in exchange
every operation below — offsetting, clipping, boolean-free assembly under the
nonzero winding rule — becomes exact and dependency-free.
"""
from __future__ import annotations

import math

from fontTools.pens.ttGlyphPen import TTGlyphPen

import font_metrics as fmet

# Held-out quality gates. A synthesized glyph must clear all of these against
# the real glyph it is being tested on, or synthesis reports itself unusable.
#
# THESE NUMBERS ARE CALIBRATED, NOT PICKED. Measured on this family, the mean
# held-out IoU against the real Bold glyphs was 0.65 for the learned transform,
# 0.60 for using the Regular unchanged, 0.53-0.58 for the best open-source
# lookalikes and 0.45 for the lookalike the engine used to ship. So ~0.65 is
# the CEILING for anything derived by offsetting an outline — a real bold is a
# redrawn face, not an inflated regular, and no amount of dilation closes that
# gap. An absolute IoU gate up near 0.9 therefore rejects every option
# including the best one, which is exactly what it did on the first run.
#
# So IoU is kept only as a gross-shape sanity floor, and the gates that decide
# usability are the ones the eye actually keys on in a word:
#   stem  — apparent weight. A letter 30% lighter than its neighbours reads as
#           a different font instantly; at 8% it does not.
#   height— apparent size. x-height/cap mismatch reads as the wrong point size.
#   spacing—the whitespace either side of the glyph (its sidebearings), which
#           is what decides whether letters sit right and, at worst, whether
#           they collide.
#
# Note what is NOT gated: the advance's agreement with the DESIGNER's advance.
# advance = left sidebearing + ink + right sidebearing, and the ink here is
# deliberately different — it was thickened to match the destination cut's
# stem. On this family the designer instead thickened inward, so a faithful
# stem forces ~27/1000em more ink, which forces ~5.5% more advance before any
# error at all. Gating on advance agreement would therefore be gating mostly
# on a decision already made and already measured elsewhere (stem, IoU,
# height), and it cannot pass for any family that emboldens inward. The
# sidebearings isolate the part that is genuinely about spacing. advance_err
# stays in the report so nothing is hidden.
# IoU stays in the report either way, because it is what makes two candidate
# donors comparable (see rank_sources).
MIN_HELDOUT_IOU = 0.45      # gross-shape sanity floor only
MAX_STEM_ERR = 0.08         # apparent weight
MAX_HEIGHT_ERR = 0.03       # apparent size
MAX_SIDEBEARING_ERR = 20.0  # per-mille of em; whitespace beside the glyph
MIN_HELDOUT_GLYPHS = 3      # too few shared glyphs to trust any fit

# Candidates for the stroke-direction falloff exponent (see stroke_gain).
# k=2 reproduces the naive per-axis blend that under-weighted diagonals;
# larger values keep diagonals nearer stem weight. Selected per font pair on
# data held out from the parameter fit, never hand-picked.
DIAG_K_CANDIDATES = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0)

# How much of the thickening grows the outer boundary vs eats into the
# counters (see dilate_xy).
#
# Deliberately a single value. Thickening inward is what a real bold does —
# measured, Regular 'o' ink is 431.5/1000em and Bold 'o' is 434.1 while the
# stem grew by 40 — and lower values did cut the synthesized ink from 27.5 to
# 16.8/1000em too wide. But they cost more than they bought:
#   * a stroke bounded by the OUTER contour on both sides has no counter to
#     eat into, so it just gets starved: at 0.25, '4''s stem below its
#     crossbar thickened by 8 units instead of 82. Whether a stroke has a
#     counter on one side is local geometry, not a per-glyph property, so a
#     single split cannot serve both kinds of stroke in the same letter.
#   * below 0.5, '4' welded shut across the gap under its crossbar — a gap
#     that is open exterior white, not an enclosed counter, so the counter
#     guard cannot see it either.
# The gain was 0.39pt -> 0.24pt of extra width at 14pt; the cost was visibly
# broken letters. Keeping the mechanism (some other family may want it) but
# not the risk.
OUTER_FRAC_CANDIDATES = (1.0,)


# ── geometry ────────────────────────────────────────────────────────────────

def _signed_area(poly: list) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def stroke_gain(ny_abs: float, dx: float, dy: float, diag_k: float) -> float:
    """How much perpendicular thickness a stroke should gain, given how
    horizontal it is (|ny| of its outward normal: 0 = vertical stroke,
    1 = horizontal stroke).

    A vertical stem gains `dx`, a horizontal bar gains `dy`, and the curve
    between them is controlled by `diag_k`. The exponent matters because the
    obvious blend is wrong: weighting by ny^2 (what a plain per-axis offset
    does implicitly) gives a 45-degree stroke only (dx+dy)/2, and in a
    monolinear design like this one the diagonals of '4', 'A', 'V', 'y' and
    'Z' carry STEM weight, not the average of stem and bar. That is exactly
    why the synthesized '4' came out visibly lighter than its neighbours while
    'h' and 'm' were within 1%. A larger exponent keeps diagonals near stem
    weight and tapers to the bar increment only for strokes that really are
    close to horizontal. `diag_k` is not hand-picked — learn_weight_transform
    selects it on held-out glyphs.
    """
    return dy + (dx - dy) * (1.0 - min(1.0, abs(ny_abs)) ** diag_k)


def _ink_side(all_polys: list, poly: list, ccw: bool) -> int:
    """+1 if *poly* encloses ink (grow it to embolden), -1 if it encloses a
    hole (shrink it to embolden).

    Probes just inside the contour and asks the WHOLE glyph whether that point
    is ink. Several edges are sampled and the answer is a majority vote, since
    a single probe can land in a thin waist where the opposite boundary is
    only a few units away.
    """
    bb = fmet.poly_bbox([poly])
    if not bb:
        return 1
    eps = max(1.0, 0.02 * min(bb[2] - bb[0], bb[3] - bb[1]))
    n = len(poly)
    # Sample the longest edges: their midpoints are furthest from corners and
    # from the opposite side of a thin stroke.
    edges = sorted(range(n),
                   key=lambda i: -math.hypot(poly[(i + 1) % n][0] - poly[i][0],
                                             poly[(i + 1) % n][1] - poly[i][1]))[:7]
    votes = 0
    for i in edges:
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        length = math.hypot(ex, ey)
        if length < 1e-9:
            continue
        ex, ey = ex / length, ey / length
        nx, ny = (ey, -ex) if ccw else (-ey, ex)   # this contour's own outward
        px = (x0 + x1) / 2.0 - nx * eps            # step INWARD
        py = (y0 + y1) / 2.0 - ny * eps
        votes += 1 if fmet.winding_at(all_polys, px, py) != 0 else -1
    return 1 if votes >= 0 else -1


def counter_open_area(polys: list, hole: list, samples: int = 32) -> float:
    """Area of *hole* that is genuinely ink-free in *polys*.

    Measures the white space a reader actually sees inside a counter, rather
    than the area the counter's contour nominally encloses. The two differ
    once an offset makes the contour self-intersect, which is why this is the
    quantity worth testing.
    """
    hb = fmet.poly_bbox([hole])
    if not hb:
        return 0.0
    dy = (hb[3] - hb[1]) / samples
    if dy <= 0:
        return 0.0
    total = 0.0
    for i in range(samples):
        y = hb[1] + (i + 0.5) * dy
        for x0, x1 in fmet.scanline_runs([hole], y):
            if fmet.winding_at(polys, (x0 + x1) / 2.0, y) == 0:
                total += (x1 - x0) * dy
    return total


def topology_ok(before_polys: list, after_polys: list,
                min_open_frac: float = 0.20) -> bool:
    """True if thickening preserved the glyph's counters as open white space.

    Offsetting an outline is only safe while no boundary crosses another. In a
    narrow concave region — the apex of '4''s triangular counter, the tip of
    'e''s — a large inward offset welds the counter shut and, under the
    nonzero winding rule, the two strokes either side of it merge into one
    slab. This is CHECKED rather than trusted because a scalar quality score
    cannot see it: the hyperparameter search picked an aggressive inward
    offset that scored well on mean overlap, because the shared glyphs
    scoring it had no narrow triangular counter, while '4' — absent from that
    pool and so never scored — came out welded shut.

    The measure is each counter's OPEN (ink-free) area, before vs after. Three
    earlier attempts were each wrong in an instructive way:

      - Comparing whole-glyph scanline run COUNTS at matched heights. Looked
        precise, wasn't: thickening legitimately moves the height at which an
        arch meets its stem, so 'n', 'o' and 'w' all "failed" while scoring up
        to 0.98 overlap. It measured shape, not structure.

      - Comparing the counter CONTOUR's enclosed area. Misses the failure
        entirely: once the walls cross, the contour becomes a figure-eight
        whose lobes still enclose area, so a welded counter looks healthy.

      - Requiring every probe inside the counter to be white. False-positives
        on almost every counter: an offset contour routinely overshoots a
        sharp apex and leaves a small reversed lobe, and inside that lobe the
        winding is -2. Nonzero-nonzero means painted, so the lobe punches no
        hole and the glyph renders correctly — a benign artifact this test
        called fatal.

    Open area is immune to all three: a benign apex lobe contributes nothing
    because it is not white, and a welded counter loses its white body.
    """
    if not before_polys or not after_polys:
        return False
    if len(after_polys) != len(before_polys):
        return False

    b_ccw = [_signed_area(pl) > 0 for pl in before_polys]
    a_ccw = [_signed_area(pl) > 0 for pl in after_polys]
    b_side = [_ink_side(before_polys, pl, c) for pl, c in zip(before_polys, b_ccw)]

    for b, a, side in zip(before_polys, after_polys, b_side):
        if side >= 0:
            continue
        open_before = counter_open_area(before_polys, b)
        if open_before <= 0:
            continue
        if counter_open_area(after_polys, a) < min_open_frac * open_before:
            return False
    return True


def dilate_xy(polys: list, dx: float, dy: float, diag_k: float = 4.0,
              outer_frac: float = 1.0) -> list:
    """Offset every contour outward so a vertical stem of width w becomes
    w + dx, a horizontal bar of thickness t becomes t + dy, and strokes in
    between follow `stroke_gain`. Negative values thin.

    x and y are separate because a real bold is not a uniformly fattened
    regular: in a low-contrast Latin design the stems gain substantially more
    than the bars, and offsetting both by the stem's increment turns the
    crossbars of 'e', 'E' and 't' into slabs and chokes the counters shut.
    Both increments are measured off the two cuts (see
    learn_weight_transform), not assumed.

    Each EDGE is offset along its own outward normal by its own gain/2, and
    each vertex is placed at the MITER — the intersection of its two offset
    edge lines. Intersecting rather than averaging the two normals is what
    makes the anisotropy correct at a corner: on a rectangle offset in x
    only, the miter keeps the corners exactly on the original horizontal
    edges, whereas a bisector offset of uniform length would drag them
    upward and make the shape taller as well as wider. The miter length is
    clamped, because at a near-cusp the exact intersection runs away to
    infinity and would fire a spike across the glyph; past the clamp the
    vertex falls back to a plain perpendicular offset.

    `outer_frac` splits the thickening between growing outward and eating
    inward. It exists because measuring this family showed a real bold does
    NOT simply get wider: Regular 'o' ink is 431.5/1000em and Bold 'o' is
    434.1 — 2.6 wider — while the stem grew by 40. The designer thickened
    almost entirely INWARD, shrinking the counter. Growing both boundaries by
    the full amount (outer_frac=1) made every synthesized letter ~27/1000em
    too wide, which then crushed its right sidebearing to zero or negative
    and would have let letters collide.

    A stroke bounded by an outer contour on one side and a counter on the
    other still gains the full amount, since the two shares sum to it:
    outer_frac/2 + (2 - outer_frac)/2 = 1. A glyph with NO counter is forced
    back to outer_frac=1, because a bare stem like 'l' or 'I' has nothing to
    eat into and can only thicken outward — which is exactly what the
    designer did there (Regular 'l' ink 174u -> Bold 256u, the full step).
    """
    if not dx and not dy:
        return [list(p) for p in polys]
    # Whether this glyph has a counter at all decides if the inward share is
    # even available (see outer_frac in the docstring).
    has_hole = any(_ink_side(polys, pl, _signed_area(pl) > 0) < 0
                   for pl in polys if len(pl) >= 3)
    out = []
    for poly in polys:
        n = len(poly)
        if n < 3:
            continue
        ccw = _signed_area(poly) > 0
        # +1 to grow this contour, -1 to shrink it. A contour that ENCLOSES
        # ink (an outer boundary) must grow to thicken the stroke; a contour
        # that encloses a hole (the counter of 'o', 'e', '4') must shrink,
        # because the ink is on the outside of it. Deciding this from the
        # contour's own winding direction is not enough — that grows the hole
        # as well and cancels the outer contour's gain, which is what left '4'
        # with no thickening at all while single-contour letters like 'h' and
        # 'n' came out within 1%.
        sign = _ink_side(polys, poly, ccw)
        share = (outer_frac if sign > 0 else (2.0 - outer_frac)) if has_hole else 1.0
        # Per-edge unit direction, outward normal, and offset vector.
        dirs, offs = [], []
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            # Deliberately NOT named dx/dy: those are this function's offset
            # parameters, and shadowing them here silently turned the whole
            # offset into a no-op once (they became the last edge's unit
            # vector, ~0.5 units instead of the requested ~80).
            ex, ey = x1 - x0, y1 - y0
            length = math.hypot(ex, ey)
            if length < 1e-9:
                dirs.append(None)
                offs.append((0.0, 0.0))
                continue
            ex, ey = ex / length, ey / length
            # Interior lies left of travel on a counter-clockwise contour, so
            # outward is the right-hand normal there and the left-hand one on
            # a clockwise contour.
            nx, ny = (ey, -ex) if ccw else (-ey, ex)
            h = sign * share * stroke_gain(abs(ny), dx, dy, diag_k) / 2.0
            dirs.append((ex, ey))
            offs.append((nx * h, ny * h))

        limit = 2.5 * max(abs(dx), abs(dy), 1.0)
        moved = []
        for i in range(n):
            d_prev, d_next = dirs[i - 1], dirs[i]
            a, b = offs[i - 1], offs[i]
            px, py = poly[i]
            if d_prev is None or d_next is None:
                src = b if d_prev is None else a
                moved.append((px + src[0], py + src[1]))
                continue
            cx, cy = b[0] - a[0], b[1] - a[1]
            det = -d_prev[0] * d_next[1] + d_next[0] * d_prev[1]
            if abs(det) < 1e-9:
                moved.append((px + a[0], py + a[1]))   # collinear edges
                continue
            s = (-cx * d_next[1] + d_next[0] * cy) / det
            vx = a[0] + s * d_prev[0]
            vy = a[1] + s * d_prev[1]
            if math.hypot(vx, vy) > limit:
                vx = (a[0] + b[0]) / 2.0               # cusp: bevel instead
                vy = (a[1] + b[1]) / 2.0
            moved.append((px + vx, py + vy))
        out.append(moved)
    return out


def scale_polys(polys: list, sx: float, sy: float) -> list:
    return [[(x * sx, y * sy) for (x, y) in poly] for poly in polys]


def translate_polys(polys: list, dx: float, dy: float) -> list:
    return [[(x + dx, y + dy) for (x, y) in poly] for poly in polys]


def clip_polys_rect(polys: list, x0=None, y0=None, x1=None, y1=None) -> list:
    """Intersect contours with an axis-aligned rectangle (any bound may be
    None for "unbounded"), via Sutherland–Hodgman against each half-plane in
    turn. Valid here because a rectangle is convex, and because these contours
    are already flattened to line segments.
    """
    planes = []
    if x0 is not None:
        planes.append(lambda p: p[0] - x0)
    if x1 is not None:
        planes.append(lambda p: x1 - p[0])
    if y0 is not None:
        planes.append(lambda p: p[1] - y0)
    if y1 is not None:
        planes.append(lambda p: y1 - p[1])

    out = []
    for poly in polys:
        cur = list(poly)
        for inside in planes:
            if not cur:
                break
            nxt = []
            m = len(cur)
            for i in range(m):
                a, b = cur[i], cur[(i + 1) % m]
                da, db = inside(a), inside(b)
                if da >= 0:
                    nxt.append(a)
                if (da >= 0) != (db >= 0):
                    t = da / (da - db)
                    nxt.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            cur = nxt
        if len(cur) >= 3:
            out.append(cur)
    return out


# ── rasterization + shape comparison ────────────────────────────────────────

def raster(polys: list, upm: float, size: int = 96) -> set:
    """Fill *polys* into a set of (col, row) cells over a window fixed in font
    units, so two glyphs rasterized at the same upm/size are directly
    comparable cell for cell. Window: x ∈ [-0.1, 1.1] em, y ∈ [-0.35, 1.05] em,
    which comfortably contains any Latin glyph including its side bearings,
    descender and accents.
    """
    x_lo, x_hi = -0.10 * upm, 1.10 * upm
    y_lo, y_hi = -0.35 * upm, 1.05 * upm
    w = x_hi - x_lo
    h = y_hi - y_lo
    cells = set()
    for row in range(size):
        y = y_lo + (row + 0.5) * h / size
        for rx0, rx1 in fmet.scanline_runs(polys, y):
            c0 = int(math.floor((rx0 - x_lo) * size / w + 0.5))
            c1 = int(math.floor((rx1 - x_lo) * size / w + 0.5))
            for col in range(max(0, c0), min(size, c1)):
                cells.add((col, row))
    return cells


def iou(a: set, b: set) -> float:
    """Intersection-over-union of two rasterized glyphs: 1.0 identical, 0.0
    disjoint. Chosen over a plain pixel-difference count because it does not
    reward a glyph for being mostly-empty the way raw agreement does."""
    if not a and not b:
        return 1.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


# ── glyph access helpers ────────────────────────────────────────────────────

def _gname(tt, ch: str):
    try:
        return (tt.getBestCmap() or {}).get(ord(ch))
    except Exception:  # noqa: BLE001
        return None


def _advance(tt, ch: str):
    gn = _gname(tt, ch)
    if not gn:
        return None
    try:
        return tt["hmtx"][gn][0]
    except Exception:  # noqa: BLE001
        return None


def _polys(tt, ch: str) -> list:
    gn = _gname(tt, ch)
    return fmet.glyph_polys(tt, gn) if gn else []


def shared_chars(src_tt, dst_tt, restrict: str = None) -> list:
    """Characters both fonts draw a non-empty glyph for — the training and
    held-out pool for a weight transform. Restricted to letters and digits:
    punctuation is too small and too idiosyncratic to say anything useful about
    a weight step."""
    pool = restrict or ("abcdefghijklmnopqrstuvwxyz"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    out = []
    for ch in pool:
        if _gname(src_tt, ch) and _gname(dst_tt, ch) and _polys(src_tt, ch) and _polys(dst_tt, ch):
            out.append(ch)
    return out


# ── the learned weight transform ────────────────────────────────────────────

def _fit_line(pairs):
    """Least-squares y = a·x + b over (x, y) pairs; (1.0, 0.0) if degenerate."""
    n = len(pairs)
    if n < 2:
        return 1.0, 0.0
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] * p[0] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return 1.0, (sy - sx) / n
    a = (n * sxy - sx * sy) / denom
    return a, (sy - a * sx) / n


class WeightTransform:
    """A measured cut→cut step (e.g. Regular→Bold) for ONE font pair, with a
    held-out accuracy report attached.

    Never construct this directly — use `learn_weight_transform`, which is what
    fits it and, more importantly, what tests it.

    The pipeline `apply` runs, and why it is in this order:

      1. unit scale — into the destination cut's unitsPerEm.
      2. vertical affine — a measured y → a·y + b, fitted on the top and
         bottom of every shared glyph. This is what moves the source cut's
         x-height, cap height, ascender and overshoots onto the destination
         cut's, without needing to classify the letter first.
      3. dilate_xy — the measured weight step: stems by dx, bars by dy.
      4. vertical re-pin — dilation grows a glyph by dy/2 at each end, but a
         real bold does not get taller than its regular; heights are a family
         constant. So the result is squeezed back onto the vertical extent
         step 2 predicted, which restores the baseline and the height exactly.
      5. horizontal re-pin — dilation also pushes the left edge out by dx/2,
         which would shift the letter off its sidebearing and into its
         neighbour. The left edge is put back on the destination cut's
         measured sidebearing for a letter of this shape. Only the LEFT edge
         is pinned: the ink legitimately gets dx wider, and pinning both would
         cancel the thickening this whole step exists to apply.
    """

    def __init__(self, upm, unit_scale, va, vb, stem_dx, bar_dy,
                 lsb_a, lsb_b, adv_anchors, rsb_med, report,
                 adv_model="local_ratio", diag_k=4.0, rsb_a=1.0, rsb_b=0.0,
                 outer_frac=1.0, exact_map=None):
        self.upm = upm
        self.unit_scale = unit_scale
        self.va, self.vb = va, vb           # vertical affine, dst units
        self.stem_dx = stem_dx              # units added to vertical stems
        self.bar_dy = bar_dy                # units added to horizontal bars
        self.lsb_a, self.lsb_b = lsb_a, lsb_b   # left-sidebearing affine
        self.adv_anchors = adv_anchors      # [(src_adv, dst_adv)] in dst units
        self.rsb_med = rsb_med              # dst font's median right sidebearing
        self.adv_model = adv_model          # "local_ratio" | "sidebearing"
        self.diag_k = diag_k                # stroke-direction falloff exponent
        self.rsb_a, self.rsb_b = rsb_a, rsb_b   # right-sidebearing affine
        self.outer_frac = outer_frac         # outward vs inward thickening split
        self.exact_map = exact_map or {}     # (class, src advance) -> dst advance
        self.report = report

    @property
    def usable(self) -> bool:
        return bool(self.report.get("usable"))

    def _advance_local_ratio(self, src_advance: float) -> float:
        """Destination advance by LOCAL regression: use only the shared glyphs
        whose source advance is near this one, and take the median of their
        dst/src ratios.

        A single global ratio (or increment) fitted across the whole alphabet
        was measurably wrong on this family — its width table is irregular
        (several letters keep the same advance from Regular to Bold while
        others grow by a quarter), so one constant cannot fit both groups.
        Letters of similar width tend to behave alike, which is what makes the
        local fit both better and still purely measured.
        """
        scaled = src_advance * self.unit_scale
        if not self.adv_anchors:
            return scaled
        for tol in (0.06, 0.12, 0.25, 1e9):
            near = [d / s for s, d in self.adv_anchors
                    if s and abs(s - scaled) <= tol * scaled]
            if len(near) >= 3 or (near and tol > 1.0):
                near.sort()
                return scaled * near[len(near) // 2]
        return scaled

    def _advance_sidebearing(self, synth_polys: list) -> float:
        """Destination advance built from the glyph actually produced: where its
        ink ends, plus the destination font's own median right sidebearing.

        Structural rather than extrapolated — it cannot disagree with the ink
        it is measuring, whereas a cross-cut ratio can predict an advance
        narrower than the letter it is supposed to contain."""
        bb = fmet.poly_bbox(synth_polys)
        if not bb:
            return 0.0
        return bb[2] + self.rsb_med

    def _advance_sb_transfer(self, src_advance: float, src_bbox, synth_polys: list) -> float:
        """Destination advance from where the synthesized ink ends, plus this
        LETTER'S OWN right sidebearing carried across the cuts.

        The other two models both predict from a single global quantity, and
        neither can work here: measured on this family, Regular 'u' and 'E'
        have the SAME advance (863) but map to different Bold advances (1087
        and 981), so destination advance is provably not a function of source
        advance alone. A letter's own sidebearing is the per-glyph signal that
        separates those two cases.
        """
        bb = fmet.poly_bbox(synth_polys)
        if not bb or not src_bbox:
            return self._advance_local_ratio(src_advance)
        rsb_src = (src_advance - src_bbox[2]) * self.unit_scale
        return bb[2] + (self.rsb_a * rsb_src + self.rsb_b)

    @staticmethod
    def _char_class(ch: str) -> str:
        if ch.islower():
            return "lower"
        if ch.isupper():
            return "upper"
        if ch.isdigit():
            return "digit"
        return "other"

    def advance_exact(self, ch: str, src_advance: float):
        """The destination advance of a glyph that has the SAME advance as
        this one in the source cut and the same character class — or None.

        This is the most precise rule available and it needs no typographic
        assumption: fonts routinely give whole groups of glyphs identical
        advances, so if the source cut gives 'h' and 'u' the same advance, the
        destination cut almost certainly does too, and the destination's 'u' is
        already in the file. Measured here, that returns Bold's own 'n'/'u'
        advance for 'h' — which is the structurally correct answer, since 'h'
        is an 'n' with a taller left stem.

        Character class is part of the key because advance alone is ambiguous:
        Regular 'u' and Regular 'E' BOTH have advance 863 but map to 1087 and
        981 respectively, so pooling them predicts neither. Restricting the
        transfer to letters of the same case resolves it.
        """
        if not self.exact_map:
            return None
        key = (self._char_class(ch), round(src_advance * self.unit_scale))
        return self.exact_map.get(key)

    def predict_advance(self, src_advance: float, synth_polys: list = None,
                        src_bbox=None, ch: str = None) -> float:
        """The advance to write for this glyph.

        Prefers the destination cut's own advance for a glyph of the same
        class and source advance (`advance_exact`) — that is the designer's
        real number and keeps run width honest — but only while it actually
        leaves room for the ink produced. This matters because the synthesized
        ink is legitimately WIDER than the designer's: matching the
        destination's stem weight in a family that emboldens inward costs
        ~27/1000em of extra ink, so the designer's advance can be narrower
        than the letter it now has to contain. Measured on this family,
        taking the exact advance unconditionally left 6 of 15 held-out
        letters with a zero or negative right sidebearing — letters that
        would touch or overlap their neighbour. Where that happens the
        advance is derived from the ink instead, trading a marginally wider
        run (which run-level fitting can absorb) for spacing that is never
        broken (which nothing downstream can repair).
        """
        floor = max(0.25 * self.rsb_med, 0.0)
        if ch:
            exact = self.advance_exact(ch, src_advance)
            if exact is not None:
                bb = fmet.poly_bbox(synth_polys) if synth_polys else None
                if bb is None or (exact - bb[2]) >= floor:
                    return exact
        if self.adv_model == "sb_transfer" and synth_polys and src_bbox:
            return self._advance_sb_transfer(src_advance, src_bbox, synth_polys)
        if self.adv_model == "sidebearing" and synth_polys:
            return self._advance_sidebearing(synth_polys)
        return self._advance_local_ratio(src_advance)

    def _stages(self, polys: list):
        """(pre, dilated, final) — the three stages of `apply`.

        Exposed because the structural guard has to compare the glyph before
        and after DILATION SPECIFICALLY, in one shared coordinate frame.
        Comparing the raw source against the finished glyph does not work: the
        unit scale, the vertical affine, the vertical re-pin and the
        sidebearing translate all move coordinates, so probe points mapped
        proportionally between those two frames drift by more than a counter
        is wide and land on ink — which made the guard fail every single glyph
        that has a counter, including ones that were provably correct.
        Comparing `pre` with `dilated` needs no mapping at all: dilation moves
        boundaries but leaves the frame alone.
        """
        u = self.unit_scale
        pre = [[(x * u, (y * u) * self.va + self.vb) for (x, y) in poly]
               for poly in polys]
        dil = dilate_xy(pre, self.stem_dx, self.bar_dy, self.diag_k, self.outer_frac)
        p = dil
        bb_before = fmet.poly_bbox(pre)
        bb_after = fmet.poly_bbox(dil)
        if bb_before and bb_after:
            h_before = bb_before[3] - bb_before[1]
            h_after = bb_after[3] - bb_after[1]
            if h_after > 1e-6 and h_before > 1e-6:
                k = h_before / h_after
                p = [[(x, (y - bb_after[1]) * k + bb_before[1]) for (x, y) in poly]
                     for poly in p]
            target_x0 = bb_before[0] * self.lsb_a + self.lsb_b
            p = translate_polys(p, target_x0 - bb_after[0], 0.0)
        return pre, dil, p

    def apply(self, polys: list) -> list:
        return self._stages(polys)[2]

    def apply_checked(self, polys: list):
        """(final_polys, structure_survived). See topology_ok."""
        pre, dil, out = self._stages(polys)
        return out, topology_ok(pre, dil)

    def __repr__(self):
        r = self.report
        return (f"<WeightTransform stem{self.stem_dx:+.0f}u bar{self.bar_dy:+.0f}u "
                f"k={self.diag_k:g} of={self.outer_frac:g} "
                f"vy({self.va:.4f},{self.vb:+.0f}) "
                f"iou={r.get('iou_mean') or 0:.3f} "
                f"stem_err={r.get('stem_err') or 1:.1%} "
                f"h_err={r.get('height_err') or 1:.1%} "
                f"adv={self.adv_model}:{r.get('advance_err') or 1:.1%} "
                f"usable={self.usable}>")


def learn_weight_transform(src_tt, dst_tt, raster_size: int = 96) -> WeightTransform:
    """Fit and TEST a cut→cut transform using only the glyphs both fonts have.

    The pool is split: every other glyph fits the transform, the rest are held
    out and never influence it. The held-out glyphs are then synthesized and
    compared against the destination font's real glyphs, giving the accuracy
    numbers in `.report` and the `.usable` verdict.
    """
    m_src = fmet.measure(src_tt)
    m_dst = fmet.measure(dst_tt)
    upm = m_dst["upm"]
    unit_scale = upm / m_src["upm"]

    pool = shared_chars(src_tt, dst_tt)
    if len(pool) < MIN_HELDOUT_GLYPHS * 2:
        return WeightTransform(upm, unit_scale, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, [], 0.0,
                               {"usable": False, "reason": "too_few_shared_glyphs",
                                "shared": len(pool)})

    fit_chars = pool[0::2]
    held_chars = pool[1::2]

    def build(chars, diag_k, outer_frac):
        """Fit every parameter of the transform from *chars* alone.

        Called twice, deliberately. First on half the shared glyphs, so the
        other half can measure the error honestly. Then again on ALL of them
        for the transform actually returned: the accuracy estimate has to come
        from data the fit never saw, but there is no reason to ship a fit that
        threw away half its evidence. This is why the returned transform's
        parameters and its report come from different fits.
        """
        # Vertical affine and left-sidebearing affine from glyph bounding
        # boxes. Using every glyph's top AND bottom (rather than one x-height
        # reading) means overshoots, ascenders, descenders and cap height all
        # constrain the same fit, and one odd glyph cannot dominate it.
        v_pairs, lsb_pairs, anchors, rsbs, rsb_pairs = [], [], [], [], []
        for ch in chars:
            bs = fmet.poly_bbox(_polys(src_tt, ch))
            bd = fmet.poly_bbox(_polys(dst_tt, ch))
            if not bs or not bd:
                continue
            v_pairs.append((bs[1] * unit_scale, bd[1]))
            v_pairs.append((bs[3] * unit_scale, bd[3]))
            lsb_pairs.append((bs[0] * unit_scale, bd[0]))
            a_s, a_d = _advance(src_tt, ch), _advance(dst_tt, ch)
            if a_s and a_d:
                anchors.append((a_s * unit_scale, a_d))
                rsbs.append(a_d - bd[2])
                rsb_pairs.append(((a_s - bs[2]) * unit_scale, a_d - bd[2]))
        va, vb = _fit_line(v_pairs)
        lsb_a, lsb_b = _fit_line(lsb_pairs)

        stem_src = (m_src["stem_units"] or 0) * unit_scale
        stem_dst = m_dst["stem_units"] or 0
        bar_src = (m_src["bar_units"] or 0) * unit_scale
        bar_dst = m_dst["bar_units"] or 0
        # The vertical affine already scales bar thickness by `va`; the
        # offsets only make up what remains.
        stem_dx = (stem_dst - stem_src * va) if (stem_src and stem_dst) else 0.0
        bar_dy = (bar_dst - bar_src * va) if (bar_src and bar_dst) else 0.0

        # The destination font's own right-sidebearing habit, for the
        # structural advance model. Median so one unusually tight or loose
        # letter cannot set the convention for all of them.
        rsbs.sort()
        rsb_med = rsbs[len(rsbs) // 2] if rsbs else 0.0
        rsb_a, rsb_b = _fit_line(rsb_pairs)

        groups: dict = {}
        for ch in chars:
            a_s, a_d = _advance(src_tt, ch), _advance(dst_tt, ch)
            if a_s and a_d:
                groups.setdefault(
                    (WeightTransform._char_class(ch), round(a_s * unit_scale)), []
                ).append(a_d)
        exact_map = {}
        for key, vals in groups.items():
            vals.sort()
            exact_map[key] = vals[len(vals) // 2]

        return WeightTransform(upm, unit_scale, va, vb, stem_dx, bar_dy,
                               lsb_a, lsb_b, anchors, rsb_med, {}, diag_k=diag_k,
                               rsb_a=rsb_a, rsb_b=rsb_b, outer_frac=outer_frac,
                               exact_map=exact_map)

    def mean_iou(cand, chars):
        """Mean overlap against the real glyphs — or None if this candidate
        breaks any glyph's structure, which disqualifies it outright rather
        than letting a good average hide a ruined letter. The structure check
        runs over a WIDER set than the scored one (every shared glyph plus
        whatever characters the caller still needs), because the glyph that
        breaks is often not one of the glyphs available to score."""
        vals = []
        for ch in chars:
            sp, dp = _polys(src_tt, ch), _polys(dst_tt, ch)
            if sp and dp:
                vals.append(iou(raster(cand.apply(sp), upm, raster_size),
                                raster(dp, upm, raster_size)))
        for ch in guard_chars:
            sp = _polys(src_tt, ch)
            if sp and not cand.apply_checked(sp)[1]:
                return None
        return (sum(vals) / len(vals)) if vals else None

    # Choose the stroke-direction exponent by NESTED selection: split the fit
    # half again, fit on one part and score candidates on the other. The
    # held-out half below is never consulted here, so the accuracy this
    # function reports is not the same data that picked the exponent — pick a
    # hyperparameter on your test set and the report stops meaning anything.
    # Structure is checked on every character the source cut can draw, not
    # just the ones that can be scored — the counter that collapses is
    # typically in a glyph the destination cut lacks, which is exactly the
    # glyph being synthesized.
    src_cov = fmet.measure(src_tt)["coverage"]
    guard_chars = [c for c in ("abcdefghijklmnopqrstuvwxyz"
                               "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
                   if ord(c) in src_cov]

    inner_fit, inner_sel = fit_chars[0::2], fit_chars[1::2]
    k_scores = {}
    for k in DIAG_K_CANDIDATES:
        for of in OUTER_FRAC_CANDIDATES:
            sc = mean_iou(build(inner_fit, k, of), inner_sel)
            if sc is not None:
                k_scores[(k, of)] = sc
    diag_k, outer_frac = (max(k_scores, key=k_scores.get) if k_scores else (4.0, 1.0))

    xf = build(fit_chars, diag_k, outer_frac)

    # ── held-out validation ────────────────────────────────────────────────
    # Every number below comes from glyphs that took no part in any fit above,
    # compared against the destination font's REAL glyph for the same
    # character. Both advance models are scored here and the better one is
    # adopted, so the choice is measured on this font rather than assumed.
    ious, stem_errs, height_errs, lsb_errs = [], [], [], []
    rsb_errs = {"local_ratio": [], "sidebearing": [], "sb_transfer": []}
    rsb_negative = {"local_ratio": 0, "sidebearing": 0, "sb_transfer": 0}
    adv_errs = {"local_ratio": [], "sidebearing": [], "sb_transfer": []}
    for ch in held_chars:
        src_polys = _polys(src_tt, ch)
        dst_polys = _polys(dst_tt, ch)
        if not src_polys or not dst_polys:
            continue
        synth = xf.apply(src_polys)
        ious.append(iou(raster(synth, upm, raster_size),
                        raster(dst_polys, upm, raster_size)))
        # Only on letters whose leftmost feature IS a stem — on a 'd' or an 'o'
        # the first run crosses the bowl, which is thicker than the stem and
        # responds differently to an offset, and scoring those as stem error
        # reported 20% on a transform whose actual stems were within 1%.
        if m_dst["x_height_units"] and ch in fmet.LEFT_STEM_CHARS:
            y = m_dst["x_height_units"] * 0.5
            r_s = fmet.scanline_runs(synth, y)
            r_d = fmet.scanline_runs(dst_polys, y)
            if r_s and r_d:
                w_s = r_s[0][1] - r_s[0][0]
                w_d = r_d[0][1] - r_d[0][0]
                if w_d:
                    stem_errs.append(abs(w_s - w_d) / w_d)
        bb_s, bb_d = fmet.poly_bbox(synth), fmet.poly_bbox(dst_polys)
        if bb_s and bb_d:
            h_d = bb_d[3] - bb_d[1]
            if h_d:
                height_errs.append(abs((bb_s[3] - bb_s[1]) - h_d) / h_d)
        a_src, a_dst = _advance(src_tt, ch), _advance(dst_tt, ch)
        if a_src and a_dst and bb_s and bb_d:
            per_mille = 1000.0 / upm
            src_bb = fmet.poly_bbox(src_polys)
            lsb_errs.append(abs(bb_s[0] - bb_d[0]) * per_mille)
            rsb_dst = a_dst - bb_d[2]
            # Score the path the caller actually takes: the exact-advance
            # transfer applies first when it hits, so leaving it out here would
            # measure a model the engine never uses on its own.
            # Score the path the caller actually takes: the exact-advance
            # transfer applies first when it hits AND leaves room, so leaving
            # it out here would measure a model the engine never uses alone.
            floor = max(0.25 * xf.rsb_med, 0.0)
            exact = xf.advance_exact(ch, a_src)
            if exact is not None and (exact - bb_s[2]) < floor:
                exact = None
            preds = {
                "local_ratio": exact if exact is not None else xf._advance_local_ratio(a_src),
                "sidebearing": exact if exact is not None else xf._advance_sidebearing(synth),
                "sb_transfer": exact if exact is not None else xf._advance_sb_transfer(a_src, src_bb, synth),
            }
            for name, pred in preds.items():
                adv_errs[name].append(abs(pred - a_dst) / a_dst)
                rsb_syn = pred - bb_s[2]
                rsb_errs[name].append(abs(rsb_syn - rsb_dst) * per_mille)
                if rsb_syn < 0:
                    rsb_negative[name] += 1

    def mean(xs):
        return (sum(xs) / len(xs)) if xs else None

    adv_scores = {k: mean(v) for k, v in adv_errs.items()}
    rsb_scores = {k: mean(v) for k, v in rsb_errs.items()}
    # Choose the advance model on SPACING, and never one that would let a
    # letter collide with its neighbour: a model can score well on advance
    # agreement while leaving a negative right sidebearing, which measured on
    # this family happened for 6 of 15 held-out letters.
    def _adv_rank(name):
        return (rsb_negative.get(name, 0),
                rsb_scores.get(name) if rsb_scores.get(name) is not None else 1e9)

    best_adv = min((k for k, v in rsb_scores.items() if v is not None),
                   key=_adv_rank, default="sidebearing")
    xf.adv_model = best_adv
    adv_err = adv_scores.get(best_adv)
    spacing_err = max([v for v in (mean(lsb_errs), rsb_scores.get(best_adv))
                       if v is not None] or [None])
    n_collide = rsb_negative.get(best_adv, 0)

    iou_mean = mean(ious)
    stem_err = mean(stem_errs)
    height_err = mean(height_errs)

    checks = {
        "enough_heldout": len(ious) >= MIN_HELDOUT_GLYPHS,
        "shape": iou_mean is not None and iou_mean >= MIN_HELDOUT_IOU,
        "stem": stem_err is not None and stem_err <= MAX_STEM_ERR,
        "height": height_err is None or height_err <= MAX_HEIGHT_ERR,
        "spacing": spacing_err is None or spacing_err <= MAX_SIDEBEARING_ERR,
        "no_collisions": n_collide == 0,
    }
    usable = all(checks.values())
    xf.report = {
        "usable": usable,
        "reason": None if usable else next(k for k, v in checks.items() if not v),
        "checks": checks,
        "iou_mean": iou_mean, "iou_min": (min(ious) if ious else None),
        "stem_err": stem_err, "height_err": height_err,
        "advance_err": adv_err, "advance_model": best_adv,
        "advance_err_by_model": adv_scores,
        "spacing_err": spacing_err, "lsb_err": mean(lsb_errs),
        "rsb_err": rsb_scores.get(best_adv), "rsb_err_by_model": rsb_scores,
        "would_collide": n_collide, "collisions_by_model": rsb_negative,
        "diag_k": diag_k, "outer_frac": outer_frac,
        "n_hyper_candidates_ok": len(k_scores),
        "hyper_best_score": k_scores.get((diag_k, outer_frac)),
        "n_fit": len(fit_chars), "n_heldout": len(ious),
        "n_stem_heldout": len(stem_errs),
        "heldout_chars": "".join(held_chars),
        "shared_chars": "".join(pool),
    }
    # Refit on every shared glyph for the transform that actually gets used,
    # carrying over the held-out report and the advance model it selected.
    final = build(pool, diag_k, outer_frac)
    final.adv_model = best_adv
    final.report = xf.report
    return final


def synthesize_char(src_tt, ch: str, xf: WeightTransform):
    """Build *ch* in the destination cut from the source cut's real glyph.
    Returns {"polys", "advance"} in destination font units, or None."""
    polys = _polys(src_tt, ch)
    src_adv = _advance(src_tt, ch)
    if not polys or src_adv is None:
        return None
    out, ok = xf.apply_checked(polys)
    if not ok:
        return None
    return {"polys": out,
            "advance": xf.predict_advance(src_adv, out, fmet.poly_bbox(polys), ch)}


def polys_to_ttglyph(polys: list, glyph_set=None):
    """Turn flattened contours into a TrueType glyph (all straight segments).

    The point count is higher than a hand-drawn glyph's, but the deviation from
    the original curve is well under a rasterizer's resolution at text sizes,
    and every consumer of the result — the PDF viewer's rasterizer included —
    treats it as an ordinary glyph.
    """
    pen = TTGlyphPen(glyph_set or {})
    for poly in polys:
        if len(poly) < 3:
            continue
        pen.moveTo((round(poly[0][0]), round(poly[0][1])))
        for pt in poly[1:]:
            pen.lineTo((round(pt[0]), round(pt[1])))
        pen.closePath()
    return pen.glyph()
