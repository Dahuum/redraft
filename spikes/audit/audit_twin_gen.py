"""audit_twin_gen.py — the twin test for PDFs made by PROGRAMS.

Invoices, receipts and statements are mostly written by code — ReportLab,
fpdf2 — not by a word processor. For those the "source" is the data, so the
twin is exact: generate the document again with the changed value.

Four font setups, because they are four different PDFs underneath:
  reportlab/std   Helvetica, a standard-14 font: NOT embedded at all
  reportlab/ttf   DejaVu Sans, embedded as a subset
  fpdf2/std       Helvetica via fpdf2
  fpdf2/ttf       DejaVu Sans via fpdf2 (Type0/CID)

Needs reportlab and fpdf2 (run with a venv that has them); skips otherwise.
Verdicts as audit_twin.py.
"""
import io
import os
import subprocess
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402,F401
warnings.simplefilter("ignore")
import fitz  # noqa: E402
from api import extract_spans, apply_replacements  # noqa: E402

try:
    import reportlab  # noqa: F401
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
    from reportlab.platypus import Paragraph, Frame
    from reportlab.lib.styles import ParagraphStyle
    from fpdf import FPDF
except ImportError:
    print("SKIP - needs reportlab and fpdf2")
    sys.exit(0)


def _fc(pattern):
    return subprocess.run(["fc-match", "-f", "%{file}", pattern],
                          capture_output=True, text=True).stdout


DEJAVU = _fc("DejaVu Sans:style=Book")
DEJAVU_B = _fc("DejaVu Sans:style=Bold")
DATA = {
    "name": "Nadia Benali", "company": "Atlas Consulting SARL", "date": "14/03/2024",
    "vat": "MA9911223", "item": "Implementation support", "amount": "11,400.00",
    "para": ("This statement confirms that Nadia Benali has settled every invoice "
             "issued by Atlas Consulting SARL for the period ending 14/03/2024, "
             "and that no further amount is due on this account."),
}


def reportlab_pdf(d, ttf):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    if ttf:
        pdfmetrics.registerFont(RLTTFont("DV", DEJAVU))
        pdfmetrics.registerFont(RLTTFont("DVB", DEJAVU_B))
        reg, bold = "DV", "DVB"
    else:
        reg, bold = "Helvetica", "Helvetica-Bold"
    c.setFont(bold, 16)
    c.drawString(60, 780, "STATEMENT OF ACCOUNT")
    c.setFont(reg, 11)
    y = 740
    for label, key in (("Billed to:", "name"), ("Company:", "company"),
                       ("Date of issue:", "date"), ("VAT number:", "vat")):
        c.drawString(60, y, label)
        c.drawString(170, y, d[key])
        y -= 18
    c.drawString(60, y - 10, d["item"])
    c.drawRightString(530, y - 10, d["amount"])
    style = ParagraphStyle("p", fontName=reg, fontSize=11, leading=15)
    Frame(60, 420, 470, 200, showBoundary=0).addFromList([Paragraph(d["para"], style)], c)
    c.save()
    return buf.getvalue()


