import io
import zipfile
import tempfile
from pathlib import Path

import openpyxl
import pandas as pd
import streamlit as st

from generate_bills import _parse_amount, REQUIRED_COLS
from main import InvoicePDF


def load_invoices_from_xlsx(file) -> list[dict]:
    wb = openpyxl.load_workbook(file)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    missing = [c for c in REQUIRED_COLS if c not in headers]
    if missing:
        st.error(f"Colonnes manquantes dans le fichier : {missing}")
        st.stop()

    invoices = []
    for row in rows[1:]:
        data = dict(zip(headers, row))
        if not any(v for v in data.values()):
            continue
        invoices.append({
            "invoice_number": str(data.get("invoice_number") or ""),
            "invoice_date":   str(data.get("invoice_date")   or ""),
            "client_name":    str(data.get("client_name")    or ""),
            "client_address": str(data.get("client_address") or ""),
            "client_ref":     str(data.get("client_ref")     or ""),
            "client_ice":     str(data.get("client_ice")     or ""),
            "description":    str(data.get("description")    or ""),
            "bon_commande":   str(data.get("bon_commande")   or ""),
            "montant_ht":     _parse_amount(data.get("montant_ht")),
            "banque":         str(data.get("banque")         or ""),
            "agence":         str(data.get("agence")         or ""),
            "compte":         str(data.get("compte")         or ""),
        })
    return invoices


def build_zip(invoices: list[dict]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for inv in invoices:
            safe_no  = inv["invoice_number"].replace("/", "-").replace(" ", "_")
            pdf_path = tmp_path / f"facture_{safe_no}.pdf"
            InvoicePDF(inv, str(pdf_path)).build()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for pdf in sorted(tmp_path.glob("*.pdf")):
                zf.write(pdf, pdf.name)
        return buf.getvalue()


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SWAM – Générateur de Factures",
    page_icon="🧾",
    layout="centered",
)

st.title("🧾 Générateur de Factures SWAM")
st.write("Uploadez votre fichier Excel, vérifiez les données, puis téléchargez les PDFs.")
st.divider()

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Choisir le fichier Excel (.xlsx)", type=["xlsx"])

if uploaded:
    try:
        invoices = load_invoices_from_xlsx(uploaded)
    except Exception as e:
        st.error(f"Erreur lors de la lecture : {e}")
        st.stop()

    if not invoices:
        st.warning("Aucune facture trouvée dans le fichier.")
        st.stop()

    st.success(f"{len(invoices)} facture(s) détectée(s)")

    df = pd.DataFrame([{
        "N° Facture":  inv["invoice_number"],
        "Date":        inv["invoice_date"],
        "Client":      inv["client_name"],
        "Montant HT":  f"{inv['montant_ht']:,.2f}",
    } for inv in invoices])
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()

    if st.button("Générer les PDFs", type="primary", use_container_width=True):
        with st.spinner("Génération en cours..."):
            try:
                zip_bytes = build_zip(invoices)
            except Exception as e:
                st.error(f"Erreur lors de la génération : {e}")
                st.stop()

        st.success("PDFs générés avec succès !")
        st.download_button(
            label="Télécharger les PDFs (.zip)",
            data=zip_bytes,
            file_name="factures.zip",
            mime="application/zip",
            use_container_width=True,
        )
