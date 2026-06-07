"""
pdf_extractor.py — Core extraction engine for Redraft
======================================================
Extracts every element from a born-digital PDF with exact coordinates,
fonts, colors, and shapes using PyMuPDF + pdfplumber.

Output: a clean JSON that ReportLab can redraw from.

Usage:
    from pdf_extractor import extract_pdf
    result = extract_pdf("invoice.pdf")

Install:
    pip install pymupdf pdfplumber
"""

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

import fitz          # PyMuPDF
import pdfplumber


# ── Constants ─────────────────────────────────────────────────────────────────

SUBSET_FONT_PATTERN = re.compile(r"^[A-Z]{6,8}\+")

_ARABIC_BLOCKS = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

FONT_FALLBACK = {
    "Helvetica":             "Helvetica",
    "Helvetica-Bold":        "Helvetica-Bold",
    "Helvetica-Oblique":     "Helvetica-Oblique",
    "Helvetica-BoldOblique": "Helvetica-BoldOblique",
    "Times-Roman":           "Times-Roman",
    "Times-Bold":            "Times-Bold",
    "Times-Italic":          "Times-Italic",
    "Times-BoldItalic":      "Times-BoldItalic",
    "Courier":               "Courier",
    "Courier-Bold":          "Courier-Bold",
    "Courier-Oblique":       "Courier-Oblique",
    "Courier-BoldOblique":   "Courier-BoldOblique",
    "HelveticaNeueLTStd":    "Helvetica",
    "HelveticaNeue":         "Helvetica",
    "Arial":                 "Helvetica",
    "Arial-Bold":            "Helvetica-Bold",
    "Arial-Italic":          "Helvetica-Oblique",
    "Arial-BoldItalic":      "Helvetica-BoldOblique",
    "TimesNewRoman":         "Times-Roman",
    "TimesNewRoman-Bold":    "Times-Bold",
    "TimesNewRoman-Italic":  "Times-Italic",
    "TimesNewRoman-BoldItalic": "Times-BoldItalic",
}

_FONT_BOLD   = getattr(fitz, "TEXT_FONT_BOLD",   0x10)
_FONT_ITALIC = getattr(fitz, "TEXT_FONT_ITALIC", 0x02)

# Keywords that identify script / handwriting / decorative fonts which must
# always be embedded — Helvetica is never an acceptable substitute.
_SCRIPT_KEYWORDS = frozenset({
    "script", "hand", "writing", "italic", "cursive",
    "bd", "brush", "pen", "sign",
})


def _is_decorative_font(name: str) -> bool:
    """True if *name* looks like a script, handwriting, or decorative face."""
    lower = name.lower()
    return any(kw in lower for kw in _SCRIPT_KEYWORDS)


# ── Text / script helpers ─────────────────────────────────────────────────────

def _is_arabic_char(c: str) -> bool:
    cp = ord(c)
    return any(lo <= cp <= hi for lo, hi in _ARABIC_BLOCKS)


def _has_arabic(text: str) -> bool:
    return any(_is_arabic_char(c) for c in text)


