# GFP – Générateur de Factures PDF

Génère automatiquement des factures PDF au format SWAM à partir de données saisies manuellement ou lues depuis un fichier Excel.

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `main.py` | Module de **génération PDF** uniquement – classe `InvoicePDF` |
| `generate_bills.py` | Lit la base CSV, **boucle sur chaque ligne** et crée un PDF par facture |
| `invoices_db.csv` | **Base de données** : 1 ligne = 1 facture (séparateur `;`) |
| `Fature_RUN Fraude_Inwi_Mars2026.xlsx` | Template SWAM d'origine (référence) |
| `image1.png` | Logo SWAM (en-tête) |
| `image2.png` | Tampon / cachet société (bas de page) |

---

## Utilisation

### 1. Ajouter des factures à `invoices_db.csv`

Ouvrez `invoices_db.csv` (Excel, LibreOffice, éditeur de texte…) et ajoutez **une ligne par facture**. Format :

- séparateur : `;`
- encodage : UTF-8 ou Latin-1 (auto-détecté)
- colonnes obligatoires :

```
invoice_number;invoice_date;client_name;client_address;client_ref;client_ice;description;bon_commande;montant_ht;banque;agence;compte
```

> `montant_ht` accepte les formats européens (`3.560,00`, `1 234,56`) **et** le point décimal (`47673.99`).
> La TVA 20 %, le Total TTC et la somme en lettres sont calculés automatiquement.

### 2. Lancer la génération par lot

```bash
python3 generate_bills.py
```

Crée un dossier `./bill_YYYY-MM-DD/` (date du jour) contenant **un PDF par ligne** du CSV (nommé `facture_<invoice_number>.pdf`).

### 3. Utiliser `InvoicePDF` directement depuis un script Python

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
| Total TTC | `montant_ht + TVA` | **✓ auto** |
| Arrêtée en lettres | Conversion numérique → mots (français) | **✓ auto** |
| `banque` | Nom de la banque | ✗ |
| `agence` | Nom de l'agence | ✗ |
| `compte` | Numéro de compte bancaire | ✗ |

---

## Structure de la base CSV `invoices_db.csv`

| Colonne | Description | Exemple |
|---|---|---|
| `invoice_number` | Numéro de facture | `W/2026/04/001` |
| `invoice_date` | Date de la facture | `30/04/2026` |
| `client_name` | Nom du client | `Wana Corporate` |
| `client_address` | Adresse du client | `Lottissement LA COLLINE 2 …` |
| `client_ref` | Référence client (optionnel) | *(vide)* |
| `client_ice` | ICE du client | `001957412000035` |
| `description` | Description de la prestation | `Run relatif au monitoring …` |
| `bon_commande` | Référence du bon de commande | `Réf: Bon de commande N°4500044831 …` |
| `montant_ht` | Montant HT (numérique) | `47673.99` |
| `banque` | Nom de la banque | `ATTIJARIWAFABANK.` |
| `agence` | Nom de l'agence | `C.A. MANDARONA LOT. …` |
| `compte` | Numéro de compte | `007 780 0003409000001312 34` |

---

## Dépendances

```bash
pip install reportlab num2words
```
