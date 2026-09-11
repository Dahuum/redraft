"""
font_metrics.py — measure a font's real typographic parameters from its own
outlines, so decisions about it can be made from numbers instead of from its
name.

WHY THIS EXISTS
---------------
When an edit needs a character the PDF's embedded subset never included, some
glyph has to come from somewhere else. The engine used to pick that "somewhere
else" by NAME: a hardcoded table said TW Cen MT looks like Poppins, so Poppins
it was. A name lookup can't see that Poppins' lowercase is far larger on the
body and noticeably heavier in the stem than TW Cen MT's — so the injected
letter lands visibly wrong next to the letters around it, at the right size but
in the wrong shape. That is the "letters look off" failure.

Everything here is measured off the actual outlines (scanline-intersected, not
guessed from the OS/2 table — a subsetter can leave OS/2 stale or bogus, and
many embedded subsets have no usable OS/2 at all) and reported in per-mille of
the font's own unitsPerEm, so two fonts at different UPMs are directly
comparable.

Measured, not assumed:
  x_height / cap_height / ascender / descender — vertical proportions, taken
      from flat-topped reference glyphs so an overshoot on a round letter
      ('o', 'e') can't inflate them.
  stem — vertical stem thickness, the single strongest signal of apparent
      weight. Measured by intersecting a scanline through the middle of the
      x-height band with a glyph that is (in essentially every Latin design) a
      plain vertical stem, and taking the width of the first "inside" run.
  bar — horizontal bar/crossbar thickness, which together with `stem` gives
      stroke contrast (a high-contrast serif vs. a monolinear sans).
  slant — italic angle. Mixing an upright glyph into slanted text (or the
      reverse) is an obvious defect, so this is a hard filter, not a score.
  advances — the real advance widths, so horizontal fit can be scored too.
"""
from __future__ import annotations

import math

from fontTools.pens.basePen import BasePen

# Reference glyphs per measurement, best first. Flat-topped/flat-bottomed
# shapes come first so the value isn't inflated by the overshoot a round or
# pointed letter is drawn with (an 'o' is cut a little above the x-height line
# and an 'x' is not, which is exactly why 'x' names the measurement).
_X_HEIGHT_REFS = "xzvwuns"
_CAP_HEIGHT_REFS = "HEFITZXLN"
_ASCENDER_REFS = "lbdkh"
_DESCENDER_REFS = "pqgyj"
# Glyphs whose LEFTMOST vertical feature is a plain stem, so a scanline's
# first "inside" run measures stem thickness. 'd' and 'q' are deliberately
# absent even though they have a stem: theirs is on the RIGHT, and the first
# run through them lands on the bowl instead, which is thicker and behaves
# differently under an offset — measuring those as "stem" put a 20% error into
# a validation report that was otherwise sound.
_STEM_REFS = "lIHEFBDPRnhmbkpu"
# Same set, exposed for callers that need to know which characters a stem
# comparison is meaningful on (see glyph_synth's held-out validation).
LEFT_STEM_CHARS = _STEM_REFS
# Glyphs with a horizontal bar crossing the middle of the x-height band.
_BAR_REFS = "eEfHtA"


class _PolyPen(BasePen):
    """Flattens a glyph (curves subdivided, composites decomposed by BasePen
    via the glyph set) into closed polygons in font units."""

    def __init__(self, glyph_set, tolerance: float = 2.0):
        super().__init__(glyph_set)
        self.polys: list = []
        self._cur: list = []
        # Segments per curve, from the flattening tolerance in font units. At a
        # 2048-unit em a 2-unit deviation is ~0.014pt at 14pt type — far below
        # anything a rasterizer can show — while keeping the segment count (and
        # so the scanline cost) small.
        self._steps = max(4, min(24, int(math.ceil(12.0 / max(tolerance, 0.25)))))

    def _moveTo(self, pt):
        self._flush()
        self._cur = [pt]

    def _lineTo(self, pt):
        self._cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        p0 = self._cur[-1] if self._cur else (0.0, 0.0)
        n = self._steps
        for i in range(1, n + 1):
            t = i / n
            u = 1.0 - t
            self._cur.append((
                u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
            ))

    def _closePath(self):
        self._flush()

    def _endPath(self):
        self._flush()

    def _flush(self):
        if len(self._cur) >= 3:
            self.polys.append(self._cur)
        self._cur = []

    def done(self) -> list:
        self._flush()
        return self.polys


