"""
generate_bills.py
-----------------
Reads every row of the invoice database  invoices_db.csv
(1 row = 1 invoice) and generates one PDF per row inside
    ./bill_<YYYY-MM-DD>/          (today's date).

The CSV is expected to be:
    • semicolon-separated   (;)
    • Latin-1 or UTF-8      (auto-detected)
    • headers on first line

Expected columns (in any order):
    invoice_number | invoice_date | client_name | client_address | client_ref |
    client_ice | description | bon_commande | montant_ht | banque | agence | compte

`montant_ht` accepts European format:   3.560,00    1 234,56    47673.99
TVA 20 %, Total TTC and the "Arrêtée … en lettres" line are computed
automatically by InvoicePDF — do NOT put them in the CSV.

Run:
    python3 generate_bills.py
"""

import csv
import sys
from datetime import date
from pathlib import Path

from main import InvoicePDF

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH    = SCRIPT_DIR / "invoices_db.csv"
DELIMITER  = ";"

REQUIRED_COLS = (
    "invoice_number", "invoice_date", "client_name", "client_address",
    "client_ref", "client_ice", "description", "bon_commande",
    "montant_ht", "banque", "agence", "compte",
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _parse_amount(raw) -> float:
    """Accept '3.560,00', '1 234,56', '47673.99', 47673.99 → float."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(" ", "").replace("\u00a0", "")
    if "," in s:                         # European: remove '.' thousands, swap ','→'.'
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _read_csv_rows(path: Path) -> list[dict]:
    """Open the CSV with Latin-1 / UTF-8 fallback and return list of dicts."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f, delimiter=DELIMITER)
                rows = [{(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                         for k, v in r.items()} for r in reader]
            return rows
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("generate_bills", b"", 0, 1,
                             f"Unable to decode {path.name} as utf-8 or latin-1")


def load_invoices(csv_path: Path) -> list[dict]:
    """Read the invoice DB and return one dict per non-empty row."""
    if not csv_path.exists():
        sys.exit(f"❌  File not found: {csv_path}")

    rows = _read_csv_rows(csv_path)
    if not rows:
        return []

    missing = [c for c in REQUIRED_COLS if c not in rows[0]]
    if missing:
        sys.exit(f"❌  Missing columns in {csv_path.name}: {missing}\n"
                 f"   Found columns: {list(rows[0].keys())}")

    invoices = []
    for i, r in enumerate(rows, start=2):   # row 1 = headers
        if not any(v for v in r.values()):
            continue
        try:
            invoices.append({
                "invoice_number":  r["invoice_number"],
                "invoice_date":    r["invoice_date"],
                "client_name":     r["client_name"],
                "client_address":  r["client_address"],
                "client_ref":      r["client_ref"],
                "client_ice":      r["client_ice"],
                "description":     r["description"],
                "bon_commande":    r["bon_commande"],
                "montant_ht":      _parse_amount(r["montant_ht"]),
                "banque":          r["banque"],
                "agence":          r["agence"],
                "compte":          r["compte"],
            })
        except Exception as e:
            sys.exit(f"❌  Error on CSV row {i}: {e}\n   Row data: {r}")
    return invoices


# ── Batch generation ──────────────────────────────────────────────────────────
def main() -> None:
    invoices = load_invoices(DB_PATH)
    if not invoices:
        print(f"⚠  Aucune facture trouvée dans {DB_PATH.name}")
        return

    out_dir = SCRIPT_DIR / f"bill_{date.today().isoformat()}"
    out_dir.mkdir(exist_ok=True)

    for inv in invoices:
        safe_no  = inv["invoice_number"].replace("/", "-").replace(" ", "_")
        pdf_path = out_dir / f"facture_{safe_no}.pdf"
        InvoicePDF(inv, str(pdf_path)).build()

    print(f"\n✔  {len(invoices)} facture(s) générée(s) dans {out_dir}/")


if __name__ == "__main__":
    main()