def _normalize_arabic(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _detect_text_direction(text: str) -> str:
    if not text:
        return "ltr"
    arabic = sum(1 for c in text if _is_arabic_char(c))
    alpha  = sum(1 for c in text if c.isalpha())
    if alpha == 0:
        return "ltr"
    return "rtl" if arabic / alpha > 0.5 else "ltr"


def _is_subset_font(font_name: str) -> bool:
    return bool(SUBSET_FONT_PATTERN.match(font_name))


def _strip_subset_prefix(font_name: str) -> str:
    return SUBSET_FONT_PATTERN.sub("", font_name)


def _find_fallback_font(font_name: str) -> str:
    clean = _strip_subset_prefix(font_name)
    clean = clean.split(",")[0].strip()
    if clean in FONT_FALLBACK:
        return FONT_FALLBACK[clean]
    for key, fb in FONT_FALLBACK.items():
        if key.lower() == clean.lower():
            return fb
    return "Helvetica"


# ── Color helpers ─────────────────────────────────────────────────────────────

def _rgb_to_hex(r, g, b) -> str:
    """Convert 0-1 float RGB to hex string like #061320."""
    return "#{:02X}{:02X}{:02X}".format(
        int(round(max(0.0, min(1.0, r)) * 255)),
        int(round(max(0.0, min(1.0, g)) * 255)),
        int(round(max(0.0, min(1.0, b)) * 255)),
    )


def _parse_color(color) -> Optional[str]:
    """
    PyMuPDF returns colors in several formats depending on colorspace.
    Normalise everything to hex or None.
    """
    if color is None:
        return None
    if isinstance(color, int):
        # packed RGB integer
        r = ((color >> 16) & 0xFF) / 255
        g = ((color >> 8)  & 0xFF) / 255
        b = (color         & 0xFF) / 255
        return _rgb_to_hex(r, g, b)
    if isinstance(color, (list, tuple)):
        if len(color) == 3:
            return _rgb_to_hex(*color)
        if len(color) == 1:          # grayscale
            v = color[0]
            return _rgb_to_hex(v, v, v)
        if len(color) == 4:          # CMYK → RGB with black generation
            c, m, y, k = color
            r = max(0.0, 1.0 - min(1.0, c + k))
            g = max(0.0, 1.0 - min(1.0, m + k))
            b = max(0.0, 1.0 - min(1.0, y + k))
            return _rgb_to_hex(r, g, b)
    return None


# ── Font extraction ────────────────────────────────────────────────────────────

def extract_fonts(doc: fitz.Document, out_dir: str) -> Tuple[dict, dict]:
    """
    Extract all embedded fonts from the PDF, save to out_dir.

    Returns:
        (fonts, subset_fonts)

        fonts:        { font_name : saved_file_path }
        subset_fonts: { font_name : { "base_name", "fallback",
                                      "is_subset", "font_type", "skipped" } }
    """
    os.makedirs(out_dir, exist_ok=True)
    saved  = {}
    subset = {}
    seen_xrefs = set()

    for page in doc:
        for info in page.get_fonts(full=True):
            xref      = info[0]
            font_type = info[2] if len(info) > 2 else "?"
            font_name = info[3] if len(info) > 3 else f"font_{xref}"

            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            is_subset   = _is_subset_font(font_name)
            base_name   = _strip_subset_prefix(font_name)
            fallback_fn = _find_fallback_font(font_name)

            subset[font_name] = {
                "base_name":    base_name,
                "fallback":     fallback_fn,
                "is_subset":    is_subset,
                "font_type":    font_type,
                "skipped":      False,
                "is_decorative": _is_decorative_font(base_name),
            }

            if font_type.lower() == "type3":
                subset[font_name]["skipped"] = True
                print(f"  ⚠ Skipping Type3 font xref={xref} ({font_name}) — "
                      f"glyph programs, not reusable as a font file.")
                continue

            if "cid" in font_type.lower():
                print(f"  ⚠ CID font xref={xref} ({font_name}) — "
                      f"extracting bytes but Arabic shaping tables may be incomplete.")

            try:
                font_data = doc.extract_font(xref)
                raw_bytes = font_data[3]
                if not raw_bytes:
                    subset[font_name]["skipped"] = True
                    print(f"  WARNING: font {font_name} could not be extracted "
                          f"— falling back to {fallback_fn}")
                    continue

                if raw_bytes[:4] == b"\x00\x01\x00\x00" or raw_bytes[:4] == b"true":
                    ext = ".ttf"
                elif raw_bytes[:4] == b"OTTO":
                    ext = ".otf"
                elif raw_bytes[:2] == b"%!":
                    ext = ".pfa"
                else:
                    ext = ".ttf"

                safe_name = "".join(c if c.isalnum() or c == "-" else "_"
                                    for c in font_name)
                file_path = os.path.join(out_dir, f"{safe_name}{ext}")

                with open(file_path, "wb") as f:
                    f.write(raw_bytes)

                saved[font_name] = file_path
                print(f"  ✔ Font saved: {font_name} → {file_path}")

            except Exception as e:
                subset[font_name]["skipped"] = True
                print(f"  ⚠ Could not extract font xref={xref} "
                      f"({font_name}): {e}")

    return saved, subset


# ── Text extraction ────────────────────────────────────────────────────────────

def _extract_text_elements(page: fitz.Page) -> list:
    """
    Extract every text span with exact coordinates, font, size, color.
    Uses get_text("rawdict") for maximum detail.

    Per-span metadata added:
        direction      — "ltr" | "rtl"  (script-based detection)
        font_is_subset — bool           (warn UI: edits may lose glyphs)
        font_base_name — str            (original name minus subset prefix)
        font_fallback  — str            (ReportLab-compatible fallback name)
        value          — Unicode text, NFKC-normalised for Arabic
    """
    elements = []
    blocks = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    for block in blocks:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                #raw_text = span["text"]
                raw_text = span.get("text") or "".join(c.get("c", "") for c in span.get("chars", []))
                if not raw_text.strip():
                    continue

                x0, y0, x1, y1 = span["bbox"]
                font_name  = span.get("font", "Helvetica")
                font_size  = round(span.get("size", 10), 2)
                color_int  = span.get("color", 0)
                flags      = span.get("flags", 0)

                is_bold   = bool(flags & _FONT_BOLD)
                is_italic = bool(flags & _FONT_ITALIC)

                if is_bold and is_italic:
                    font_weight, font_style = "bold", "italic"
                elif is_bold:
                    font_weight, font_style = "bold", "normal"
                elif is_italic:
                    font_weight, font_style = "normal", "italic"
                else:
                    font_weight, font_style = "normal", "normal"

                fn_lower = font_name.lower()
                if "bold" in fn_lower:
                    font_weight = "bold"
                if "italic" in fn_lower or "oblique" in fn_lower:
                    font_style = "italic"

                has_arabic = _has_arabic(raw_text)
                value = _normalize_arabic(raw_text) if has_arabic else raw_text
                direction = _detect_text_direction(value)
                font_is_subset = _is_subset_font(font_name)

                # span["origin"] is the exact baseline position (PyMuPDF top-left coords).
                # Store it so reconstruct.py can use it directly instead of approximating.
                span_origin = span.get("origin")
                baseline_y  = round(span_origin[1], 2) if span_origin else None

                # Store per-character x origins so the renderer can draw each
                # character at its exact PDF position.  This avoids font-metrics
                # mismatches when ReportLab's advance widths differ from the PDF's.
                chars_data = span.get("chars", [])
                char_origins = None
                if chars_data and len(chars_data) > 1:
                    char_origins = [
                        [round(ch.get("origin", [0, 0])[0], 2), ch.get("c", "")]
                        for ch in chars_data
                    ]

                elements.append({
                    "type":           "text",
                    "value":          value,
                    "x":              round(x0, 2),
                    "y":              round(y0, 2),
                    "baseline_y":     baseline_y,
                    "char_origins":   char_origins,
                    "width":          round(x1 - x0, 2),
                    "height":         round(y1 - y0, 2),
                    "font_family":    font_name,
                    "font_size":      font_size,
                    "font_weight":    font_weight,
                    "font_style":     font_style,
                    "color":          _parse_color(color_int) or "#000000",
                    "direction":      direction,
                    "font_is_subset": font_is_subset,
                    "font_base_name": _strip_subset_prefix(font_name),
                    "font_fallback":  _find_fallback_font(font_name),
                    "alignment":      "left",
                    "origin":         "pymupdf",
                })

    return elements


# ── Shape extraction helpers ───────────────────────────────────────────────────

def _split_line_subpaths(items, tol: float = 2.0) -> list:
    """Group ('l', p1, p2) items into connected sub-paths."""
    if not items:
        return []
    subpaths = [[items[0]]]
    for item in items[1:]:
        prev_end   = subpaths[-1][-1][2]
        curr_start = item[1]
        if abs(prev_end.x - curr_start.x) <= tol and abs(prev_end.y - curr_start.y) <= tol:
            subpaths[-1].append(item)
        else:
            subpaths.append([item])
    return subpaths


def _is_closed_axis_rect(sp, tol: float = 1.5) -> bool:
    """True if sp (4 line items) forms a closed axis-aligned rectangle."""
    if len(sp) != 4 or any(i[0] != 'l' for i in sp):
        return False
    for item in sp:
        p1, p2 = item[1], item[2]
        if not (abs(p1.x - p2.x) < tol or abs(p1.y - p2.y) < tol):
            return False  # diagonal → not axis-aligned
    end, start = sp[-1][2], sp[0][1]
    return abs(end.x - start.x) <= tol and abs(end.y - start.y) <= tol


# ── Shape extraction ───────────────────────────────────────────────────────────

def _sample_page_color(pix: "fitz.Pixmap", rect: "fitz.Rect",
                        page_w: float, page_h: float) -> Optional[str]:
    """Sample the median color of a rect region from a pre-rendered pixmap.

    Returns a hex color string or None if sampling fails.
    The pixmap is assumed to be rendered at 72 DPI (1 px = 1 pt).
    """
    try:
        import numpy as np
        x0 = max(0, int(rect.x0))
        y0 = max(0, int(rect.y0))
        x1 = min(pix.width,  int(rect.x1))
        y1 = min(pix.height, int(rect.y1))
        if x1 <= x0 or y1 <= y0:
            return None
        samples = pix.samples
        n = pix.n  # bytes per pixel (3=RGB, 4=RGBA)
        rows = []
        for row in range(y0, y1, max(1, (y1 - y0) // 8)):
            for col in range(x0, x1, max(1, (x1 - x0) // 8)):
                base = (row * pix.width + col) * n
                rows.append((samples[base], samples[base+1], samples[base+2]))
        if not rows:
            return None
        arr = sorted(rows, key=lambda t: t[0]+t[1]+t[2])
        mid = arr[len(arr)//2]
        return "#{:02X}{:02X}{:02X}".format(mid[0], mid[1], mid[2])
    except Exception:
        return None


def _extract_shape_elements(page: fitz.Page) -> list:
    """
    Extract vector paths: rectangles, lines, curves.
    PyMuPDF get_drawings() returns all vector graphics.
    """
    # Pre-render at 72 DPI (1 pt = 1 px) for color sampling of opacity-0 shapes.
    # These are PDF knockout/transparency-group elements that punch through to the
    # actual rendered color, which we must sample directly.
    _pix72 = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)

    elements = []

    for path in page.get_drawings():
        # Respect transparency: zero opacity means fully transparent.
        fill_opacity   = path.get("fill_opacity")
        stroke_opacity = path.get("stroke_opacity")

        fill   = _parse_color(path.get("fill"))
        stroke = _parse_color(path.get("color"))

        if fill_opacity is not None and fill_opacity <= 0.0:
            # Sample the rendered color to detect PDF knockout/erase transparency
            # groups that punch through a dark background to reveal white paper.
            # Only emit the sampled color when it is meaningfully lighter than
            # black (luminance > 30%), meaning a true knockout effect.
            # Dark sampled colors indicate the shape is just a metadata/bounding-box
            # element that happens to overlap an already-dark region.
            rect = path.get("rect")
            if rect is not None:
                sampled = _sample_page_color(_pix72, rect,
                                             page.rect.width, page.rect.height)
                if sampled:
                    # Parse hex → luminance check
                    try:
                        r = int(sampled[1:3], 16)
                        g = int(sampled[3:5], 16)
                        b = int(sampled[5:7], 16)
                        lum = 0.299*r + 0.587*g + 0.114*b
                        fill = sampled if lum > 76 else None  # >30% luminance
                    except Exception:
                        fill = None
                else:
                    fill = None
            else:
                fill = None
        if stroke_opacity is not None and stroke_opacity <= 0.0:
            stroke = None

        width = round(path.get("width") or 0, 2)
        rect  = path.get("rect")
        items = path.get("items", [])

        # True only for paths that actually form an axis-aligned rectangle:
        # either a single 're' item, or line items whose endpoints use no more
        # than 2 distinct x-values and 2 distinct y-values (rounded to nearest
        # integer to tolerate floating-point imprecision in PDF coordinates).
        def _items_form_rect(its) -> bool:
            # Single-item 're' paths are handled by path.get("type")=="re" — don't intercept here.
            if not its or len(its) <= 1:
                return False
            if not all(i[0] in ("l", "re", "c") for i in its):
                return False
            pts = []
            for it in its:
                for p in it[1:]:
                    if hasattr(p, "x"):
                        pts.append((round(p.x), round(p.y)))
            if not pts:
                return False
            xs = {p[0] for p in pts}
            ys = {p[1] for p in pts}
            # A rectangle has exactly 2 distinct x-values and 2 distinct y-values.
            # A single line has xs=2,ys=1 or xs=1,ys=2 which must NOT be a rect.
            # Multiple parallel lines have xs=2, ys=N>2 — also not a rect.
            return len(xs) == 2 and len(ys) == 2

        is_rect = _items_form_rect(items)

        if rect is None:
            continue

        x0, y0, x1, y1 = rect

        if is_rect or path.get("type") == "re":
            br = round(path.get("radius") or 0, 2)

            # Detect ellipses: PyMuPDF encodes an ellipse/oval as a single
            # ('re', rect, -1) item. Use max border_radius to render as a circle.
            w_shape = x1 - x0
            h_shape = y1 - y0
            if (len(items) == 1
                    and items[0][0] == "re"
                    and len(items[0]) > 2
                    and items[0][2] == -1):
                br = round(min(w_shape, h_shape) / 2.0, 2)

            elements.append({
                "type":          "rectangle",
                "x":             round(x0, 2),
                "y":             round(y0, 2),
                "width":         round(w_shape, 2),
                "height":        round(h_shape, 2),
                "fill_color":    fill,
                "stroke_color":  stroke,
                "stroke_width":  width,
                "border_radius": br,
                "origin":        "pymupdf",
            })
        else:
            is_horizontal = abs(y1 - y0) < 2
            is_vertical   = abs(x1 - x0) < 2

            if is_horizontal:
                elements.append({
                    "type":         "line",
                    "x1":           round(x0, 2),
                    "y1":           round((y0 + y1) / 2, 2),
                    "x2":           round(x1, 2),
                    "y2":           round((y0 + y1) / 2, 2),
                    "color":        stroke or "#000000",
                    "stroke_width": width,
                    "origin":       "pymupdf",
                })
            elif is_vertical:
                elements.append({
                    "type":         "line",
                    "x1":           round((x0 + x1) / 2, 2),
                    "y1":           round(y0, 2),
                    "x2":           round((x0 + x1) / 2, 2),
                    "y2":           round(y1, 2),
                    "color":        stroke or "#000000",
                    "stroke_width": width,
                    "origin":       "pymupdf",
                })
            else:
                curve_cmds = {"c", "v", "y", "qu", "curve"}
                has_curves = any(i[0] in curve_cmds for i in items)

                # For all-line paths: split into connected sub-paths and handle
                # each sub-path specifically rather than treating the whole as
                # a filled bounding-box rectangle.
                all_lines = bool(items) and all(i[0] == 'l' for i in items)
                if all_lines:
                    subpaths = _split_line_subpaths(items)
                    handled  = True
                    sub_elems: list = []

                    for sp in subpaths:
                        if len(sp) == 1:
                            # Single disconnected segment → line
                            lx1, ly1 = sp[0][1].x, sp[0][1].y
                            lx2, ly2 = sp[0][2].x, sp[0][2].y
                            if abs(lx2 - lx1) > 0.1 or abs(ly2 - ly1) > 0.1:
                                sub_elems.append({
                                    "type":         "line",
                                    "x1":           round(lx1, 2),
                                    "y1":           round(ly1, 2),
                                    "x2":           round(lx2, 2),
                                    "y2":           round(ly2, 2),
                                    "color":        stroke or "#000000",
                                    "stroke_width": width,
                                    "origin":       "pymupdf",
                                })
                        elif _is_closed_axis_rect(sp):
                            # Closed axis-aligned rectangle → filled rect
                            pts = [p for seg in sp for p in (seg[1], seg[2])]
                            rx0 = min(p.x for p in pts)
                            rx1 = max(p.x for p in pts)
                            ry0 = min(p.y for p in pts)
                            ry1 = max(p.y for p in pts)
                            if (rx1 - rx0) > 0.3 and (ry1 - ry0) > 0.3:
                                sub_elems.append({
                                    "type":          "rectangle",
                                    "x":             round(rx0, 2),
                                    "y":             round(ry0, 2),
                                    "width":         round(rx1 - rx0, 2),
                                    "height":        round(ry1 - ry0, 2),
                                    "fill_color":    fill,
                                    "stroke_color":  stroke,
                                    "stroke_width":  width,
                                    "border_radius": 0,
                                    "origin":        "pymupdf",
                                })
                        else:
                            # Non-rectangular connected polygon → rasterize
                            handled = False
                            break

                    if handled:
                        elements.extend(sub_elems)
                        continue
                    # Fall through with rasterization forced
                    has_curves = True

                elements.append({
                    "type":         "path",
                    "x":            round(x0, 2),
                    "y":            round(y0, 2),
                    "width":        round(x1 - x0, 2),
                    "height":       round(y1 - y0, 2),
                    "fill_color":   fill,
                    "stroke_color": stroke,
                    "stroke_width": width,
                    "_has_curves":  has_curves,
                    # Raw data for isolated rasterization (stripped before JSON output)
                    "_items":       items,
                    "fill":         path.get("fill"),
                    "color":        path.get("color"),
                    "width_raw":    path.get("width"),
                    "origin":       "pymupdf",
                })

    # ── Post-filter: drop zero-width lines ───────────────────────────────
    # In PDF a stroke_width of 0 means "thinnest possible line" which is
    # device-dependent and effectively invisible at print resolutions.
    # These are never intentional visible elements.
    elements = [e for e in elements
                if not (e["type"] == "line" and e.get("stroke_width", 1) == 0)]

    # ── Post-filter: drop ghost shadow duplicates ────────────────────────
    # PyMuPDF's get_drawings() sometimes returns the same shape twice:
    # once with the correct fill colour, and once with fill=#000000 at
    # stroke_width=0.  These shadow artefacts paint over the real content.
    TOL = 2.0

    def _same_bbox(a: dict, b: dict) -> bool:
        for k in ("x", "y", "width", "height"):
            if k in a and k in b:
                if abs(a[k] - b[k]) > TOL:
                    return False
        return True

    def _is_ghost(e: dict, others: list) -> bool:
        if e["type"] not in ("path", "rectangle"):
            return False
        if e.get("stroke_width", 1) != 0:
            return False
        fc = e.get("fill_color")
        if fc is None or fc.upper() != "#000000":
            return False
        for o in others:
            if o is e:
                continue
            if o["type"] not in ("path", "rectangle"):
                continue
            if not _same_bbox(e, o):
                continue
            ofc = o.get("fill_color")
            if ofc is not None and ofc.upper() != "#000000":
                return True
        return False

    ghost_ids = {id(e) for e in elements if _is_ghost(e, elements)}
    elements = [e for e in elements if id(e) not in ghost_ids]

    return elements


# ── Complex path rasterization ────────────────────────────────────────────────

def _draw_path_on_shape(shape, items: list):
    """Replay path items onto a PyMuPDF shape object."""
    for item in items:
        cmd = item[0]
        try:
            if cmd == "l":
                shape.draw_line(item[1], item[2])
            elif cmd == "c":
                # cubic bezier: p1=start, p2=ctrl1, p3=ctrl2, p4=end
                shape.draw_bezier(item[1], item[2], item[3], item[4])
            elif cmd == "re":
                shape.draw_rect(item[1])
            elif cmd == "qu":
                shape.draw_quad(item[1])
            # 'm' (moveto) is implicit when consecutive draw_* calls are non-adjacent
        except Exception:
            pass  # skip malformed items


def _rasterize_path_isolated(path_data: dict,
                              page_w: float, page_h: float,
                              clip_rect: fitz.Rect,
                              scale: float) -> Optional[bytes]:
    """
    Rasterize a single path in isolation on a transparent background.

    Creates a temporary blank page, replays the path's drawing commands,
    and rasterizes only the clip_rect region.  Returns raw PNG bytes.
    """
    items = path_data.get("_items", [])
    fill  = path_data.get("fill")    # tuple (r, g, b) or None
    color = path_data.get("color")   # tuple (r, g, b) or None
    width = path_data.get("width") or 0

    if not items:
        return None

    try:
        doc  = fitz.open()
        pg   = doc.new_page(width=page_w, height=page_h)
        shp  = pg.new_shape()

        _draw_path_on_shape(shp, items)

        shp.finish(
            fill=fill,
            color=color,
            width=max(width, 0.5) if color else 0,
            closePath=True,
            even_odd=False,
            fill_opacity=1.0 if fill is not None else 0.0,
            stroke_opacity=1.0 if color is not None else 0.0,
        )
        shp.commit()

        mat = fitz.Matrix(scale, scale)
        pix = pg.get_pixmap(clip=clip_rect, matrix=mat, alpha=True)
        doc.close()
        return pix.tobytes("png")
    except Exception:
        return None


def _rasterize_complex_paths(page: fitz.Page,
                              paths: list,
                              img_dir: str,
                              page_num: int,
                              dpi: int = 300) -> list:
    """
    Render complex vector paths (icons, bezier shapes) as PNG images.

    Each path is rendered IN ISOLATION on a transparent background so that
    surrounding text and other page content is not baked into the raster image
    (which would cause double-rendering when text is drawn again as vectors).
    Falls back to full-page clip rasterization if isolation fails.
    """
    os.makedirs(img_dir, exist_ok=True)
    if not paths:
        return []

    scale = dpi / 72.0
    images = []

    for i, p in enumerate(paths):
        x, y, w, h = p["x"], p["y"], p["width"], p["height"]
        if w < 2.0 or h < 2.0:
            continue

        clip_rect = fitz.Rect(x, y, x + w, y + h) & page.rect
        if clip_rect.is_empty or clip_rect.width < 1 or clip_rect.height < 1:
            continue

        # Try isolated rasterization first
        png_bytes = _rasterize_path_isolated(
            p, page.rect.width, page.rect.height, clip_rect, scale
        )

        if png_bytes is None:
            # Fallback: full-page clip (may include surrounding content)
            try:
                mat = fitz.Matrix(scale, scale)
                pix = page.get_pixmap(clip=clip_rect, matrix=mat, alpha=False)
                png_bytes = pix.tobytes("png")
            except Exception as e:
                print(f"  ⚠  rasterize failed {clip_rect}: {e}")
                continue

        img_path = os.path.join(img_dir, f"page{page_num}_path{i:04d}.png")
        with open(img_path, "wb") as fh:
            fh.write(png_bytes)

        images.append({
            "type":      "image",
            "x":         round(clip_rect.x0, 2),
            "y":         round(clip_rect.y0, 2),
            "width":     round(clip_rect.width, 2),
            "height":    round(clip_rect.height, 2),
            "file_path": img_path,
            "origin":    "pymupdf_rasterized",
        })

    return images


# ── Image extraction ───────────────────────────────────────────────────────────

def _extract_image_elements(page: fitz.Page, doc: fitz.Document,
                             img_dir: str, page_num: int) -> list:
    """
    Extract embedded raster images with their positions.
    Saves each image to img_dir for use in reconstruction.

    Fixed: get_image_bbox returns list[Rect]; we iterate over it.
    """
    os.makedirs(img_dir, exist_ok=True)
    elements = []

    for img_info in page.get_images(full=True):
        xref   = img_info[0]
        smask  = img_info[1]  # soft-mask xref (0 if none)
        try:
            bbox_raw = page.get_image_bbox(img_info)
            if not bbox_raw:
                continue

            # get_image_bbox may return a single Rect or a list of Rects
            # depending on the PyMuPDF version.
            if isinstance(bbox_raw, fitz.Rect):
                bbox_list = [bbox_raw]
            else:
                bbox_list = list(bbox_raw) if bbox_raw else []

            base_image = doc.extract_image(xref)
            img_bytes  = base_image["image"]
            ext        = base_image.get("ext", "png")

            # If a soft mask (SMask) exists, composite it as an alpha channel
            # so the image renders with correct transparency.
            if smask:
                try:
                    import io
                    from PIL import Image as PILImage
                    mask_data = doc.extract_image(smask)
                    rgb_img   = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")
                    mask_img  = PILImage.open(io.BytesIO(mask_data["image"])).convert("L")
                    if mask_img.size != rgb_img.size:
                        mask_img = mask_img.resize(rgb_img.size, PILImage.LANCZOS)
                    rgb_img.putalpha(mask_img)
                    buf = io.BytesIO()
                    rgb_img.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                    ext = "png"
                except Exception:
                    pass  # fall back to image without alpha

            for bbox_idx, rect in enumerate(bbox_list):
                if not isinstance(rect, fitz.Rect):
                    continue
                if rect.is_empty:
                    continue
                x0, y0, x1, y1 = rect

                # Detect background images: those with the bounding box extending
                # meaningfully off the page.  For these, rasterize the page region
                # directly (preserving blending/overlay effects) instead of using
                # the raw embedded image, which would ignore overlapping vector shapes.
                is_background = (x0 < -1 or y0 < -1
                                 or x1 > page.rect.width + 1
                                 or y1 > page.rect.height + 1)

                if is_background:
                    clip = rect & page.rect  # visible portion only
                    if clip.is_empty or clip.width < 1 or clip.height < 1:
                        continue
                    try:
                        mat = fitz.Matrix(2.0, 2.0)  # 144 DPI
                        pix = page.get_pixmap(clip=clip, matrix=mat, alpha=False)
                        img_bytes_bg = pix.tobytes("png")
                        img_path = os.path.join(
                            img_dir,
                            f"page{page_num}_img{xref}_{bbox_idx}_bg.png"
                        )
                        with open(img_path, "wb") as f:
                            f.write(img_bytes_bg)
                        elements.append({
                            "type":      "image",
                            "x":         round(clip.x0, 2),
                            "y":         round(clip.y0, 2),
                            "width":     round(clip.width, 2),
                            "height":    round(clip.height, 2),
                            "file_path": img_path,
                            "origin":    "pymupdf_bg",
                        })
                    except Exception as e:
                        print(f"  ⚠ bg rasterize failed xref={xref}: {e}")
                    continue

                img_path = os.path.join(
                    img_dir,
                    f"page{page_num}_img{xref}_{bbox_idx}.{ext}"
                )
                with open(img_path, "wb") as f:
                    f.write(img_bytes)

                elements.append({
                    "type":      "image",
                    "x":         round(x0, 2),
                    "y":         round(y0, 2),
                    "width":     round(x1 - x0, 2),
                    "height":    round(y1 - y0, 2),
                    "file_path": img_path,
                    "origin":    "pymupdf",
                })
        except Exception as e:
            print(f"  ⚠ Could not extract image xref={xref}: {e}")

    return elements


# ── Table extraction ───────────────────────────────────────────────────────────

def _extract_table_elements(pdf_path: str, page_num: int) -> list:
    """
    Use pdfplumber for table structure — much better than PyMuPDF for tables.
    Returns table elements with headers and cell values.
    """
    elements = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num]
            tables = page.extract_tables()
            table_bboxes = page.find_tables()

            for i, (table, tbl_obj) in enumerate(zip(tables, table_bboxes)):
                if not table:
                    continue

                bbox = tbl_obj.bbox   # (x0, top, x1, bottom) in pdfplumber coords
                x0, top, x1, bottom = bbox

                headers = table[0] if table else []
                cells   = table[1:] if len(table) > 1 else []

                elements.append({
                    "type":     "table",
                    "x":        round(x0, 2),
                    "y":        round(top, 2),
                    "width":    round(x1 - x0, 2),
                    "height":   round(bottom - top, 2),
                    "columns":  len(headers),
                    "rows":     len(cells),
                    "headers":  [str(h or "") for h in headers],
                    "cells":    [[str(c or "") for c in row] for row in cells],
                    "origin":   "pdfplumber",
                })
    except Exception as e:
        print(f"  ⚠ Table extraction error on page {page_num}: {e}")

    return elements


# ── Is scanned? ────────────────────────────────────────────────────────────────

def _is_scanned(page: fitz.Page) -> bool:
    """
    Heuristic: a page is likely scanned only if it has very few text words
    AND a single large raster image covering most of the page.

    Designer-made PDFs with background images + overlay text
    (common in Moroccan print-shop invoices) should NOT trigger this.
    """
    words = page.get_text("words")
    imgs  = page.get_images()
    if not imgs:
        return False

    page_area = page.rect.width * page.rect.height
    has_full_page_img = False
    for img_info in imgs:
        try:
            bboxes = page.get_image_bbox(img_info)
        except Exception:
            continue
        if not bboxes:
            continue
        # bboxes may be a Rect or a list of Rects depending on PyMuPDF version
        if isinstance(bboxes, fitz.Rect):
            bboxes = [bboxes]
        for bbox in bboxes:
            if bbox.width * bbox.height > page_area * 0.7:
                has_full_page_img = True
                break
        if has_full_page_img:
            break

    return len(words) < 10 and has_full_page_img


# ── Main extraction ────────────────────────────────────────────────────────────

def extract_pdf(pdf_path: str, output_dir: str = None) -> dict:
    """
    Full extraction pipeline for a born-digital PDF.

    Args:
        pdf_path:   Path to the PDF file.
        output_dir: Where to save extracted fonts and images.
                    Defaults to a folder next to the PDF.

    Returns:
        dict with schema:
        {
            "source_file": "...",
            "page_count": N,
            "fonts": { font_name: file_path, ... },
            "subset_fonts": { font_name: {
                "base_name", "fallback", "is_subset", "font_type", "skipped"
            }, ... },
            "pages": [
                {
                    "page_number": 1,
                    "width_pt": 595.32,
                    "height_pt": 841.92,
                    "is_scanned": false,
                    "elements": [ ... ]
                }
            ]
        }
    """
    pdf_path   = str(pdf_path)
    output_dir = output_dir or str(Path(pdf_path).parent / "redraft_extracted")
    font_dir   = os.path.join(output_dir, "fonts")
    img_dir    = os.path.join(output_dir, "images")

    print(f"\n🔍 Extracting: {pdf_path}")
    doc = fitz.open(pdf_path)

    print("📦 Extracting fonts...")
    fonts, subset_fonts = extract_fonts(doc, font_dir)

    pages_data = []
    for page_num, page in enumerate(doc):
        print(f"📄 Processing page {page_num + 1}/{len(doc)}...")

        rect   = page.rect
        page_w = round(rect.width,  2)
        page_h = round(rect.height, 2)
        scanned = _is_scanned(page)

        if scanned:
            print(f"  ⚠ Page {page_num+1} appears to be scanned — OCR needed for text.")

        elements = []

        shapes = _extract_shape_elements(page)
        simple  = [s for s in shapes if s["type"] != "path"]
        complex = [s for s in shapes if s["type"] == "path"]
        elements.extend(simple)

        if complex:
            keep_as_rect = []
            to_rasterize = []
            for p in complex:
                is_white = (p.get("fill_color") or "").upper() == "#FFFFFF"
                has_curves = p.pop("_has_curves", False)
                page_area = page_w * page_h
                is_huge  = (p["width"] * p["height"]) > page_area * 0.8

                # Rasterize curved paths regardless of fill color — a white curved
                # shape (e.g. signature drawn in white on a dark background) must be
                # rasterized in isolation, not collapsed to a plain white rectangle.
                if has_curves:
                    to_rasterize.append(p)
                elif is_white or (is_huge and not p.get("stroke_color")):
                    keep_as_rect.append({**p, "type": "rectangle"})
                else:
                    keep_as_rect.append({**p, "type": "rectangle"})

            _internal = {"_items", "_has_curves", "fill", "color", "width_raw"}
            clean_rects = [{k: v for k, v in r.items() if k not in _internal}
                           for r in keep_as_rect]
            elements.extend(clean_rects)
            if to_rasterize:
                rasterized = _rasterize_complex_paths(page, to_rasterize,
                                                       img_dir, page_num + 1)
                elements.extend(rasterized)
                print(f"  + {len(rasterized)} rasterized paths", end="")
            if keep_as_rect:
                print(f"  ({len(keep_as_rect)} kept as rect)", end="")
            print()
        else:
            print()

        images = _extract_image_elements(page, doc, img_dir, page_num + 1)
        elements.extend(images)
        print(f"  ↳ {len(images)} images")

        if not scanned:
            texts = _extract_text_elements(page)
            elements.extend(texts)
            print(f"  ↳ {len(texts)} text spans")

        tables = _extract_table_elements(pdf_path, page_num)
        elements.extend(tables)
        print(f"  ↳ {len(tables)} tables")

        for i, el in enumerate(elements):
            el["id"] = f"p{page_num+1}_elem_{i+1:04d}"

        pages_data.append({
            "page_number": page_num + 1,
            "width_pt":    page_w,
            "height_pt":   page_h,
            "is_scanned":  scanned,
            "elements":    elements,
        })

    doc.close()

    result = {
        "source_file":  pdf_path,
        "page_count":   len(pages_data),
        "fonts":        fonts,
        "subset_fonts": subset_fonts,
        "pages":        pages_data,
    }

    # Save JSON
    json_path = os.path.join(output_dir, "extraction.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total_elements = sum(len(p["elements"]) for p in pages_data)
    print(f"\n✅ Done. {total_elements} elements across {len(pages_data)} page(s).")
    print(f"📁 Output: {json_path}")

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <file.pdf> [output_dir]")
        sys.exit(1)

    pdf  = sys.argv[1]
    out  = sys.argv[2] if len(sys.argv) > 2 else None
    data = extract_pdf(pdf, out)
    print(json.dumps(data, indent=2, ensure_ascii=False))
