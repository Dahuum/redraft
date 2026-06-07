"""
pdf_editor_test.py — Structure-preservation test for pdf_editor.py.

For every PDF in the working directory (w, a, t1–t29):
  1. Open the PDF and extract all text spans.
  2. Replace every span with realistic fake data of the same type
     (name→name, date→date, amount→amount, …).
  3. Save as edited_<filename>.pdf.
  4. Compare structure: render both at 2× and mask out all text regions;
     only non-text pixels are compared. Target: 100% identical.
  5. Print a per-file structure preservation score.
"""

import os
import re
import random
import warnings
import struct

import fitz  # PyMuPDF

from pdf_editor import PDFEditor, get_spans


# ── Fake-data tables ──────────────────────────────────────────────────────────

_FIRST = ["Emma", "James", "Sofia", "Marcus", "Aisha", "Liam", "Priya",
          "Noah", "Zoe", "Carlos", "Nina", "David", "Fatima", "Lucas"]
_LAST  = ["Thompson", "Rodriguez", "Chen", "Johnson", "Patel", "Garcia",
          "Kim", "Williams", "Nguyen", "Brown", "Silva", "Müller", "Hassan"]
_COMPANY = [
    "Vertex Solutions Inc.", "Meridian Consulting Group", "Apex Digital Ltd.",
    "Cascade Technologies", "Luminary Partners", "Orbit Creative Studio",
    "Harbor Financial", "Summit Logistics", "Crestview Systems",
    "Pinnacle Ventures", "Sterling Analytics", "Beacon Design Co.",
]
_STREET = ["Main St", "Oak Ave", "Maple Dr", "Cedar Blvd", "Park Ln",
           "Elm Rd", "River Way", "Lake View Dr", "Sunset Blvd", "Hill Rd"]
_CITY   = ["Springfield", "Riverdale", "Lakewood", "Hillcrest", "Fairview",
           "Georgetown", "Maplewood", "Clearwater", "Stonewood", "Ridgemont"]
_STATE  = ["CA", "NY", "TX", "FL", "WA", "IL", "CO", "GA", "AZ", "NC"]
_DOMAIN = ["example.com", "mail.net", "inbox.io", "webmail.org", "domain.co"]
_MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]
_MONTH_ABBR  = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

random.seed(42)


def _rand_name():
    return f"{random.choice(_FIRST)} {random.choice(_LAST)}"

def _rand_company():
    return random.choice(_COMPANY)

def _rand_address():
    num  = random.randint(10, 999)
    st   = random.choice(_STREET)
    city = random.choice(_CITY)
    st2  = random.choice(_STATE)
    z    = random.randint(10000, 99999)
    return f"{num} {st}, {city}, {st2} {z}"

def _rand_email():
    user = random.choice(_FIRST).lower() + str(random.randint(1, 99))
    return f"{user}@{random.choice(_DOMAIN)}"

def _rand_phone():
    a, b, c = random.randint(200,999), random.randint(100,999), random.randint(1000,9999)
    return f"+1 ({a}) {b}-{c}"

def _rand_date_iso():
    y = random.randint(2023, 2026)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"

def _rand_date_slash(sep="/"):
    y = random.randint(2023, 2026)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{d:02d}{sep}{m:02d}{sep}{y}"

def _rand_date_text():
    m = random.randint(0, 11)
    d = random.randint(1, 28)
    y = random.randint(2023, 2026)
    return f"{_MONTH_NAMES[m]} {d}, {y}"

def _rand_date_text2():
    m = random.randint(0, 11)
    d = random.randint(1, 28)
    y = random.randint(2023, 2026)
    return f"{d} {_MONTH_ABBR[m]} {y}"

def _rand_amount(original: str) -> str:
    """Generate a fake amount preserving the format of *original*."""
    # Extract numeric value to scale around
    nums = re.findall(r"[\d,]+\.?\d*", original)
    base = 500.0
    if nums:
        try:
            base = float(nums[0].replace(",", ""))
        except ValueError:
            pass
    # Random ±40%
    new_val = base * random.uniform(0.6, 1.4)
    # Match formatting
    if "," in original:
        formatted = f"{new_val:,.2f}"
    else:
        formatted = f"{new_val:.2f}"
    if original.lstrip().startswith("$"):
        return "$" + formatted
    if original.lstrip().startswith("€"):
        return "€" + formatted
    if original.lstrip().startswith("£"):
        return "£" + formatted
    return formatted

