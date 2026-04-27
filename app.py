import io
import zipfile
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from generate_bills import _parse_amount, _read_csv_rows, REQUIRED_COLS
from main import InvoicePDF


def load_invoices_from_csv(file) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(file.read())
        tmp_path = Path(tmp.name)

    try:
        rows = _read_csv_rows(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not rows:
        return []

    missing = [c for c in REQUIRED_COLS if c not in rows[0]]
    if missing:
        st.error(f"Colonnes manquantes dans le fichier : {missing}")
        st.stop()

    invoices = []
    for r in rows:
        if not any(v for v in r.values()):
            continue
        invoices.append({
            "invoice_number": r.get("invoice_number", ""),
            "invoice_date":   r.get("invoice_date",   ""),
            "client_name":    r.get("client_name",    ""),
            "client_address": r.get("client_address", ""),
            "client_ref":     r.get("client_ref",     ""),
            "client_ice":     r.get("client_ice",     ""),
            "description":    r.get("description",    ""),
            "bon_commande":   r.get("bon_commande",   ""),
            "montant_ht":     _parse_amount(r.get("montant_ht")),
            "banque":         r.get("banque",         ""),
            "agence":         r.get("agence",         ""),
            "compte":         r.get("compte",         ""),
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
st.write("Uploadez votre fichier CSV, vérifiez les données, puis téléchargez les PDFs.")
st.divider()

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Choisir le fichier CSV (.csv)", type=["csv"])

if uploaded:
    try:
        invoices = load_invoices_from_csv(uploaded)
    except Exception as e:
        st.error(f"Erreur lors de la lecture : {e}")
        st.stop()

    if not invoices:
        st.warning("Aucune facture trouvée dans le fichier.")
        st.stop()

    st.success(f"{len(invoices)} facture(s) détectée(s)")

    df = pd.DataFrame([{
        "N° Facture": inv["invoice_number"],
        "Date":       inv["invoice_date"],
        "Client":     inv["client_name"],
        "Montant HT": f"{inv['montant_ht']:,.2f}",
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
