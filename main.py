"""
main.py — PDF generation module for SWAM invoices.

This module only knows how to DRAW a PDF invoice from a Python dict.
Read / batch-generation logic lives in `generate_bills.py`.

Usage:
    from main import InvoicePDF

    data = { ... invoice fields ... }
    InvoicePDF(data, "facture.pdf").build()

Fillable fields (dict keys expected by InvoicePDF):
    invoice_number      Numéro de facture       e.g.  W/2026/03/042
    invoice_date        Date de la facture      e.g.  31/03/2026
    client_name         Nom du client           e.g.  Wana Corporate
    client_address      Adresse du client       e.g.  Lottissement LA COLLINE 2 Sidi Maarouf Casablanca.
    client_ref          Référence client        e.g.  (laisser vide si non applicable)
    client_ice          ICE du client           e.g.  001957412000035
    description         Description prestation  e.g.  Run relatif au monitoring de la fraude transactionnelle du 01/03/2026 au 31/03/2026
    bon_commande        Référence bon commande  e.g.  Réf: Bon de commande N°4500044831 signé le 31/07/2023
    montant_ht          Montant HT (numérique)  e.g.  47673.99
                        → TVA 20% et Total TTC sont calculés automatiquement
                        → Arrêtée en lettres générée automatiquement
    banque              Nom de la banque        e.g.  ATTIJARIWAFABANK.
    agence              Nom de l'agence         e.g.  C.A. MANDARONA LOT. ATTAWFIQ SIDI MAAROUF
    compte              Numéro de compte        e.g.  007 780 0003409000001312 34
"""

import os
from decimal import Decimal, ROUND_HALF_UP

from num2words import num2words
from reportlab.lib import colors
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas as rl_canvas

# ── Paths (relative to this script) ──────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH  = os.path.join(SCRIPT_DIR, "image1.png")   # SWAM logo
STAMP_PATH = os.path.join(SCRIPT_DIR, "image2.png")   # company stamp

# ── Brand colours ─────────────────────────────────────────────────────────────
DARK_HEADER = colors.HexColor("#061320")   # near-black used in table headers
WHITE       = colors.white
BLACK       = colors.black

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_currency(value: float) -> str:
    """Format a float as  47 673,99"""
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    int_part, dec_part = str(d).split(".")
    # Thousands separator (space)
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


# ── PDF builder ───────────────────────────────────────────────────────────────

