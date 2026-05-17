"""
merge_engine.py — Generic bulk PDF generator for Redraft
=========================================================
Works with any PDF template (extraction.json from pdf_extractor.py).
Substitutes variable fields per CSV/Excel row, handles text wrapping,
computed fields (TVA, amount-in-words), and renders clean PDFs via ReportLab.

Usage:
    from merge_engine import MergeEngine

    engine = MergeEngine("extraction.json", variable_fields, computed_fields)
    engine.batch("invoices.csv", "output/")

Field mapping JSON format (also accepted via CLI):

    {
      "variable_fields": {
        "p1_elem_0004": {"column": "invoice_number"},
        "p1_elem_0017": {"column": "description", "wrap": true, "leading": 13},
        "p1_elem_0021": {"column": "montant_ht",    "format": "currency"}
      },
      "computed_fields": [
        {"target": "p1_elem_0023", "formula": "montant_ht * 0.20",
         "format": "currency"},
        {"target": "p1_elem_0028",
         "formula": "amount_in_words_fr(montant_ht * 1.20)",
         "format": "text"}
      ],
      "filename_pattern": "facture_{invoice_number}.pdf",
      "csv_delimiter": ";"
    }

Install:
    pip install reportlab num2words openpyxl arabic-reshaper python-bidi
"""

import copy
import csv
import json
import os
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers  (same as main.py)
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_currency(value: float) -> str:
    """Format a float as  47 673,99  (space thousands, comma decimal)."""
    if value is None:
        return ""
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    int_part, dec_part = str(d).split(".")
    int_fmt = ""
    for i, ch in enumerate(reversed(int_part)):
        if i and i % 3 == 0:
            int_fmt = " " + int_fmt
        int_fmt = ch + int_fmt
    return f"{int_fmt},{dec_part}"


def amount_in_words_fr(value: float) -> str:
    """Convert a float amount to French words (MAD)."""
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    int_part = int(d)
    cents = int(round((float(d) - int_part) * 100))
    words = num2words(int_part, lang="fr").capitalize()
    if cents:
        words += f", {cents:02d} Cts"
    words += " . (Toutes taxes comprises)"
    return words


def _parse_amount(raw) -> float:
    """Accept '3.560,00'   '1 234,56'   '47673.99'   47673.99  →  float."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(" ", "").replace("\u00a0", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _reshape_bidi(text: str) -> str:
    """Arabic reshaping + bidirectional reordering (best-effort)."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


# ═══════════════════════════════════════════════════════════════════════════════
#  MergeEngine
# ═══════════════════════════════════════════════════════════════════════════════

