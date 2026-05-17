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
                "base_name": base_name,
                "fallback":  fallback_fn,
                "is_subset": is_subset,
                "font_type": font_type,
                "skipped":   False,
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

                elements.append({
                    "type":           "text",
                    "value":          value,
                    "x":              round(x0, 2),
                    "y":              round(y0, 2),
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


# ── Shape extraction ───────────────────────────────────────────────────────────

def _extract_shape_elements(page: fitz.Page) -> list:
    """
    Extract vector paths: rectangles, lines, curves.
    PyMuPDF get_drawings() returns all vector graphics.
    """
    elements = []

    for path in page.get_drawings():
        fill   = _parse_color(path.get("fill"))
        stroke = _parse_color(path.get("color"))
        width  = round(path.get("width") or 0, 2)
        rect   = path.get("rect")           # bounding box of the whole path

        # Check if this is a simple rectangle
        items  = path.get("items", [])
        is_rect = (
            len(items) == 5
            and all(i[0] in ("l", "re", "c") for i in items)
        )

        if rect is None:
            continue

        x0, y0, x1, y1 = rect

        if is_rect or path.get("type") == "re":
            elements.append({
                "type":         "rectangle",
                "x":            round(x0, 2),
                "y":            round(y0, 2),
                "width":        round(x1 - x0, 2),
                "height":       round(y1 - y0, 2),
                "fill_color":   fill,
                "stroke_color": stroke,
                "stroke_width": width,
                "origin":       "pymupdf",
            })
        else:
            # Generic path — store as a line using bounding box
            # (handles horizontal/vertical rules well)
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
                # Complex path — check if it has bezier curves
                curve_cmds = {"c", "v", "y", "qu", "curve"}
                has_curves = any(i[0] in curve_cmds for i in items)

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

def _rasterize_complex_paths(page: fitz.Page,
                              paths: list,
                              img_dir: str,
                              page_num: int,
                              dpi: int = 300) -> list:
    """
    Render complex vector paths (icons, bezier shapes) as PNG images.

    Uses PyMuPDF's built-in renderer to rasterize only the bounding-box
    region of each path at *dpi*.  The resulting PNGs are saved in *img_dir*
    and returned as ``{"type": "image", ...}`` elements.
    """
    os.makedirs(img_dir, exist_ok=True)
    if not paths:
        return []

    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    images = []

    for i, p in enumerate(paths):
        x, y, w, h = p["x"], p["y"], p["width"], p["height"]
        if w < 2.0 or h < 2.0:
            continue

        clip_rect = fitz.Rect(x, y, x + w, y + h) & page.rect
        if clip_rect.is_empty or clip_rect.width < 1 or clip_rect.height < 1:
            continue

        try:
            pix = page.get_pixmap(clip=clip_rect, matrix=mat, alpha=False)
        except Exception as e:
            print(f"  ⚠  rasterize failed {clip_rect}: {e}")
            continue

        img_path = os.path.join(img_dir, f"page{page_num}_path{i:04d}.png")
        pix.save(img_path)

        images.append({
            "type":      "image",
            "x":         round(x, 2),
            "y":         round(y, 2),
            "width":     round(w, 2),
            "height":    round(h, 2),
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
        xref = img_info[0]
        try:
            bbox_list = page.get_image_bbox(img_info)
            if not bbox_list:
                continue

            base_image = doc.extract_image(xref)
            img_bytes  = base_image["image"]
            ext        = base_image.get("ext", "png")

            for bbox_idx, rect in enumerate(bbox_list):
                if not rect:
                    continue
                x0, y0, x1, y1 = rect

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
        bboxes = page.get_image_bbox(img_info)
        if not bboxes:
            continue
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

                if is_white or (is_huge and not p.get("stroke_color")):
                    keep_as_rect.append({**p, "type": "rectangle"})
                elif has_curves:
                    to_rasterize.append(p)
                else:
                    keep_as_rect.append({**p, "type": "rectangle"})

            elements.extend(keep_as_rect)
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
