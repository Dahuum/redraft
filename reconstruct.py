"""
reconstruct.py — Redraw a PDF page from extraction.json using ReportLab.

Reads the structural + visual JSON produced by pdf_extractor.py,
registers the extracted fonts, and renders every element in z-order
to produce rebuilt.pdf.

Usage:
    python reconstruct.py [extraction.json] [output.pdf]

    Defaults:
        extraction.json  →  redraft_extracted/extraction.json
        output.pdf       →  rebuilt.pdf
"""

import json
import os
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

_SUBSET_RE = re.compile(r"^[A-Z]{6,8}\+")

# Keywords that mark script / handwriting / decorative fonts that must always
# be registered — Helvetica is never an acceptable substitute for these.
_SCRIPT_KEYWORDS = frozenset({
    "script", "hand", "writing", "italic", "cursive",
    "bd", "brush", "pen", "sign",
})


def _strip_prefix(name: str) -> str:
    return _SUBSET_RE.sub("", name)


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:60]


def _to_color(val):
    """Convert a hex string or a color name to a ReportLab color object."""
    if val is None or val == "none" or val == "":
        return None
    if isinstance(val, str):
        return colors.HexColor(val)
    return val


def _is_decorative_font(name: str) -> bool:
    """True if *name* looks like a script, handwriting, or decorative face."""
    lower = name.lower()
    return any(kw in lower for kw in _SCRIPT_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════════
#  Font registry
# ═══════════════════════════════════════════════════════════════════════════════

def _is_standard_font(name: str) -> bool:
    """Return True if *name* is a common sans/serif font that Helvetica can
    reasonably substitute.  Decorative, script, or symbol fonts return False."""
    PATTERNS = (
        "helvetica", "arial", "times", "courier", "symbol",
        "opensans", "poppins", "roboto", "lato", "montserrat",
        "noto", "source", "ubuntu", "verdana", "georgia",
        "tahoma", "trebuchet", "calibri", "cambria",
        "inter", "nunito", "raleway", "oswald", "merriweather",
    )
    lower = name.lower()
    return any(p in lower for p in PATTERNS)


def register_fonts(fonts_dict: dict, subset_info: dict, font_dir: str) -> dict:
    """Register extracted fonts with ReportLab.

    Every font is attempted.  Registration uses the *base name* (XXXXXX+ prefix
    stripped) so lookup is deterministic.  Fonts that cannot be loaded are
    logged as WARNING with their fallback name — no silent substitutions.

    Script / decorative fonts (BDScript, Brush, etc.) are always registered
    regardless of family.  Standard sans/serif subsets (Poppins, OpenSans …)
    are also registered; if their CMAP clashes with ReportLab's encoder the
    exception is caught and a WARNING is emitted.
    """
    lookup: dict[str, str] = {}

    for font_name, rel_path in fonts_dict.items():
        info          = subset_info.get(font_name, {})
        base_name     = info.get("base_name", _strip_prefix(font_name))
        fallback      = info.get("fallback", "Helvetica")
        is_decorative = info.get("is_decorative", False) or _is_decorative_font(base_name)

        # Resolve the file path
        path = rel_path if os.path.isabs(rel_path) else os.path.join(
            font_dir, os.path.basename(rel_path))

        if not os.path.exists(path):
            print(f"  WARNING: font {base_name} — file not found, "
                  f"falling back to {fallback}")
            continue
        if not path.lower().endswith((".ttf", ".otf")):
            continue

        # Register under the exact base name (no XXXXXX+ prefix).
        safe_base = _sanitize(base_name)
        try:
            pdfmetrics.registerFont(TTFont(safe_base, path))
        except Exception as e:
            print(f"  WARNING: font {base_name} could not be registered "
                  f"({e}) — falling back to {fallback}")
            continue

        # Map: full subset name  →  registered name
        #      base name         →  registered name  (for fallback lookup)
        lookup[font_name] = safe_base
        lookup.setdefault(base_name, safe_base)
        print(f"  ✔ Registered: {base_name} ({safe_base})")

    return lookup


# ═══════════════════════════════════════════════════════════════════════════════
#  Renderer
# ═══════════════════════════════════════════════════════════════════════════════

class PageRenderer:
    """Renders a single page from extraction JSON to a ReportLab canvas."""

    # Built-in Helvetica family used when no matching font is registered.
    _BUILTIN_STYLES = {
        ("normal", "normal"): "Helvetica",
        ("bold",   "normal"): "Helvetica-Bold",
        ("normal", "italic"): "Helvetica-Oblique",
        ("bold",   "italic"): "Helvetica-BoldOblique",
    }

    def __init__(self, page: dict, font_lookup: dict, font_dir: str,
                 img_dir: str = ""):
        self.page        = page
        self.font_lookup = font_lookup
        self.font_dir    = font_dir
        self.img_dir     = img_dir
        self.ph          = page["height_pt"]

    # ── coordinate helpers ────────────────────────────────────────────────────
    # PyMuPDF uses top-left origin (y increases downward).
    # ReportLab uses bottom-left origin (y increases upward).
    #
    # Conversion formulas:
    #   rectangles / images : rl_y = page_height - pdf_y - element_height
    #   text baseline       : rl_y = page_height - pdf_y - font_size * 0.75
    #   line endpoints      : rl_y = page_height - pdf_y   (h = 0)

    def _y(self, top: float, h: float = 0.0) -> float:
        """Top-left y (PDF) → ReportLab bottom-left y.
        Formula: page_height - top - h
        """
        return self.ph - top - h

    def _ty(self, top: float, sz: float) -> float:
        """Top-left y (PDF) → approximate text baseline y.
        Formula: page_height - top - font_size * 0.75
        """
        return self.ph - top - sz * 0.75

    # ── font resolution ───────────────────────────────────────────────────────

    def _font(self, el: dict) -> str:
        family   = el.get("font_family", "")
        fallback = el.get("font_fallback", "")
        weight   = el.get("font_weight", "normal")
        style    = el.get("font_style",  "normal")

        if family in self.font_lookup:
            return self.font_lookup[family]
        if fallback in self.font_lookup:
            return self.font_lookup[fallback]
        # Strip subset prefix and retry
        base = _strip_prefix(family)
        if base in self.font_lookup:
            return self.font_lookup[base]

        key = (weight, style)
        if key in self._BUILTIN_STYLES:
            return self._BUILTIN_STYLES[key]
        return "Helvetica"

    # ── render ────────────────────────────────────────────────────────────────

    def render(self, out_path: str):
        pw = self.page["width_pt"]
        c  = rl_canvas.Canvas(out_path, pagesize=(pw, self.ph))

        elements = self.page["elements"]

        # Guard: each element is rendered at most once.
        # Prevents double-drawing when the element list contains duplicates
        # or when script/CID font registration affects encoding layers.
        rendered_ids: set = set()

        # Split images into background (extends off-page — usually placed before
        # vector shapes in the PDF) and foreground (rasterised path images).
        pw, ph = self.page["width_pt"], self.ph
        def _is_bg_image(e):
            return (e["type"] == "image"
                    and e.get("origin") == "pymupdf_bg")

        all_rects  = [e for e in elements if e["type"] in ("path", "rectangle")]
        bg_images  = [e for e in elements if _is_bg_image(e)]
        lines      = [e for e in elements if e["type"] == "line"]
        fg_images  = [e for e in elements
                      if e["type"] == "image" and not _is_bg_image(e)]
        texts      = [e for e in elements if e["type"] == "text"]

        # Build a list of background regions.  Shapes/lines fully inside a bg
        # region are already captured by the rasterized bg_image and must be
        # skipped to avoid double-painting.
        bg_rects = []
        for bi in bg_images:
            bg_rects.append((bi["x"], bi["y"],
                             bi["x"] + bi["width"],
                             bi["y"] + bi["height"]))

        page_area = pw * ph

        def _is_full_page(el) -> bool:
            """True if the element covers ≥80% of the page — i.e. a background rect."""
            w = el.get("width", 0)
            h = el.get("height", 0)
            return w * h >= page_area * 0.8

        def _in_bg(el) -> bool:
            """True if the element's visible (on-page) region is fully enclosed by a bg region."""
            ex0 = el.get("x", el.get("x1", 0))
            ey0 = el.get("y", el.get("y1", 0))
            ex1 = ex0 + el.get("width", abs(el.get("x2", ex0) - ex0))
            ey1 = ey0 + el.get("height", abs(el.get("y2", ey0) - ey0))
            # Clip element to page bounds before checking containment
            ex0c = max(ex0, 0.0);  ey0c = max(ey0, 0.0)
            ex1c = min(ex1, pw);   ey1c = min(ey1, ph)
            if ex1c <= ex0c or ey1c <= ey0c:
                return False  # element fully off-page
            for bx0, by0, bx1, by1 in bg_rects:
                if ex0c >= bx0 - 2 and ey0c >= by0 - 2 and ex1c <= bx1 + 2 and ey1c <= by1 + 2:
                    return True
            return False

        def _overlaps_bg(el) -> bool:
            """True if the element overlaps any bg region (even partially).
            Such elements are background containers and must be drawn before bg_images,
            not after — otherwise they cover the rasterized bg image."""
            ex0 = el.get("x", el.get("x1", 0))
            ey0 = el.get("y", el.get("y1", 0))
            ex1 = ex0 + el.get("width", abs(el.get("x2", ex0) - ex0))
            ey1 = ey0 + el.get("height", abs(el.get("y2", ey0) - ey0))
            ex0c = max(ex0, 0.0);  ey0c = max(ey0, 0.0)
            ex1c = min(ex1, pw);   ey1c = min(ey1, ph)
            if ex1c <= ex0c or ey1c <= ey0c:
                return False
            for bx0, by0, bx1, by1 in bg_rects:
                # Overlap = not (completely outside)
                if ex1c > bx0 - 2 and ex0c < bx1 + 2 and ey1c > by0 - 2 and ey0c < by1 + 2:
                    return True
            return False

        full_page_rects = [e for e in all_rects if _is_full_page(e)]
        content_rects   = [e for e in all_rects if not _is_full_page(e)]

        # Paint order:
        # 1. Full-page background rects (white/colored page fill)
        # 2. Pre-bg content rects: overlap a bg_image region → drawn before bg image
        #    (they are the background containers for the rasterized scene)
        # 3. Background images (rasterized from original, preserving blending)
        # 4. Remaining content rects (skip those fully inside bg region — already captured)
        # 5. Lines (skip those in bg region)
        # 6. Foreground images (rasterized complex paths)
        # 7. Text
        for el in full_page_rects:
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            rendered_ids.add(eid)
            self._draw_rect(c, el)

        for el in content_rects:
            if not _overlaps_bg(el):
                continue
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            rendered_ids.add(eid)
            self._draw_rect(c, el)

        for el in bg_images:
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            rendered_ids.add(eid)
            self._draw_image(c, el)

        for el in content_rects:
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            if _in_bg(el):
                rendered_ids.add(eid)
                continue  # already captured in bg_image rasterization
            rendered_ids.add(eid)
            self._draw_rect(c, el)

        for el in lines:
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            if _in_bg(el):
                rendered_ids.add(eid)
                continue
            rendered_ids.add(eid)
            self._draw_line(c, el)

        for el in fg_images:
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            rendered_ids.add(eid)
            self._draw_image(c, el)

        for el in texts:
            eid = el.get("id", "")
            if eid and eid in rendered_ids:
                continue
            rendered_ids.add(eid)
            self._draw_text(c, el)

        c.save()

    def _draw_rect(self, c, el: dict):
        fill_raw   = el.get("fill_color") or el.get("fill")
        stroke_raw = el.get("stroke_color") or el.get("stroke")
        sw         = el.get("stroke_width", 0)

        fill_c   = _to_color(fill_raw)
        stroke_c = _to_color(stroke_raw)

        if fill_c is None and stroke_c is None:
            return

        x, y, w, h = el["x"], el["y"], el["width"], el["height"]
        # rl_y = page_height - pdf_y - element_height
        yr = self._y(y, h)

        # Fill-only rects bleed 0.5 pt so they extend under adjacent strokes
        # (PDF strokes are centred on the path edge).
        if fill_c is not None and stroke_c is None:
            bleed = 0.5
            x, y, w, h = x - bleed, y - bleed, w + 2 * bleed, h + 2 * bleed
            yr = self._y(y, h)  # recompute after bleed

        if fill_c is not None:
            c.setFillColor(fill_c)
        if stroke_c is not None:
            c.setStrokeColor(stroke_c)

        c.setLineWidth(max(sw, 0.0))

        radius = el.get("border_radius", 0)
        if radius > 0:
            c.roundRect(x, yr, w, h, radius,
                        fill=1 if fill_c is not None else 0,
                        stroke=1 if stroke_c is not None else 0)
        else:
            c.rect(x, yr, w, h,
                   fill=1 if fill_c is not None else 0,
                   stroke=1 if stroke_c is not None else 0)

        c.setFillColor(colors.black)
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.5)

    def _draw_line(self, c, el: dict):
        x1, y1 = el["x1"], el["y1"]
        x2, y2 = el["x2"], el["y2"]

        col = _to_color(el.get("color", "#000000"))
        sw  = el.get("stroke_width", 0.0)
        if sw <= 0:
            sw = 0.5

        if col is not None:
            c.setStrokeColor(col)
        c.setLineWidth(sw)
        # rl_y = page_height - pdf_y  (line endpoints have no height)
        c.line(x1, self._y(y1), x2, self._y(y2))
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.5)

    def _draw_text(self, c, el: dict):
        font = self._font(el)
        fs   = el["font_size"]
        c.setFont(font, fs)
        text_color = _to_color(el.get("color", "#000000"))
        if text_color is not None:
            c.setFillColor(text_color)

        value     = str(el.get("value", ""))
        x, y      = el["x"], el["y"]
        w         = el.get("width", 0)
        alignment = el.get("alignment", "left")

        # Use the exact baseline_y from PyMuPDF's span["origin"][1] when available.
        # This is far more accurate than the 0.75*fs approximation, especially for
        # fonts with non-standard ascender ratios (e.g. Poppins, custom display fonts).
        baseline_y = el.get("baseline_y")
        if baseline_y is not None:
            yr = self.ph - baseline_y
        else:
            yr = self._ty(y, fs)

        char_origins = el.get("char_origins")
        if char_origins and alignment == "left":
            # Draw each character at its exact PDF x origin to avoid font-metrics
            # mismatches between the extracted TTF and ReportLab's advance widths.
            for cx, ch in char_origins:
                if ch:
                    c.drawString(cx, yr, ch)
        elif alignment == "right":
            c.drawRightString(x + w, yr, value)
        elif alignment == "center":
            c.drawCentredString(x + w / 2.0, yr, value)
        else:
            c.drawString(x, yr, value)

        c.setFillColor(colors.black)

    def _draw_image(self, c, el: dict):
        fp = el.get("file_path", "")
        if self.img_dir and not os.path.isabs(fp):
            fp = os.path.join(self.img_dir, os.path.basename(fp))
        if not os.path.exists(fp):
            return

        x, y, w, h = el["x"], el["y"], el["width"], el["height"]
        try:
            # rl_y = page_height - pdf_y - element_height
            c.drawImage(fp, x, self._y(y, h), width=w, height=h, mask="auto")
        except Exception as e:
            print(f"  ⚠  image failed {fp}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main(json_path: str, out_path: str):
    json_path = os.path.abspath(json_path)
    base_dir  = os.path.dirname(json_path)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    font_dir = os.path.join(base_dir, "fonts")
    img_dir  = os.path.join(base_dir, "images")

    print(f"📄  Loading {json_path}  ({data['page_count']} page(s))")

    print("📦  Registering fonts...")
    font_lookup = register_fonts(data.get("fonts", {}),
                                 data.get("subset_fonts", {}),
                                 font_dir)
    print(f"    {len(font_lookup)} font name(s) mapped")

    page = data["pages"][0]
    print(f"    page {page['page_number']}: "
          f"{page['width_pt']:.1f} × {page['height_pt']:.1f} pt  "
          f"{len(page['elements'])} elements")

    renderer = PageRenderer(page, font_lookup, font_dir, img_dir)
    renderer.render(out_path)
    print(f"\n✅  rebuilt → {out_path}")


if __name__ == "__main__":
    json_path = sys.argv[1] if len(sys.argv) > 1 else "redraft_extracted/extraction.json"
    out_path  = sys.argv[2] if len(sys.argv) > 2 else "rebuilt.pdf"
    main(json_path, out_path)