class MergeEngine:
    """Generic bulk PDF generator — works with any template from pdf_extractor.py.

    Parameters
    ----------
    template_path : str
        Path to ``extraction.json`` produced by ``pdf_extractor.py``.
    variable_fields : dict
        ``{ element_id : "csv_column" }``   or
        ``{ element_id : {"column": "...", "wrap": True, "format": "currency"} }``.
    computed_fields : list[dict] | None
        Each dict: ``{"target": "elem_id", "formula": "expr", "format": "currency|text"}``.
        Formulas use ``csv_column`` names as variables and the helpers above.
    data_dirs : dict | None
        ``{"fonts": "/path/to/fonts", "images": "/path/to/images"}``.
        Defaults to folders next to the template file.
    """

    # ReportLab 14 standard fonts + common aliases
    _RL_BUILTINS = {
        "Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
        "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
        "Symbol", "ZapfDingbats",
    }
    _BUILTIN_STYLES = {
        ("normal", "normal"):   "Helvetica",
        ("bold",   "normal"):   "Helvetica-Bold",
        ("normal", "italic"):   "Helvetica-Oblique",
        ("bold",   "italic"):   "Helvetica-BoldOblique",
    }

    def __init__(
        self,
        template_path: str,
        variable_fields: Dict[str, Union[str, dict]],
        computed_fields: Optional[List[dict]] = None,
        data_dirs: Optional[dict] = None,
    ):
        with open(template_path, "r", encoding="utf-8") as f:
            self.template = json.load(f)

        self.variable_fields = self._normalise_fields(variable_fields)
        self.computed_fields = computed_fields or []

        template_dir = os.path.dirname(os.path.abspath(template_path))
        self.data_dirs = data_dirs or {}
        self.data_dirs.setdefault("fonts",  os.path.join(template_dir, "fonts"))
        self.data_dirs.setdefault("images", os.path.join(template_dir, "images"))

        self._font_map: Dict[str, str] = {}       # original_name → registered_name
        self._registered: Dict[str, str] = {}      # registered_name → file_path
        _register_extracted_fonts(self.template, self.data_dirs,
                                  self._font_map, self._registered)

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_fields(fields: dict) -> dict:
        """``"col"`` → ``{"column": "col"}``."""
        out = {}
        for elem_id, cfg in fields.items():
            out[elem_id] = {"column": cfg} if isinstance(cfg, str) else dict(cfg)
        return out

    # ── font resolution ────────────────────────────────────────────────────────

    def _resolve_font(self, el: dict) -> str:
        """Return a ReportLab-ready font name for *el*."""
        weight = el.get("font_weight", "normal")
        style  = el.get("font_style",  "normal")

        # 1. exact font_family registered?
        family = el.get("font_family", "")
        if family in self._font_map:
            return self._font_map[family]

        # 2. fallback name (from extractor's FONT_FALLBACK table)
        fallback = el.get("font_fallback", "")
        if fallback in self._font_map:
            return self._font_map[fallback]
        if fallback in self._RL_BUILTINS:
            return fallback

        # 3. built-in weight/style pair
        key = (weight, style)
        if key in self._BUILTIN_STYLES:
            return self._BUILTIN_STYLES[key]

        return "Helvetica"

    # ── data loading ───────────────────────────────────────────────────────────

    def _read_csv(self, path: str, delimiter: str = ";") -> List[dict]:
        for enc in ("utf-8-sig", "latin-1", "utf-8"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    reader = csv.DictReader(f, delimiter=delimiter)
                    rows = []
                    for r in reader:
                        clean = {}
                        for k, v in r.items():
                            key = (k or "").strip()
                            val = v.strip() if isinstance(v, str) else v
                            clean[key] = val
                        rows.append(clean)
                    return rows
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError(f"Unable to decode {path}")

    def _read_excel(self, path: str) -> List[dict]:
        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel files.  pip install openpyxl"
            )
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        raw_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not raw_rows:
            return []
        headers = [str(h or "").strip() for h in raw_rows[0]]
        result = []
        for row in raw_rows[1:]:
            if not any(v for v in row):
                continue
            result.append({
                headers[i]: str(row[i]).strip() if row[i] is not None else ""
                for i in range(len(headers))
            })
        return result

    def _load_data(self, data_path: str, delimiter: str = ";") -> List[dict]:
        ext = Path(data_path).suffix.lower()
        if ext == ".csv":
            return self._read_csv(data_path, delimiter)
        if ext in (".xlsx", ".xls"):
            return self._read_excel(data_path)
        raise ValueError(f"Unsupported format: {ext}  (use .csv or .xlsx)")

    # ── substitution ───────────────────────────────────────────────────────────

    @staticmethod
    def _find_element(page: dict, element_id: str) -> Optional[dict]:
        for el in page["elements"]:
            if el.get("id") == element_id:
                return el
        return None

    @staticmethod
    def _substitute_text(el: dict, raw_value, field_config: dict) -> dict:
        """Return a *copy* of *el* with its value replaced, formatted if needed."""
        el = copy.deepcopy(el)
        fmt = field_config.get("format", "text")

        if fmt == "currency":
            num = _parse_amount(raw_value)
            el["value"] = fmt_currency(num)
            el["_numeric_value"] = num          # kept for computed-field formulas
        elif fmt == "number":
            el["value"] = str(_parse_amount(raw_value))
            el["_numeric_value"] = _parse_amount(raw_value)
        else:
            el["value"] = str(raw_value) if raw_value is not None else ""

        return el

    def _substitute_page(self, page: dict, row: dict) -> dict:
        """Deep-copy *page* and replace variable fields from *row*."""
        page = copy.deepcopy(page)
        substituted = {}          # column_name → (possibly parsed) value

        for elem_id, fcfg in self.variable_fields.items():
            col = fcfg["column"]
            raw = row.get(col, "")
            el = self._find_element(page, elem_id)
            if el is None:
                print(f"  ⚠  element '{elem_id}' not found on page "
                      f"{page['page_number']}")
                continue

            new_el = self._substitute_text(el, raw, fcfg)
            if fcfg.get("format") in ("currency", "number"):
                substituted[col] = new_el.get("_numeric_value", _parse_amount(raw))
            else:
                substituted[col] = new_el["value"]

            # replace in-place
            elements = page["elements"]
            for i, e in enumerate(elements):
                if e.get("id") == elem_id:
                    elements[i] = new_el
                    break

        return page, substituted

    # ── computed fields ────────────────────────────────────────────────────────

    def _eval_formula(self, formula: str, values: dict) -> Any:
        safe = {
            "amount_in_words_fr": amount_in_words_fr,
            "fmt_currency":       fmt_currency,
            "round":              round,
            "min":                min,
            "max":                max,
            "abs":                abs,
            "int":                int,
            "float":              float,
            "str":                str,
            "len":                len,
        }
        try:
            return eval(formula, {"__builtins__": {}}, {**safe, **values})
        except Exception as e:
            print(f"  ⚠  formula error '{formula}': {e}")
            return ""

    def _apply_computed(self, page: dict, values: dict,
                        page_num: int) -> dict:
        """Evaluate computed fields in order, injecting results into *page*.

        Each computed field's formula can reference previous computed-field
        results (they accumulate in *values* under their column name).
        """
        for comp in self.computed_fields:
            target_id = comp.get("target") or comp.get("element_id")
            formula   = comp["formula"]
            fmt_mode  = comp.get("format", "text")
            col_name  = comp.get("column", "")       # store result back into values

            result = self._eval_formula(formula, values)

            if fmt_mode == "currency" and isinstance(result, (int, float)):
                display = fmt_currency(result)
                numeric = result
            elif fmt_mode == "number" and isinstance(result, (int, float)):
                display = str(result)
                numeric = result
            else:
                display = str(result) if result is not None else ""
                numeric = None

            el = self._find_element(page, target_id)
            if el:
                el["value"] = display
                if numeric is not None:
                    el["_numeric_value"] = numeric
            else:
                print(f"  ⚠  computed target '{target_id}' not found on "
                      f"page {page_num}")

            if col_name and numeric is not None:
                values[col_name] = numeric
            elif col_name:
                values[col_name] = display

        return page

    # ── rendering ──────────────────────────────────────────────────────────────

    def _rl_y(self, page_h: float, top_y: float, height: float = 0.0) -> float:
        """Convert top-left *top_y* to ReportLab's bottom-left y."""
        return page_h - top_y - height

    def _rl_text_y(self, page_h: float, top_y: float, font_size: float) -> float:
        """Convert top-y to ReportLab baseline y (approximate ascent)."""
        return page_h - top_y - font_size * 0.75

    def _render_one(self, c: rl_canvas.Canvas, el: dict, page_h: float):
        t = el["type"]

        if t in ("rectangle", "path"):
            x, y, w, h = el["x"], el["y"], el["width"], el["height"]
            yr = self._rl_y(page_h, y, h)

            fill_c   = el.get("fill_color")
            stroke_c = el.get("stroke_color")
            sw       = el.get("stroke_width", 0)
            has_fill = bool(fill_c and fill_c != "none")
            has_stroke = bool(stroke_c and stroke_c != "none")

            if has_fill:
                c.setFillColor(fill_c)
            if has_stroke:
                c.setStrokeColor(stroke_c)
            c.setLineWidth(sw)
            c.rect(x, yr, w, h, fill=1 if has_fill else 0,
                   stroke=1 if has_stroke else 0)

            c.setFillColor(colors.black)
            c.setStrokeColor(colors.black)
            c.setLineWidth(0.5)

        elif t == "line":
            y1r = self._rl_y(page_h, el["y1"], 0)
            y2r = self._rl_y(page_h, el["y2"], 0)
            if el.get("color"):
                c.setStrokeColor(el["color"])
            c.setLineWidth(el.get("stroke_width", 0.5))
            c.line(el["x1"], y1r, el["x2"], y2r)
            c.setStrokeColor(colors.black)
            c.setLineWidth(0.5)

        elif t == "image":
            x, y, w, h = el["x"], el["y"], el["width"], el["height"]
            yr = self._rl_y(page_h, y, h)
            fp = el.get("file_path", "")
            img_dir = self.data_dirs["images"]
            if img_dir and not os.path.isabs(fp):
                fp = os.path.join(img_dir, os.path.basename(fp))
            if os.path.exists(fp):
                try:
                    c.drawImage(fp, x, yr, width=w, height=h, mask="auto")
                except Exception as e:
                    print(f"  ⚠  image failed {fp}: {e}")

        elif t == "text":
            font = self._resolve_font(el)
            c.setFont(font, el["font_size"])
            c.setFillColor(el.get("color", "#000000"))

            value = el.get("value", "")
            x, y  = el["x"], el["y"]
            w     = el.get("width", 0)
            fs    = el["font_size"]

            if el.get("direction") == "rtl":
                value = _reshape_bidi(value)

            alignment = el.get("alignment", "left")

            if self._wrapping_enabled(el):
                leading = el.get("leading", fs * 1.3)
                style = ParagraphStyle("x", fontName=font, fontSize=fs,
                                       leading=leading,
                                       textColor=el.get("color", "#000000"))
                p = Paragraph(value, style)
                pw = el.get("width", w)
                if pw <= 0:
                    pw = max(c.stringWidth(value, font, fs) * 1.02, 100)
                _, ph = p.wrapOn(c, pw, 2000)
                yr = self._rl_y(page_h, y, ph)
                p.drawOn(c, x, yr)
            elif alignment == "right":
                yr = self._rl_text_y(page_h, y, fs)
                c.drawRightString(x + w, yr, value)
            elif alignment == "center":
                yr = self._rl_text_y(page_h, y, fs)
                c.drawCentredString(x + w / 2, yr, value)
            else:
                yr = self._rl_text_y(page_h, y, fs)
                c.drawString(x, yr, value)

            c.setFillColor(colors.black)

        elif t == "table":
            self._render_table(c, el, page_h)

    def _wrapping_enabled(self, el: dict) -> bool:
        eid = el.get("id", "")
        return bool(eid and self.variable_fields.get(eid, {}).get("wrap"))

    def _render_table(self, c: rl_canvas.Canvas, el: dict, page_h: float):
        x, y, w, h = el["x"], el["y"], el["width"], el["height"]
        headers = el.get("headers", [])
        cells   = el.get("cells",   [])
        n_cols  = max(el.get("columns", len(headers)), 1)
        n_rows  = len(cells)

        col_w = w / n_cols
        header_h = min(h * 0.25, 30.0)
        row_h = (h - header_h) / max(n_rows, 1) if n_rows else 0

        yr = self._rl_y(page_h, y, h)

        c.setStrokeColor(colors.Color(0.8, 0.8, 0.8))
        c.setLineWidth(0.5)
        c.rect(x, yr, w, h, fill=0, stroke=1)

        for col in range(1, n_cols):
            cx = x + col * col_w
            c.line(cx, yr, cx, yr + h)

        if headers:
            header_bottom = yr + h - header_h
            c.line(x, header_bottom, x + w, header_bottom)
            c.setFont("Helvetica-Bold", 8)
            c.setFillColor(colors.black)
            for col, ht in enumerate(headers):
                ty = self._rl_text_y(page_h, y + 4, 8)
                c.drawString(x + col * col_w + 2, ty, str(ht or "")[:50])

        if row_h > 0:
            c.setFont("Helvetica", 8)
            for ri, row in enumerate(cells):
                row_y = y + header_h + ri * row_h
                for ci, ct in enumerate(row):
                    ty = self._rl_text_y(page_h, row_y + 3, 8)
                    c.drawString(x + ci * col_w + 2, ty, str(ct or "")[:50])

        c.setStrokeColor(colors.black)
        c.setFillColor(colors.black)

    # ── page rendering ─────────────────────────────────────────────────────────

    def render_page(self, page: dict, output_path: str):
        """Render a single page JSON to a PDF file."""
        pw, ph = page["width_pt"], page["height_pt"]
        c = rl_canvas.Canvas(output_path, pagesize=(pw, ph))

        for el in page["elements"]:
            self._render_one(c, el, ph)

        c.save()

    # ── batch processing ───────────────────────────────────────────────────────

    def batch(
        self,
        data_path: str,
        output_dir: str,
        *,
        delimiter: str = ";",
        filename_pattern: Optional[str] = None,
    ) -> List[str]:
        """Process all data rows → one PDF per row.

        Parameters
        ----------
        data_path : str
            Path to CSV or Excel file.
        output_dir : str
            Directory where PDFs will be written (created if missing).
        delimiter : str
            CSV delimiter  (default ``";"``).
        filename_pattern : str | None
            ``"facture_{invoice_number}.pdf"`` — uses ``str.format()`` with
            the full row dict.  Default: ``"row_{row_num:04d}.pdf"``.

        Returns
        -------
        List[str]
            Absolute paths of generated PDFs.
        """
        rows = self._load_data(data_path, delimiter)
        if not rows:
            print("⚠  No data rows found.")
            return []

        os.makedirs(output_dir, exist_ok=True)
        generated: List[str] = []

        for i, row in enumerate(rows):
            if not any(v for v in row.values()):
                continue

            # --- substitute & compute ---
            page = copy.deepcopy(self.template["pages"][0])
            page, values = self._substitute_page(page, row)
            page = self._apply_computed(page, values, page["page_number"])

            # --- filename ---
            if filename_pattern:
                safe = {k: str(v).replace("/", "-").replace(" ", "_")[:60]
                        for k, v in row.items()}
                fname = filename_pattern.format(row_num=i + 1, **safe, **row)
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
            else:
                fname = f"row_{i + 1:04d}.pdf"

            pdf_path = os.path.join(output_dir, fname)
            self.render_page(page, pdf_path)
            generated.append(pdf_path)
            print(f"  ✔ [{i + 1}/{len(rows)}] {fname}")

        print(f"\n✅ {len(generated)} PDF(s) → {os.path.abspath(output_dir)}/")
        return generated


