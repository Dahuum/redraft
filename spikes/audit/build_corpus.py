"""Rebuild the audit corpus: generated locally + downloaded. Idempotent.

Nothing here is committed — PDFs stay out of the repo. Destination is
$RD_CORPUS (default ~/.cache/redraft-audit/corpus).

The hand-built documents at the bottom exist to reach structures no generator
produces: a rotation carried in the text matrix, Tc/Tw/Tz text state, one Tj
per character, a TJ array with kerning and no space glyphs, a nested Form
XObject, object streams and linearization.
"""
import glob
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import fitz  # noqa: E402

C = _common.CORPUS
SRC = os.path.join(os.path.dirname(C), "src")
for _d in (C, SRC):
    os.makedirs(_d, exist_ok=True)


def w(name, text):
    with open(os.path.join(SRC, name), "w", encoding="utf-8") as f:
        f.write(text)


# ── LibreOffice: four layouts from HTML/CSV ─────────────────────────────────
w("invoice.html", """<html><head><meta charset="utf-8"><style>
body{font-family:'Liberation Serif',serif;font-size:11pt}h1{font-family:'Liberation Sans',sans-serif}
td{padding:4px 10px;border-bottom:1px solid #999}</style></head><body>
<h1>INVOICE 2024-0188</h1><p>Billed to: <b>Nadia Benali</b><br>Company: Atlas Consulting SARL<br>
Date of issue: 14/03/2024<br>VAT number: MA9911223</p><table>
<tr><td>Architecture review</td><td>4</td><td>1,200.00</td><td>4,800.00</td></tr>
<tr><td>Implementation support</td><td>12</td><td>950.00</td><td>11,400.00</td></tr></table>
<p>Subtotal 17,400.00 &mdash; <b>Total due 20,880.00 MAD</b></p></body></html>""")
w("report.html", """<html><head><meta charset="utf-8"><style>
body{font-family:'Liberation Serif',serif;font-size:10.5pt;column-count:2;column-gap:28px}
h1{column-span:all}</style></head><body><h1>Quarterly Operations Review</h1>
<p>Prepared by <b>Hana Ouazzani</b> on 12 October 2024.</p>
<p>Throughput rose 14.2% against a target of 9.0%, driven by the Tangier line.</p>
<p>Headcount stood at 214 at quarter end, against a budget of 220.</p></body></html>""")
w("form.html", """<html><head><meta charset="utf-8"><style>
body{font-family:'DejaVu Serif',serif;font-size:11pt}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #444;padding:5px 8px}</style></head><body><h2>Registration Form</h2><table>
<tr><th>Field</th><th>Value</th></tr><tr><td>Family name</td><td>Benjelloun</td></tr>
<tr><td>Given name</td><td>Othmane</td></tr><tr><td>National ID</td><td>JB884512</td></tr>
<tr><td>Tuition</td><td>34,500.00 MAD</td></tr></table></body></html>""")
w("letter.html", """<html><head><meta charset="utf-8"><style>
body{font-family:'DejaVu Sans',sans-serif;font-size:12pt;line-height:1.6}</style></head><body>
<p>Casablanca, le 7 f&eacute;vrier 2025</p><p><b>Objet&nbsp;: Attestation d'emploi</b></p>
<p>Je soussign&eacute;, <b>Karim El Amrani</b>, certifie que Madame <b>Salma Bouzidi</b>,
n&eacute;e le 03/11/1994 &agrave; Marrakech, CIN <b>BK447120</b>, est employ&eacute;e depuis 2021.</p>
<p>Son salaire brut mensuel s'&eacute;l&egrave;ve &agrave; 18 500,00 dirhams.</p></body></html>""")
w("notice.html", """<html><head><meta charset="utf-8"><style>
body{font-family:'Liberation Serif',serif;font-size:12pt;line-height:1.5}
p{text-align:justify}</style></head><body>
<p>Par la présente, la direction certifie que Monsieur Youssef Amrani, titulaire de la carte
nationale BE447120, a suivi avec assiduité la totalité du programme de formation continue
organisé du 3 mars au 27 juin 2024, et qu'il a satisfait à l'ensemble des évaluations prévues
par le règlement pédagogique en vigueur.</p>
<p>Cette attestation lui est délivrée à sa demande pour servir et valoir ce que de droit.</p>
</body></html>""")
w("sales.csv", "Product,Region,Units,Revenue,Margin\nWidget A,North,1420,84200.00,31.5%\n"
               "Widget B,South,980,52100.00,27.2%\nWidget C,East,2310,131700.00,35.8%\n")

try:
    subprocess.run(["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", C,
                    "invoice.html", "report.html", "form.html", "letter.html", "notice.html",
                    "sales.csv"],
                   cwd=SRC, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=400)
