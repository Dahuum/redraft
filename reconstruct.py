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
    """Register non-subset TTF/OTF fonts + decorative subset fonts.

    Skips subset fonts whose base name is a standard sans/serif family
    (Helvetica/Arial/OpenSans/Poppins/...) because their CMAPs clash with
    ReportLab encoding, causing offset-duplicate text.  The built-in
    Helvetica fallback renders them cleanly.

    Keeps subset fonts that are *not* standard (BDScript, decorative,
    symbol, handwriting) because Helvetica cannot reasonably substitute
    a script face.
    """
    lookup: dict[str, str] = {}

    for font_name, rel_path in fonts_dict.items():
        info = subset_info.get(font_name, {})
        is_subset = info.get("is_subset") or info.get("skipped")

        if is_subset and _is_standard_font(info.get("base_name", font_name)):
            continue

        path = rel_path if os.path.isabs(rel_path) else os.path.join(
            font_dir, os.path.basename(rel_path))
        if not os.path.exists(path):
            continue
        if not path.lower().endswith((".ttf", ".otf")):
            continue

        safe_name = _sanitize(font_name)
        try:
            pdfmetrics.registerFont(TTFont(safe_name, path))
        except Exception as e:
            print(f"  ⚠  font register failed '{font_name}': {e}")
            continue

        lookup[font_name] = safe_name
        base = _strip_prefix(font_name)
        if base != font_name:
            lookup.setdefault(base, safe_name)

    return lookup


# ═══════════════════════════════════════════════════════════════════════════════
#  Renderer
# ═══════════════════════════════════════════════════════════════════════════════

class PageRenderer:
    """Renders a single page from extraction JSON to a ReportLab canvas."""

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

    # coordinate helpers -------------------------------------------------------

    def _y(self, top: float, h: float = 0.0) -> float:
        """Top-left y → ReportLab bottom-left y."""
        return self.ph - top - h

    def _ty(self, top: float, sz: float) -> float:
        """Top-left y → approximate baseline y."""
        return self.ph - top - sz * 0.75

    # font ---------------------------------------------------------------------

    def _font(self, el: dict) -> str:
        family  = el.get("font_family", "")
        fallback = el.get("font_fallback", "")
        weight  = el.get("font_weight", "normal")
        style   = el.get("font_style",  "normal")

        if family in self.font_lookup:
            return self.font_lookup[family]
        if fallback in self.font_lookup:
            return self.font_lookup[fallback]

        key = (weight, style)
        if key in self._BUILTIN_STYLES:
            return self._BUILTIN_STYLES[key]
        return "Helvetica"

    # render -------------------------------------------------------------------

    def render(self, out_path: str):
        pw = self.page["width_pt"]
        c = rl_canvas.Canvas(out_path, pagesize=(pw, self.ph))

        for el in self.page["elements"]:
            t = el["type"]

            if t in ("path", "rectangle"):
                self._draw_rect(c, el)
            elif t == "line":
                self._draw_line(c, el)
            elif t == "text":
                self._draw_text(c, el)
            elif t == "image":
                self._draw_image(c, el)
            # table type is intentionally skipped — individual text spans
            # and line elements inside the table region already cover its
            # visual content. Rendering the table struct separately would
            # double-draw cell text and produce incorrect grid overlaps.

        c.save()

    def _draw_rect(self, c, el: dict):
        fill_raw = el.get("fill_color") or el.get("fill")
        stroke_raw = el.get("stroke_color") or el.get("stroke")
        sw = el.get("stroke_width", 0)

        fill_c   = _to_color(fill_raw)
        stroke_c = _to_color(stroke_raw)

        if fill_c is None and stroke_c is None:
            return

        x, y, w, h = el["x"], el["y"], el["width"], el["height"]
        yr = self._y(y, h)

        if fill_c is not None:
            c.setFillColor(fill_c)
        if stroke_c is not None:
            c.setStrokeColor(stroke_c)

        c.setLineWidth(max(sw, 0.0))
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

        value = str(el.get("value", ""))
        x, y = el["x"], el["y"]
        w = el.get("width", 0)
        alignment = el.get("alignment", "left")

        # choose rendering method
        if alignment == "right":
            yr = self._ty(y, fs)
            c.drawRightString(x + w, yr, value)
        elif alignment == "center":
            yr = self._ty(y, fs)
            c.drawCentredString(x + w / 2.0, yr, value)
        else:
            yr = self._ty(y, fs)
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
