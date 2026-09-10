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
#   advance—spacing, and therefore whether the run still fits its box.
# IoU stays in the report either way, because it is what makes two candidate
# donors comparable (see rank_sources).
MIN_HELDOUT_IOU = 0.45      # gross-shape sanity floor only
MAX_STEM_ERR = 0.08         # apparent weight
MAX_HEIGHT_ERR = 0.03       # apparent size
MAX_ADVANCE_ERR = 0.06      # spacing / run width
MIN_HELDOUT_GLYPHS = 3      # too few shared glyphs to trust any fit


# ── geometry ────────────────────────────────────────────────────────────────

def _signed_area(poly: list) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def dilate_xy(polys: list, dx: float, dy: float) -> list:
    """Offset every contour outward, by *dx*/2 per side horizontally and
    *dy*/2 vertically, so a vertical stem of width w becomes w + dx and a
    horizontal bar of thickness t becomes t + dy. Negative values thin.

    x and y are separate because a real bold is not a uniformly fattened
    regular: in a low-contrast Latin design the vertical stems gain
    substantially more than the horizontal bars, and offsetting both by the
    stem's increment turns the crossbars of 'e', 'E' and 't' into slabs and
    chokes the counters shut. Both increments are measured off the two cuts
    (see learn_weight_transform), not assumed.

    Each vertex moves along the bisector of its two adjacent edge normals,
    scaled by 1/cos(half-turn) so that flat runs land exactly the requested
    distance out (a plain miter). The scale is clamped, because at a near-cusp
    the exact miter runs away to infinity and would fire a spike across the
    glyph — clamping trades a hair of sharpness at the tip for never doing
    that.
    """
    if not dx and not dy:
        return [list(p) for p in polys]
    out = []
    for poly in polys:
        n = len(poly)
        if n < 3:
            continue
        ccw = _signed_area(poly) > 0
        normals = []
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            # Deliberately NOT named dx/dy: those are this function's offset
            # parameters, and shadowing them here silently turned the whole
            # offset into a no-op once (the offsets became the last edge's
            # unit vector, ~0.5 units instead of the requested ~80).
            ex, ey = x1 - x0, y1 - y0
            length = math.hypot(ex, ey)
            if length < 1e-9:
                normals.append(None)
                continue
            ex, ey = ex / length, ey / length
            # Interior lies left of travel on a counter-clockwise contour, so
            # outward is the right-hand normal there and the left-hand one on a
            # clockwise contour.
            normals.append((ey, -ex) if ccw else (-ey, ex))
        moved = []
        for i in range(n):
            prev_n = normals[i - 1]
            next_n = normals[i]
            cands = [v for v in (prev_n, next_n) if v is not None]
            if not cands:
                moved.append(poly[i])
                continue
            bx = sum(v[0] for v in cands) / len(cands)
            by = sum(v[1] for v in cands) / len(cands)
            blen = math.hypot(bx, by)
            if blen < 1e-9:
                moved.append(poly[i])
                continue
            bx, by = bx / blen, by / blen
            # cos(half turn) == how much the bisector shortened; undo it.
            cos_half = max(blen, 0.45)
            moved.append((poly[i][0] + bx * (dx / 2.0) / cos_half,
                          poly[i][1] + by * (dy / 2.0) / cos_half))
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
                 lsb_a, lsb_b, adv_anchors, rsb_med, report, adv_model="local_ratio"):
        self.upm = upm
        self.unit_scale = unit_scale
        self.va, self.vb = va, vb           # vertical affine, dst units
        self.stem_dx = stem_dx              # units added to vertical stems
        self.bar_dy = bar_dy                # units added to horizontal bars
        self.lsb_a, self.lsb_b = lsb_a, lsb_b   # left-sidebearing affine
        self.adv_anchors = adv_anchors      # [(src_adv, dst_adv)] in dst units
        self.rsb_med = rsb_med              # dst font's median right sidebearing
        self.adv_model = adv_model          # "local_ratio" | "sidebearing"
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

    def predict_advance(self, src_advance: float, synth_polys: list = None) -> float:
        if self.adv_model == "sidebearing" and synth_polys:
            return self._advance_sidebearing(synth_polys)
        return self._advance_local_ratio(src_advance)

    def apply(self, polys: list) -> list:
        u = self.unit_scale
        p = [[(x * u, (y * u) * self.va + self.vb) for (x, y) in poly] for poly in polys]
        bb_before = fmet.poly_bbox(p)
        p = dilate_xy(p, self.stem_dx, self.bar_dy)
        bb_after = fmet.poly_bbox(p)
        if bb_before and bb_after:
            h_before = bb_before[3] - bb_before[1]
            h_after = bb_after[3] - bb_after[1]
            if h_after > 1e-6 and h_before > 1e-6:
                k = h_before / h_after
                p = [[(x, (y - bb_after[1]) * k + bb_before[1]) for (x, y) in poly]
                     for poly in p]
            target_x0 = bb_before[0] * self.lsb_a + self.lsb_b
            p = translate_polys(p, target_x0 - bb_after[0], 0.0)
        return p

    def __repr__(self):
        r = self.report
        return (f"<WeightTransform stem{self.stem_dx:+.0f}u bar{self.bar_dy:+.0f}u "
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

    def build(chars):
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
        v_pairs, lsb_pairs, anchors, rsbs = [], [], [], []
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

        return WeightTransform(upm, unit_scale, va, vb, stem_dx, bar_dy,
                               lsb_a, lsb_b, anchors, rsb_med, {})

    xf = build(fit_chars)

    # ── held-out validation ────────────────────────────────────────────────
    # Every number below comes from glyphs that took no part in any fit above,
    # compared against the destination font's REAL glyph for the same
    # character. Both advance models are scored here and the better one is
    # adopted, so the choice is measured on this font rather than assumed.
    ious, stem_errs, height_errs = [], [], []
    adv_errs = {"local_ratio": [], "sidebearing": []}
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
        if a_src and a_dst:
            adv_errs["local_ratio"].append(
                abs(xf._advance_local_ratio(a_src) - a_dst) / a_dst)
            adv_errs["sidebearing"].append(
                abs(xf._advance_sidebearing(synth) - a_dst) / a_dst)

    def mean(xs):
        return (sum(xs) / len(xs)) if xs else None

    adv_scores = {k: mean(v) for k, v in adv_errs.items()}
    best_adv = min((k for k, v in adv_scores.items() if v is not None),
                   key=lambda k: adv_scores[k], default="local_ratio")
    xf.adv_model = best_adv
    adv_err = adv_scores.get(best_adv)

    iou_mean = mean(ious)
    stem_err = mean(stem_errs)
    height_err = mean(height_errs)

    checks = {
        "enough_heldout": len(ious) >= MIN_HELDOUT_GLYPHS,
        "shape": iou_mean is not None and iou_mean >= MIN_HELDOUT_IOU,
        "stem": stem_err is not None and stem_err <= MAX_STEM_ERR,
        "height": height_err is None or height_err <= MAX_HEIGHT_ERR,
        "advance": adv_err is None or adv_err <= MAX_ADVANCE_ERR,
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
        "n_fit": len(fit_chars), "n_heldout": len(ious),
        "n_stem_heldout": len(stem_errs),
        "heldout_chars": "".join(held_chars),
        "shared_chars": "".join(pool),
    }
    # Refit on every shared glyph for the transform that actually gets used,
    # carrying over the held-out report and the advance model it selected.
    final = build(pool)
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
    out = xf.apply(polys)
    return {"polys": out, "advance": xf.predict_advance(src_adv, out)}


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