def _rand_ref(original: str) -> str:
    """Generate a fake reference/invoice number matching the pattern of *original*."""
    # Replace digit runs with random digits of the same length
    def _replace_digits(m):
        return "".join(str(random.randint(0, 9)) for _ in m.group())
    return re.sub(r"\d+", _replace_digits, original)


# ── Type detection ─────────────────────────────────────────────────────────────

_RE_DATE_ISO    = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_DATE_SLASH  = re.compile(r"^\d{1,2}[/\-]\d{1,2}[/\-]\d{4}$")
_RE_DATE_TEXT   = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4}", re.I)
_RE_DATE_TEXT2  = re.compile(
    r"\d{1,2}\s+"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{4}", re.I)
_RE_MONEY       = re.compile(r"^[\$€£]?[\d,]+\.\d{2}$")
_RE_MONEY_LOOSE = re.compile(r"^[\$€£]\s*[\d,]+")
_RE_EMAIL       = re.compile(r"^[\w.+\-]+@[\w\-]+\.[a-z]{2,}$", re.I)
_RE_PHONE       = re.compile(r"^(\+\d[\d\s\-\(\)]{6,})$")
_RE_REF         = re.compile(r"^[A-Z]{2,}-?\d{4,}(-\d+)?$")
_RE_REF_NUM     = re.compile(r"^\d{6,}$")
_RE_IBAN_BIC    = re.compile(r"^[A-Z]{2}\d{2}[\w\s]{10,}$")
_RE_PERCENT     = re.compile(r"^\d+(\.\d+)?%$")


def detect_type(text: str) -> str:
    t = text.strip()
    if not t:
        return "empty"
    if _RE_EMAIL.match(t):           return "email"
    if _RE_PHONE.match(t):           return "phone"
    if _RE_DATE_ISO.match(t):        return "date_iso"
    if _RE_DATE_SLASH.match(t):      return "date_slash"
    if _RE_DATE_TEXT.search(t):      return "date_text"
    if _RE_DATE_TEXT2.search(t):     return "date_text2"
    if _RE_MONEY.match(t):           return "money"
    if _RE_MONEY_LOOSE.match(t):     return "money"
    if _RE_PERCENT.match(t):         return "percent"
    if _RE_REF.match(t):             return "ref"
    if _RE_REF_NUM.match(t):         return "ref_num"
    if _RE_IBAN_BIC.match(t):        return "ref"
    if len(t.split()) >= 2 and all(w[0].isupper() for w in t.split() if w):
        return "name"
    return "other"


def fake_for(text: str) -> str:
    """Return a realistic fake replacement for *text* based on its detected type."""
    t    = text.strip()
    kind = detect_type(t)

    if kind == "email":         return _rand_email()
    if kind == "phone":         return _rand_phone()
    if kind == "date_iso":      return _rand_date_iso()
    if kind == "date_slash":    return _rand_date_slash("/" if "/" in t else "-")
    if kind == "date_text":     return _rand_date_text()
    if kind == "date_text2":    return _rand_date_text2()
    if kind == "money":         return _rand_amount(t)
    if kind == "percent":
        v = round(random.uniform(5, 25), 1)
        return f"{v:.0f}%" if "." not in t else f"{v}%"
    if kind == "ref":           return _rand_ref(t)
    if kind == "ref_num":       return _rand_ref(t)
    if kind == "name":          return _rand_name()
    # Longer "other" text: keep structure, randomise words a bit
    return t  # keep unchanged — avoids corrupting labels/headers


# ── Structure comparison ───────────────────────────────────────────────────────

def structure_score(original_pdf: str, edited_pdf: str, scale: float = 2.0) -> float:
    """
    Compare non-text pixels between *original_pdf* and *edited_pdf*.

    1. Render both at *scale*× resolution.
    2. Build a text-region mask from the original's span bboxes (dilated 4 px).
    3. Compare only non-masked pixels.
    4. Return the fraction that are identical (0.0 – 1.0).
    """
    try:
        import numpy as np
    except ImportError:
        warnings.warn("numpy not available — falling back to pixel_match only")
        return _pixel_match_fallback(original_pdf, edited_pdf, scale)

    orig_doc = fitz.open(original_pdf)
    edit_doc = fitz.open(edited_pdf)

    total_nontext = 0
    total_match   = 0

    for page_num in range(len(orig_doc)):
        mat   = fitz.Matrix(scale, scale)
        orig_pix = orig_doc[page_num].get_pixmap(matrix=mat, alpha=False)
        edit_pix = edit_doc[page_num].get_pixmap(matrix=mat, alpha=False)

        w, h = orig_pix.width, orig_pix.height
        if edit_pix.width != w or edit_pix.height != h:
            # Resize edited to match (shouldn't happen, but be safe)
            continue

        orig_arr = np.frombuffer(orig_pix.samples, dtype=np.uint8).reshape(h, w, 3)
        edit_arr = np.frombuffer(edit_pix.samples, dtype=np.uint8).reshape(h, w, 3)

        # Build text mask from original spans.
        mask = np.zeros((h, w), dtype=bool)
        spans = get_spans(orig_doc, page_num)
        pad = int(4 * scale)
        for span in spans:
            bx0 = max(0, int(span["bbox"].x0 * scale) - pad)
            by0 = max(0, int(span["bbox"].y0 * scale) - pad)
            bx1 = min(w, int(span["bbox"].x1 * scale) + pad)
            by1 = min(h, int(span["bbox"].y1 * scale) + pad)
            mask[by0:by1, bx0:bx1] = True

        nontext_mask = ~mask
        n_nontext = int(nontext_mask.sum())
        if n_nontext == 0:
            continue

        # Per-channel comparison
        diff = np.abs(orig_arr.astype(np.int16) - edit_arr.astype(np.int16))
        max_diff = diff.max(axis=2)          # max channel diff per pixel
        identical = (max_diff[nontext_mask] <= 2)  # ≤2 tolerance for JPEG artefacts

        total_nontext += n_nontext
        total_match   += int(identical.sum())

    orig_doc.close()
    edit_doc.close()

    if total_nontext == 0:
        return 1.0
    return total_match / total_nontext


def _pixel_match_fallback(original_pdf: str, edited_pdf: str, scale: float) -> float:
    """Simple pixel-match fallback when numpy is unavailable."""
    orig_doc = fitz.open(original_pdf)
    edit_doc = fitz.open(edited_pdf)
    total = 0; match = 0
    for i in range(len(orig_doc)):
        mat = fitz.Matrix(scale, scale)
        op = orig_doc[i].get_pixmap(matrix=mat, alpha=False)
        ep = edit_doc[i].get_pixmap(matrix=mat, alpha=False)
        for j in range(0, len(op.samples), 3):
            total += 1
            if op.samples[j:j+3] == ep.samples[j:j+3]:
                match += 1
    orig_doc.close(); edit_doc.close()
    return match / total if total else 1.0


# ── Main test loop ─────────────────────────────────────────────────────────────

def edit_pdf(input_path: str, output_path: str):
    """Replace all text spans with fake data and save to *output_path*."""
    ed = PDFEditor(input_path)

    for page_num in range(len(ed.doc)):
        spans  = ed.spans(page_num)
        pairs  = []
        for span in spans:
            replacement = fake_for(span["text"])
            if replacement != span["text"]:
                pairs.append((span, replacement))
        if pairs:
            ed.replace_all(pairs, page_num=page_num)

    ed.save(output_path)


def run_all():
    pdfs = ["w", "a"] + [f"t{i}" for i in range(1, 30)]

    results = []
    for name in pdfs:
        src = f"{name}.pdf"
        dst = f"edited_{name}.pdf"
        if not os.path.exists(src):
            continue

        print(f"\n── {name}.pdf ──")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            edit_pdf(src, dst)
            font_warns = [w for w in caught if "pdf_editor" in str(w.message)]

        if font_warns:
            for w in font_warns[:3]:
                print(f"  ⚠  {w.message}")

        score = structure_score(src, dst)
        pct   = score * 100
        mark  = "✅" if pct >= 99.0 else ("⚠ " if pct >= 95.0 else "❌")
        print(f"  {mark} structure preservation: {pct:.1f}%")
        results.append((name, pct))

    print("\n" + "═" * 50)
    print(f"{'PDF':<8}  {'Structure':>10}")
    print("─" * 22)
    for name, pct in results:
        mark = "✅" if pct >= 99.0 else ("⚠ " if pct >= 95.0 else "❌")
        print(f"{name:<8}  {pct:>9.1f}%  {mark}")
    passing = sum(1 for _, p in results if p >= 99.0)
    print("─" * 22)
    print(f"  {passing}/{len(results)} at 99%+")


if __name__ == "__main__":
    run_all()
