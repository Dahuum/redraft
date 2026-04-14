# GFP – Générateur de Factures PDF

Génère automatiquement des factures PDF au format SWAM à partir de données saisies manuellement ou lues depuis un fichier Excel.

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `main.py` | Module principal – toute la logique de génération |
| `image1.png` | Logo SWAM (en-tête) |
| `image2.png` | Tampon / cachet société (bas de page) |

---

## Utilisation

### 1. Depuis la ligne de commande (données démo intégrées)

```bash
python3 main.py --out facture.pdf
```

### 2. Depuis la ligne de commande avec un fichier Excel

```bash
python3 main.py --db database.xlsx --row 0 --out facture.pdf
```

> `--row` = index de la ligne à utiliser (0 = première ligne de données).

### 3. Depuis un script Python

```python
from main import InvoicePDF

data = {
    "invoice_number": "W/2026/04/001",
    "invoice_date":   "30/04/2026",
    "client_name":    "Wana Corporate",
    "client_address": "Lottissement LA COLLINE 2 Sidi Maarouf Casablanca.",
    "client_ref":     "",                          # laisser vide si non applicable
    "client_ice":     "001957412000035",
    "description":    "Run relatif au monitoring de la fraude transactionnelle du 01/04/2026 au 30/04/2026",
    "bon_commande":   "Réf: Bon de commande N°4500044831 signé le 31/07/2023",
    "montant_ht":     47673.99,                    # numérique – TVA et TTC calculés automatiquement
    "banque":         "ATTIJARIWAFABANK.",
    "agence":         "C.A. MANDARONA LOT. ATTAWFIQ SIDI MAAROUF",
    "compte":         "007 780 0003409000001312 34",
}

InvoicePDF(data, "facture.pdf").build()
```

---

## Champs saisissables

| Clé | Description | Auto ? |
|---|---|---|
| `invoice_number` | Numéro de facture | ✗ |
| `invoice_date` | Date de la facture | ✗ |
| `client_name` | Nom du client | ✗ |
| `client_address` | Adresse du client | ✗ |
| `client_ref` | Référence client (optionnel) | ✗ |
| `client_ice` | ICE du client | ✗ |
| `description` | Description de la prestation | ✗ |
| `bon_commande` | Référence bon de commande | ✗ |
| `montant_ht` | Montant HT (float) | ✗ |
| TVA 20 % | `montant_ht × 0.20` | **✓ auto** |
| Total TTC | `montant_ht + TVA + 0.01` | **✓ auto** |
| Arrêtée en lettres | Conversion numérique → mots (français) | **✓ auto** |
| `banque` | Nom de la banque | ✗ |
| `agence` | Nom de l'agence | ✗ |
| `compte` | Numéro de compte bancaire | ✗ |

---

## Colonnes Excel attendues (si --db)

Le fichier Excel doit contenir une ligne d'en-tête avec exactement ces noms de colonnes :

```
invoice_number | invoice_date | client_name | client_address | client_ref |
client_ice | description | bon_commande | montant_ht | banque | agence | compte
```

---

## Dépendances

```bash
pip install reportlab pandas openpyxl num2words
```