def fpdf_pdf(d, ttf):
    pdf = FPDF(format="A4")
    import datetime
    pdf.set_creation_date(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc))
    pdf.add_page()
    if ttf:
        pdf.add_font("DV", "", DEJAVU)
        pdf.add_font("DV", "B", DEJAVU_B)
        fam = "DV"
    else:
        fam = "Helvetica"
    pdf.set_font(fam, "B", 16)
    pdf.cell(0, 10, "STATEMENT OF ACCOUNT", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 11)
    for label, key in (("Billed to:", "name"), ("Company:", "company"),
                       ("Date of issue:", "date"), ("VAT number:", "vat")):
        pdf.cell(40, 7, label)
        pdf.cell(0, 7, d[key], new_x="LMARGIN", new_y="NEXT")
    pdf.cell(120, 9, d["item"])
    pdf.cell(0, 9, d["amount"], align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.multi_cell(0, 6, d["para"])
    return bytes(pdf.output())


CASES = [("name", "Nadia Benkirane"), ("name", "Zoé Ångström"),
         ("date", "28/11/2025"), ("vat", "MA4455667"),
         ("company", "Atlas Group"), ("amount", "9,875.50"),
         ("item", "Architecture audit")]

DPI = 110


def words(pdf):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return d[0].get_text("words")
    finally:
        d.close()


def compare(a, b):
    wa, wb = words(a), words(b)
    pool, off = list(wb), []
    for w in wa:
        k = next((i for i, v in enumerate(pool) if v[4] == w[4]
                  and abs(v[0] - w[0]) <= 0.5 and abs(v[1] - w[1]) <= 0.5), None)
        if k is None:
            near = min((v for v in wb if v[4] == w[4]),
                       key=lambda v: abs(v[0] - w[0]) + abs(v[1] - w[1]), default=None)
            off.append((w[4], round(near[0] - w[0], 1) if near else None,
                        round(near[1] - w[1], 1) if near else None))
        else:
            pool.pop(k)
    pa = fitz.open(stream=a, filetype="pdf")[0].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
    pb = fitz.open(stream=b, filetype="pdf")[0].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
    sa, sb = pa.samples, pb.samples
    frac = sum(1 for i in range(len(sa)) if abs(sa[i] - sb[i]) > 48) / len(sa)
    return off, frac


GENS = {"reportlab/std": lambda d: reportlab_pdf(d, False),
        "reportlab/ttf": lambda d: reportlab_pdf(d, True),
        "fpdf2/std": lambda d: fpdf_pdf(d, False),
        "fpdf2/ttf": lambda d: fpdf_pdf(d, True)}

def cross_line(spans, old, new):
    """The (span, new_text) pair that changes *old* where it wraps from the
    end of one line to the start of the next, split at a word boundary."""
    ws = old.split()
    for k in range(1, len(ws)):
        head, tail = " ".join(ws[:k]), " ".join(ws[k:])
        for a in spans:
            ta = a["text"].rstrip()
            if not ta.endswith(head) or (len(ta) > len(head) and ta[-len(head) - 1] != " "):
                continue
            below = [b for b in spans if b.get("page", 0) == a.get("page", 0)
                     and 5 < b["origin"][1] - a["origin"][1] < 3 * a["size"]
                     and abs(b["origin"][0] - a["bbox"][0]) < 1.0]
            below.sort(key=lambda b: b["origin"][1])
            if below and below[0]["text"].startswith(tail + " "):
                b = below[0]
                return [(a, a["text"].rstrip()[:-len(head)] + new
                         + a["text"][len(a["text"].rstrip()):]),
                        (b, b["text"][len(tail) + 1:])]
    return None


tally = {}
print(f"{'producer':15} {'field':8} {'new':20} {'engine':9} {'off':>4} {'ink':>8}  verdict")
for gname, gen in GENS.items():
    orig = gen(DATA)
    c_off, c_frac = compare(orig, gen(DATA))
    print(f"{gname:15} (control)                     {len(c_off):>4} {c_frac * 100:7.3f}%")
    for key, new in CASES:
        d2 = dict(DATA)
        old = DATA[key]
        d2[key] = new
        d2["para"] = DATA["para"].replace(old, new)
        twin = gen(d2)
        spans = [s for s in extract_spans(orig) if old in s["text"]]
        if not spans:
            print(f"{gname:15} {key:8} {new:20} not found")
            continue
        out = orig
        engines = []
        # Every occurrence, as the twin changes every occurrence.
        for sd in spans:
            cur = [s for s in extract_spans(out)
                   if old in s["text"] and abs(s["bbox"][1] - sd["bbox"][1]) < 1]
            if not cur:
                continue
            out2, rep = apply_replacements(out, [(cur[0], cur[0]["text"].replace(old, new))],
                                           preserve_size=True, try_inplace=True)
            ip = rep["in_place"]
            engines.append("reflow" if ip.get("reflowed") else
                           "in-place" if ip["count"] else
                           ("refused" if out2 == out else "redraw"))
            out = out2
        # An occurrence that WRAPS ("…by Atlas Consulting / SARL for…") sits
        # in no single span. A user changes it by editing both lines, in one
        # request; do the same.
        pair = cross_line(extract_spans(out), old, new)
        if pair:
            out2, rep = apply_replacements(out, pair, preserve_size=True, try_inplace=True)
            ip = rep["in_place"]
            engines.append("2-line " + ("reflow" if ip.get("reflowed") else
                                        "in-place" if ip["count"] else
                                        ("refused" if out2 == out else "redraw")))
            out = out2
        off, frac = compare(out, twin)
        if "refused" in engines:
            verdict = "REFUSED"
        elif not off and frac < 0.0005:
            verdict = "IDENTICAL"
        elif not off:
            verdict = "CLOSE"
        else:
            verdict = "VISIBLE"
        tally[verdict] = tally.get(verdict, 0) + 1
        print(f"{gname:15} {key:8} {new:20} {'+'.join(engines):9} {len(off):>4} {frac * 100:7.3f}%  "
              f"{verdict}{'  e.g. ' + str(off[:3]) if off else ''}")
print("\nTOTAL:", tally)