# ═══════════════════════════════════════════════════════════════════════════════
#  Font registration (shared helper)
# ═══════════════════════════════════════════════════════════════════════════════

def _register_extracted_fonts(template: dict, data_dirs: dict,
                               font_map: Dict[str, str],
                               registered: Dict[str, str]) -> None:
    """Register every extracted font with ReportLab; populate *font_map*."""
    fonts = template.get("fonts", {})
    font_dir = data_dirs["fonts"]

    for font_name, font_path in fonts.items():
        if not os.path.isabs(font_path):
            font_path = os.path.join(font_dir, os.path.basename(font_path))
        if not os.path.exists(font_path):
            continue
        if font_path.lower().endswith((".ttf", ".otf")):
            _safe = re.sub(r"[^A-Za-z0-9_-]", "_", font_name)
            safe_name = _safe[:60]
            if safe_name in registered:
                font_map[font_name] = safe_name
                continue
            try:
                pdfmetrics.registerFont(TTFont(safe_name, font_path))
                registered[safe_name] = font_path
                font_map[font_name] = safe_name
            except Exception as e:
                print(f"  ⚠  font register failed '{font_name}': {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Redraft Merge Engine")
    p.add_argument("template", help="Path to extraction.json")
    p.add_argument("data",     help="Path to CSV or Excel data file")
    p.add_argument("mapping",  help="Path to field-mapping JSON")
    p.add_argument("-o", "--output",  default=None, help="Output directory")
    p.add_argument("-d", "--delimiter", default=";", help="CSV delimiter")

    args = p.parse_args()

    with open(args.mapping, "r", encoding="utf-8") as f:
        mapping = json.load(f)

    var_fields = mapping.get("variable_fields", {})
    cmp_fields = mapping.get("computed_fields", [])
    fname_pat  = mapping.get("filename_pattern")
    delim      = mapping.get("csv_delimiter", args.delimiter)

    out = args.output or f"merged_{date.today().isoformat()}"

    engine = MergeEngine(args.template, var_fields, cmp_fields)
    engine.batch(args.data, out, delimiter=delim,
                 filename_pattern=fname_pat)