except Exception as exc:  # noqa: BLE001
    print("  libreoffice unavailable:", type(exc).__name__)

# ── Ghostscript redistill at two settings ───────────────────────────────────
for setting, src_name, out_name in (("/prepress", "invoice.pdf", "invoice-gs.pdf"),
                                    ("/ebook", "letter.pdf", "letter-gs-ebook.pdf")):
    src = os.path.join(C, src_name)
    if os.path.exists(src):
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                        "-dPDFSETTINGS=" + setting,
                        "-sOutputFile=" + os.path.join(C, out_name), src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=200)

# ── PyMuPDF: base-14, an embedded TTF, a CID+CFF (OTF), leaders, scale, pages
LINES = [("Contract of Service", 18, "hebo"), ("", 0, None),
         ("Client name: Yassine Mouline", 11, "helv"),
         ("Reference number: CS-2024-7741", 11, "helv"),
         ("Effective date: 01/09/2024", 11, "helv"),
         ("Monthly fee: 7,250.00 EUR", 11, "helv"),
         ("Notice period: thirty (30) days", 11, "helv"),
         ("The parties agree the scope in Annex A may be amended.", 10, "helv")]


def simple_doc(path, fontfile=None, alias="emb"):
    d = fitz.open()
    p = d.new_page(width=595, height=842)
    if fontfile:
        p.insert_font(fontname=alias, fontfile=fontfile)
    y = 90
    for t, s, f in LINES:
        if t:
            p.insert_text((70, y), t, fontsize=s, fontname=(alias if fontfile else (f or "helv")))
            y += s + 10
        else:
            y += 12
    d.save(path)
    d.close()


simple_doc(os.path.join(C, "contract-base14.pdf"))
ttf = next((c for c in ("/usr/share/fonts/TTF/DejaVuSans.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if os.path.exists(c)),
           (glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) or [None])[0])
if ttf:
    simple_doc(os.path.join(C, "contract-embedded.pdf"), ttf)
otf = next((f for f in glob.glob("/usr/share/fonts/**/*.otf", recursive=True)
            if "C059-Roman" in f), None)
if otf:
    # an OTF embeds as Type0 with CFF outlines — the CIDFontType0 case
    d = fitz.open(); p = d.new_page(width=595, height=842)
    p.insert_font(fontname="c059", fontfile=otf)
    for i, t in enumerate(["Certificate of Completion", "Awarded to Sofia Marchetti",
                           "Programme: Advanced Typography", "Date of award: 18 June 2024",
                           "Registration number: CT-90412"]):
        p.insert_text((70, 120 + i * 28), t, fontsize=13, fontname="c059")
    d.save(os.path.join(C, "cidcff.pdf")); d.close()

d = fitz.open(); p = d.new_page(width=595, height=842); y = 120
for num, label, val in (("1", "Gross receipts or sales", "128,400.00"),
                        ("2", "Returns and allowances", "3,210.00"),
                        ("3", "Cost of goods sold", "61,905.00"),
                        ("4", "Gross profit", "63,285.00"),
                        ("5", "Other income from Part II", "4,120.00")):
    p.insert_text((60, y), num, fontsize=9, fontname="helv")
    p.insert_text((86, y), label, fontsize=9, fontname="helv")
    p.insert_text((330, y), "." * 44, fontsize=9, fontname="helv")
    p.insert_text((500, y), num, fontsize=9, fontname="hebo")
    p.insert_text((524, y), val, fontsize=9, fontname="helv")
    y += 26
d.save(os.path.join(C, "leaderform.pdf")); d.close()

d = fitz.open()
for n in range(1, 6):
    p = d.new_page(width=595, height=842)
    p.insert_text((60, 60), "ACME HOLDINGS - CONFIDENTIAL", fontsize=9, fontname="hebo")
    p.insert_text((60, 120), "Section %d: Operating covenants" % n, fontsize=15, fontname="hebo")
    for i in range(6):
        p.insert_text((60, 160 + i * 20),
                      "%d.%d  Amount payable is %s.00 EUR on the stated date." % (n, i + 1, format(1000 * n + i * 37, ",")),
                      fontsize=10, fontname="helv")
    p.insert_text((60, 800), "Page %d of 5" % n, fontsize=8, fontname="helv")
d.save(os.path.join(C, "multipage.pdf")); d.close()

# ── downloaded: real pipelines that cannot be reproduced locally ────────────
for name, url in (("arxiv-latex", "https://arxiv.org/pdf/1706.03762"),
                  ("arxiv-old", "https://arxiv.org/pdf/cs/0004003"),
                  ("wiki-chrome", "https://en.wikipedia.org/api/rest_v1/page/pdf/Python_(programming_language)"),
                  ("irs-w9", "https://www.irs.gov/pub/irs-pdf/fw9.pdf"),
                  ("irs-1040", "https://www.irs.gov/pub/irs-pdf/f1040.pdf"),
                  ("irs-w4", "https://www.irs.gov/pub/irs-pdf/fw4.pdf"),
                  ("nasa-tm", "https://ntrs.nasa.gov/api/citations/19930090934/downloads/19930090934.pdf")):
    dest = os.path.join(C, name + ".pdf")
    if os.path.exists(dest):
        continue
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "redraft-audit/1.0"})
        with urllib.request.urlopen(req, timeout=60) as fh:
            blob = fh.read()
        if blob.startswith(b"%PDF"):
            open(dest, "wb").write(blob)
    except Exception as exc:  # noqa: BLE001 — offline is fine, the rest still runs
        print("  download skipped:", name, type(exc).__name__)


