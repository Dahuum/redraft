// Click → span hit-testing, ported from the proven Python `_span_at`.
//
// PDF.js renders a page at `scale` so that a PDF point (fx, fy) in PyMuPDF's
// top-left coordinate system maps to canvas pixel (fx*scale, fy*scale). The
// click handler therefore converts a canvas-pixel click back to PDF points by
// dividing by scale, then finds the smallest-area span bbox that contains it.

const PAD = 1.5; // pt — forgiveness for thin glyph rows

// Find the span on `pageIndex` whose bbox contains PDF point (px, py).
// Smallest-area match wins (most specific). Returns the span id or null.
export function spanAt(spans, pageIndex, px, py) {
  let best = null;
  let bestArea = Infinity;
  for (const s of spans) {
    if (s.page !== pageIndex) continue;
    const [x0, y0, x1, y1] = s.bbox;
    if (px >= x0 - PAD && px <= x1 + PAD && py >= y0 - PAD && py <= y1 + PAD) {
      const area = Math.max(x1 - x0, 1) * Math.max(y1 - y0, 1);
      if (area < bestArea) {
        best = s.id;
        bestArea = area;
      }
    }
  }
  return best;
}

// Human-readable position (e.g. "top-left") used as context in field lists.
export function posHint(bbox, pw, ph) {
  const cx = (bbox[0] + bbox[2]) / 2;
  const cy = (bbox[1] + bbox[3]) / 2;
  const col = cx < pw / 3 ? "left" : cx > (2 * pw) / 3 ? "right" : "center";
  const row = cy < ph / 3 ? "top" : cy > (2 * ph) / 3 ? "bottom" : "middle";
  return `${row}-${col}`;
}