class InvoicePDF:
    PAGE_W = 595.32   # exact from original PDF
    PAGE_H = 841.92

    def __init__(self, data: dict, out_path: str):
        self.data = data
        self.out  = out_path
        self.ht      = float(data["montant_ht"])
        _tva_raw     = self.ht * 0.20
        self.tva     = round(_tva_raw, 2)
        self.ttc     = round(self.ht + _tva_raw, 2)

    def _ry(self, bot):
        return self.PAGE_H - bot

    def _ty(self, top, sz=10):
        return self.PAGE_H - top - sz * 0.75

    def _para(self, c, text, top, x, w, style):
        p = Paragraph(text, style)
        _, ph = p.wrapOn(c, w, 2000)
        p.drawOn(c, x, self.PAGE_H - top - ph)

    def build(self):
        H, W = self.PAGE_H, self.PAGE_W
        c = rl_canvas.Canvas(self.out, pagesize=(W, H))
        d = self.data
        inv_no   = str(d.get("invoice_number", ""))
        inv_date = str(d.get("invoice_date",   ""))
        cli_name = str(d.get("client_name",    ""))
        cli_addr = str(d.get("client_address", ""))
        cli_ref  = str(d.get("client_ref",     ""))
        cli_ice  = str(d.get("client_ice",     ""))
        desc     = str(d.get("description",    ""))
        bon_cmd  = str(d.get("bon_commande",   ""))

        TBL_L, TBL_R = 28.8, 556.7
        TBL_TOP, HDR_BOT = 264.2, 295.8
        TBL_W = TBL_R - TBL_L
        COL_SEP, TBL_BOT = 501.5, 430.0

        # 1. Logo
        if os.path.exists(LOGO_PATH):
            c.drawImage(LOGO_PATH, 45.2, self._ry(64.3), width=138.1, height=40.1, mask='auto')

        # 2. Yellow invoice box
        c.setFillColor(colors.HexColor("#FFFF00"))
        c.rect(331.8, self._ry(141.5), 224.8, 64.9, fill=1, stroke=0)
        c.setStrokeColor(BLACK); c.setLineWidth(0.5)
        c.rect(331.8, self._ry(141.5), 224.8, 64.9, fill=0, stroke=1)
        c.setFont("Helvetica", 10); c.setFillColor(BLACK)
        c.drawRightString(551.0, self._ty(91.8),  "Facture")
        c.drawRightString(551.0, self._ty(104.7), f"N\u00b0  {inv_no}")
        c.drawRightString(551.0, self._ty(117.7), f"Date :  {inv_date}")

        # 3. Client section
        s10 = ParagraphStyle("n", fontName="Helvetica", fontSize=10, leading=13)
        self._para(c, f"Nom Client : {cli_name}", 222.8, 30.7, 380.0, s10)
        c.drawString(416.7, self._ty(222.8), f"R\u00e9f\u00e9rence  Client :   {cli_ref}")
        self._para(c, f"Adresse     : {cli_addr}", 238.9, 30.7, 380.0, s10)
        c.drawString(416.7, self._ty(238.9), f"ICE N\u00b0 :  {cli_ice}")

        # 4. Invoice table
        c.setFillColor(DARK_HEADER)
        c.rect(TBL_L, self._ry(HDR_BOT), TBL_W, HDR_BOT - TBL_TOP, fill=1, stroke=0)
        c.setFont("Helvetica-Oblique", 10); c.setFillColor(WHITE)
        c.drawCentredString((TBL_L + COL_SEP) / 2,  self._ty(275.0), "Description")
        c.drawCentredString((COL_SEP + TBL_R) / 2,  self._ty(269.2), "Montant")
        c.drawCentredString((COL_SEP + TBL_R) / 2,  self._ty(281.7), "Total HT")
        c.setStrokeColor(colors.HexColor("#CCCCCC")); c.setLineWidth(0.5)
        c.rect(TBL_L, self._ry(TBL_BOT), TBL_W, TBL_BOT - TBL_TOP, fill=0, stroke=1)
        c.line(COL_SEP, self._ry(TBL_TOP), COL_SEP, self._ry(TBL_BOT))
        c.line(TBL_L, self._ry(HDR_BOT), TBL_R, self._ry(HDR_BOT))
        c.setFillColor(BLACK)
        s_d = ParagraphStyle("d", fontName="Helvetica", fontSize=10, leading=13)
        self._para(c, desc, 306.1, 47.6, COL_SEP - 51.6, s_d)
        amt_str    = fmt_currency(self.ht)
        col_avail  = TBL_R - 4 - COL_SEP - 2          # usable pt width of amount column
        amt_sz     = min(10, 10 * col_avail / max(c.stringWidth(amt_str, "Helvetica", 10), 1))
        c.setFont("Helvetica", amt_sz)
        c.drawRightString(TBL_R - 4, self._ty(306.1), amt_str)
        s_r = ParagraphStyle("r", fontName="Helvetica", fontSize=8,
                             textColor=colors.HexColor("#444444"), leading=11)
        self._para(c, bon_cmd, 360.3, 47.6, COL_SEP - 51.6, s_r)

        # 5. Totals  — label column adapts to value width so they never overlap
        c.setFont("Helvetica-Bold", 10); c.setFillColor(BLACK)
        val_right   = TBL_R - 4
        val_strs    = [fmt_currency(self.ht), fmt_currency(self.tva), fmt_currency(self.ttc)]
        max_val_w   = max(c.stringWidth(v, "Helvetica-Bold", 10) for v in val_strs)
        label_right = val_right - max_val_w - 8   # 8 pt gap between label and value
        c.drawRightString(label_right, self._ty(473.6), "Total HT")
        c.drawRightString(val_right,   self._ty(473.6), val_strs[0])
        c.drawRightString(label_right, self._ty(490.8), "TVA 20%")
        c.drawRightString(val_right,   self._ty(490.8), val_strs[1])
        c.drawRightString(label_right, self._ty(508.0), "Total T.T.C")
        c.drawRightString(val_right,   self._ty(508.0), val_strs[2])
        c.setStrokeColor(BLACK); c.setLineWidth(0.8)
        c.line(430.0, self._ry(521.0), TBL_R, self._ry(521.0))

        # 6. Amount in words
        c.setFont("Helvetica-Bold", 10); c.setFillColor(BLACK)
        c.drawString(47.6, self._ty(573.2),
                     "Arr\u00eat\u00e9e la pr\u00e9sente facture \u00e0 la somme de:")
        s_b = ParagraphStyle("b", fontName="Helvetica-Bold", fontSize=10, leading=13)
        self._para(c, amount_in_words_fr(self.ttc), 586.0, 47.6, 509.1, s_b)

        # 7. Bank info
        c.setFont("Helvetica", 10); c.setFillColor(BLACK)
        banque  = str(d.get("banque",  ""))
        agence  = str(d.get("agence",  ""))
        compte  = str(d.get("compte",  ""))
        c.drawString(30.7, self._ty(615.4), "Coordonn\u00e9es bancaires :")
        c.drawString(30.7, self._ty(628.1), f"Banque : {banque}")
        c.drawString(30.7, self._ty(640.2), f"Agence :{agence}")
        c.drawString(30.7, self._ty(653.0), f"Compte N\u00b0 {compte}")

        # 8. Stamp
        if os.path.exists(STAMP_PATH):
            c.drawImage(STAMP_PATH, 373.1, self._ry(737.3),
                        width=118.3, height=116.5, mask='auto')

        # 9. Footer
        c.setStrokeColor(colors.HexColor("#AAAAAA")); c.setLineWidth(0.5)
        c.line(TBL_L, self._ry(762.0), TBL_R, self._ry(762.0))
        c.setFont("Helvetica", 8); c.setFillColor(colors.HexColor("#333333"))
        c.drawCentredString(W/2, self._ty(771.1, 8),
            "Casablanca Nearshore Park Shore 1,1100 Boulevard Al Qods Sidi Maaouf -20270 Casablanca - Maroc")
        c.drawCentredString(W/2, self._ty(784.1, 8),
            "SA au capital de 65.000.000 dh RC : 343371 -TP : 36191679 -IF :18735615 - CNSS :4771342 - ICE 000113045000084")
        c.drawCentredString(W/2, self._ty(797.0, 8),
            "T\u00e9l : +212529 045 100 - Communication@swam.ma - http://Swam.ma")

        c.save()
        print(f"\u2714  Invoice written to: {self.out}")