# ── structures no generator produces ────────────────────────────────────────
def raw_pdf(path, content):
    """Minimal one-page PDF with Helvetica as /F1 and a literal content stream."""
    objs = ["<</Type/Catalog/Pages 2 0 R>>",
            "<</Type/Pages/Kids[3 0 R]/Count 1>>",
            "<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
            "/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
            None,
            "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"]
    body = b"%PDF-1.7\n"
    offsets = []
    data = content.encode("latin-1")
    for i, o in enumerate(objs, start=1):
        offsets.append(len(body))
        if o is None:
            body += ("%d 0 obj\n<</Length %d>>\nstream\n" % (i, len(data))).encode("latin-1")
            body += data + b"\nendstream\nendobj\n"
        else:
            body += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
    xref_at = len(body)
    body += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode("latin-1")
    for off in offsets:
        body += ("%010d 00000 n \n" % off).encode("latin-1")
    body += ("trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
             % (len(objs) + 1, xref_at)).encode("latin-1")
    open(path, "wb").write(body)


L = "Invoice number 2024-0455 for Nadia Benali"
raw_pdf(os.path.join(C, "tm-rotated.pdf"),
        "BT /F1 12 Tf 0.9063 0.4226 -0.4226 0.9063 90 300 Tm (Rotated: %s) Tj ET\n"
        "BT /F1 12 Tf 1 0 0 1 70 700 Tm (Upright control: %s) Tj ET\n" % (L, L))
raw_pdf(os.path.join(C, "text-state.pdf"),
        "BT /F1 12 Tf 2.5 Tc 70 740 Td (Char spaced: %s) Tj ET\n"
        "BT /F1 12 Tf 0 Tc 8 Tw 70 700 Td (Word spaced: %s) Tj ET\n"
        "BT /F1 12 Tf 0 Tw 60 Tz 70 660 Td (Condensed: %s) Tj ET\n"
        "BT /F1 12 Tf 140 Tz 70 620 Td (Expanded: %s) Tj ET\n"
        "BT /F1 12 Tf 100 Tz 70 580 Td (Plain control: %s) Tj ET\n" % (L, L, L, L, L))
parts, x = [], 70.0
for ch in "Split per character 8842":
    parts.append("BT /F1 12 Tf 1 0 0 1 %.1f 700 Tm (%s) Tj ET" % (x, ch))
    x += 7.2
raw_pdf(os.path.join(C, "per-char.pdf"),
        "\n".join(parts) + "\nBT /F1 12 Tf 1 0 0 1 70 660 Tm (Normal line %s) Tj ET\n" % L)
raw_pdf(os.path.join(C, "tj-kerned.pdf"),
        "BT /F1 12 Tf 70 700 Td [(Invoice)-320(number)-320(2024)-120(0455)-320(Nadia)-320(Benali)] TJ ET\n")

d = fitz.open(); p = d.new_page(width=595, height=842)
p.insert_text((70, 700), "Outer page text " + L, fontsize=11, fontname="helv")
inner = fitz.open(); ip = inner.new_page(width=300, height=100)
ip.insert_text((10, 50), "Inside an XObject " + L, fontsize=11, fontname="helv")
p.show_pdf_page(fitz.Rect(60, 400, 560, 560), inner, 0)
d.save(os.path.join(C, "nested-xobject.pdf")); d.close(); inner.close()

src = os.path.join(C, "invoice.pdf")
if os.path.exists(src):
    subprocess.run(["qpdf", "--object-streams=generate", src, os.path.join(C, "objstm.pdf")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["qpdf", "--linearize", src, os.path.join(C, "linearized.pdf")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("corpus: %d documents in %s" % (len(glob.glob(os.path.join(C, "*.pdf"))), C))