def glyph_polys(tt, glyph_name: str, tolerance: float = 2.0) -> list:
    """Flattened closed polygons for one glyph, in font units. [] if empty."""
    gs = tt.getGlyphSet()
    if glyph_name not in gs:
        return []
    pen = _PolyPen(gs, tolerance)
    try:
        gs[glyph_name].draw(pen)
    except Exception:  # noqa: BLE001 — a broken glyph measures as empty, not as a crash
        return []
    return pen.done()


def poly_bbox(polys: list):
    """(xMin, yMin, xMax, yMax) or None."""
    if not polys:
        return None
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def scanline_runs(polys: list, y: float) -> list:
    """Horizontal "inside" runs at height *y*, as [(x_start, x_end), …].

    Uses the nonzero winding rule — the fill rule TrueType outlines are defined
    under — so a glyph built by overlapping several same-direction contours
    (which is exactly how a synthesized glyph is assembled) measures as the one
    solid shape a rasterizer will actually paint, not as separate pieces.
    """
    crossings = []
    for poly in polys:
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            if (y0 <= y < y1) or (y1 <= y < y0):
                t = (y - y0) / (y1 - y0)
                crossings.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
    if not crossings:
        return []
    crossings.sort()
    runs, winding, start = [], 0, None
    for x, direction in crossings:
        was_inside = winding != 0
        winding += direction
        now_inside = winding != 0
        if not was_inside and now_inside:
            start = x
        elif was_inside and not now_inside and start is not None:
            if x - start > 0:
                runs.append((start, x))
            start = None
    return runs


def winding_at(polys: list, x: float, y: float) -> int:
    """Nonzero-rule winding number of the whole glyph at (x, y): 0 means the
    point is outside the ink, anything else means inside.

    Counts signed crossings of a ray cast in +x. Needed because "which way is
    outward" cannot be answered per contour: a counter (the hole in 'o', 'e',
    '4') is wound opposite its outer contour, so expanding each contour's own
    enclosed area expands the hole too and eats back exactly the ink the outer
    contour gained. Asking whether a point is INK is what distinguishes the
    two cases, for any depth of nesting.
    """
    w = 0
    for poly in polys:
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            if (y0 <= y < y1) or (y1 <= y < y0):
                t = (y - y0) / (y1 - y0)
                if x0 + t * (x1 - x0) > x:
                    w += 1 if y1 > y0 else -1
    return w


def _cmap(tt) -> dict:
    try:
        return tt.getBestCmap() or {}
    except Exception:  # noqa: BLE001
        return {}


def _gname(tt, ch: str, cmap: dict = None):
    cm = cmap if cmap is not None else _cmap(tt)
    return cm.get(ord(ch))


def _first_present(tt, chars: str, cmap: dict):
    """First char of *chars* that this font actually has a non-empty glyph for,
    as (char, glyph_name, polys)."""
    for ch in chars:
        gn = _gname(tt, ch, cmap)
        if not gn:
            continue
        polys = glyph_polys(tt, gn)
        if polys:
            return ch, gn, polys
    return None, None, None


def _vertical_extreme(tt, chars: str, cmap: dict, which: str):
    """yMax (which="max") or yMin (which="min") of the first available
    reference glyph, in font units. None if none of them are in this font."""
    _, _, polys = _first_present(tt, chars, cmap)
    if not polys:
        return None
    bb = poly_bbox(polys)
    return bb[3] if which == "max" else bb[1]


def _stem_width(tt, cmap: dict, x_height: float):
    """Vertical stem thickness in font units, or None.

    Sampled at three heights inside the x-height band and reduced with the
    median, so one sample landing on a serif bracket, an ink trap, or a curved
    entry stroke can't distort the result the way a single sample would.
    """
    ch, _, polys = _first_present(tt, _STEM_REFS, cmap)
    if not polys or not x_height:
        return None
    widths = []
    for frac in (0.40, 0.50, 0.60):
        runs = scanline_runs(polys, x_height * frac)
        if runs:
            widths.append(runs[0][1] - runs[0][0])
    if not widths:
        return None
    widths.sort()
    return widths[len(widths) // 2]


def _bar_width(tt, cmap: dict, x_height: float):
    """Horizontal bar thickness in font units, or None. Measured as a vertical
    run through the crossbar, using the same median-of-samples reduction."""
    ch, _, polys = _first_present(tt, _BAR_REFS, cmap)
    if not polys or not x_height:
        return None
    bb = poly_bbox(polys)
    # Sample vertically by transposing the polygons and reusing the scanline.
    flipped = [[(y, x) for (x, y) in poly] for poly in polys]
    mid_x = (bb[0] + bb[2]) / 2.0
    heights = []
    for frac in (0.45, 0.50, 0.55):
        x_at = bb[0] + (bb[2] - bb[0]) * frac
        runs = scanline_runs(flipped, x_at)
        # The bar is the run that straddles the middle of the x-height band.
        target = x_height * 0.5
        best = None
        for y0, y1 in runs:
            if y0 - 1 <= target <= y1 + 1:
                best = y1 - y0
                break
        if best:
            heights.append(best)
    if not heights:
        return None
    heights.sort()
    return heights[len(heights) // 2]


def _slant_degrees(tt) -> float:
    """Italic angle in degrees (0 = upright, positive = forward-leaning).

    Prefers the post table's declared italicAngle (negative there by spec) and
    falls back to measuring the lean of a stem, since a subset's post table is
    sometimes zeroed even for a genuinely slanted face.
    """
    try:
        declared = float(getattr(tt["post"], "italicAngle", 0.0) or 0.0)
        if abs(declared) > 0.5:
            return -declared
    except Exception:  # noqa: BLE001
        pass
    cmap = _cmap(tt)
    x_h = _vertical_extreme(tt, _X_HEIGHT_REFS, cmap, "max")
    _, _, polys = _first_present(tt, "lIH", cmap)
    if not polys or not x_h:
        return 0.0
    lo = scanline_runs(polys, x_h * 0.15)
    hi = scanline_runs(polys, x_h * 0.85)
    if not lo or not hi:
        return 0.0
    dx = ((hi[0][0] + hi[0][1]) / 2.0) - ((lo[0][0] + lo[0][1]) / 2.0)
    dy = x_h * 0.70
    return math.degrees(math.atan2(dx, dy))


def measure(tt, probe_chars: str = "noxHl") -> dict:
    """Measure *tt*'s typographic parameters.

    Vertical/stroke values are returned BOTH in font units (`*_units`, valid
    only within this font) and normalized to per-mille of the em (`x_height`,
    `stem`, …), which is what makes two fonts at different unitsPerEm
    comparable. `advances` is per-mille too, keyed by character.
    """
    upm = float(tt["head"].unitsPerEm or 1000)
    cmap = _cmap(tt)
    per_mille = 1000.0 / upm

    x_units = _vertical_extreme(tt, _X_HEIGHT_REFS, cmap, "max")
    cap_units = _vertical_extreme(tt, _CAP_HEIGHT_REFS, cmap, "max")
    asc_units = _vertical_extreme(tt, _ASCENDER_REFS, cmap, "max")
    desc_units = _vertical_extreme(tt, _DESCENDER_REFS, cmap, "min")
    stem_units = _stem_width(tt, cmap, x_units) if x_units else None
    bar_units = _bar_width(tt, cmap, x_units) if x_units else None

    advances = {}
    try:
        hmtx = tt["hmtx"]
        for ch in probe_chars:
            gn = _gname(tt, ch, cmap)
            if gn and gn in hmtx.metrics:
                advances[ch] = hmtx[gn][0] * per_mille
    except Exception:  # noqa: BLE001
        pass

    def norm(v):
        return None if v is None else v * per_mille

    return {
        "upm": upm,
        "x_height": norm(x_units), "x_height_units": x_units,
        "cap_height": norm(cap_units), "cap_height_units": cap_units,
        "ascender": norm(asc_units), "ascender_units": asc_units,
        "descender": norm(desc_units), "descender_units": desc_units,
        "stem": norm(stem_units), "stem_units": stem_units,
        "bar": norm(bar_units), "bar_units": bar_units,
        "contrast": (bar_units / stem_units) if (bar_units and stem_units) else None,
        "slant": _slant_degrees(tt),
        "advances": advances,
        "coverage": set(cmap.keys()),
    }


# Relative weights for the match score. Stem and x-height dominate because
# they are what the eye actually catches when one letter in a word came from
# somewhere else: a stem that is 15% too heavy reads as a bold letter dropped
# into regular text, and a wrong x-height reads as the wrong size. Cap height
# and the ascender matter less (fewer characters reach them), and contrast
# less still, but a serif/sans or high-contrast mismatch shows up there.
_SCORE_WEIGHTS = {"stem": 3.0, "x_height": 3.0, "advance": 1.5,
                  "cap_height": 1.0, "ascender": 0.8, "contrast": 0.8}


def mismatch(target: dict, candidate: dict) -> dict:
    """Score how badly *candidate* would stand in for *target*.

    Returns {"score": float, "terms": {…}, "usable": bool}. `score` is a
    weighted mean of relative errors — 0.0 is a perfect metric match, and
    lower is better with no upper bound. `usable` is False for a mismatch no
    amount of scaling can rescue (a slant difference: an italic glyph among
    upright letters is a defect, not an approximation), which is why it is a
    separate flag rather than just a large score.
    """
    terms = {}
    total = weight_sum = 0.0

    def add(key, t, c):
        if not t or not c:
            return
        nonlocal total, weight_sum
        err = abs(c - t) / t
        terms[key] = err
        w = _SCORE_WEIGHTS[key]
        total += w * err
        weight_sum += w

    add("stem", target.get("stem"), candidate.get("stem"))
    add("x_height", target.get("x_height"), candidate.get("x_height"))
    add("cap_height", target.get("cap_height"), candidate.get("cap_height"))
    add("ascender", target.get("ascender"), candidate.get("ascender"))
    add("contrast", target.get("contrast"), candidate.get("contrast"))

    shared = set(target.get("advances", {})) & set(candidate.get("advances", {}))
    if shared:
        errs = [abs(candidate["advances"][c] - target["advances"][c]) / target["advances"][c]
                for c in shared if target["advances"][c]]
        if errs:
            add("advance", 1.0, 1.0 + sum(errs) / len(errs))

    usable = abs((candidate.get("slant") or 0.0) - (target.get("slant") or 0.0)) <= 4.0
    return {"score": (total / weight_sum) if weight_sum else float("inf"),
            "terms": terms, "usable": usable}


def format_report(name: str, m: dict) -> str:
    def f(v, nd=1):
        return "  n/a" if v is None else f"{v:5.{nd}f}"
    return (f"{name:26s} upm={m['upm']:>6.0f}  x={f(m['x_height'])}  cap={f(m['cap_height'])}  "
            f"asc={f(m['ascender'])}  stem={f(m['stem'])}  bar={f(m['bar'])}  "
            f"contrast={f(m['contrast'], 2)}  slant={m['slant']:5.1f}")
