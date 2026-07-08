# Redraft

Edit, generate, and sign PDFs — exactly how you want them.

Redraft is a document tool for people who work with PDFs at scale:

- **PDF Editor** — click any text or number and change it in place; add free text in
  the document's own font; draw or type a signature and place it anywhere.
- **Bulk Generator** — pick the fields that vary, map them to a spreadsheet, and
  generate hundreds of documents (a ZIP, or one merged PDF), named per client.
- **Annex Automation** — scan a billing annex once, then remove lines and recompute
  totals from a CSV, across pages, for any client.
- **Start from text** — paste a contract, letter, or agreement → get a clean,
  professionally-typeset, editable & signable PDF (see the **Compose API** below).
- **Cloud templates & signatures** — save a template + its setup to your account and
  reuse it on any device.

**Live:** [redraft.dev](https://redraft.dev)

---

## Architecture

| Piece      | Stack                              | Hosting                          |
|------------|------------------------------------|----------------------------------|
| Frontend   | Vite + React (MPA), Tailwind       | Vercel — `redraft.dev`           |
| Backend    | FastAPI + PyMuPDF (stateless)      | Hugging Face Space (Docker)      |
| Auth + DB  | Supabase (Postgres, RLS, Storage)  | Supabase                         |

- **Stateless backend** — files are processed in memory and never stored server-side.
- **Auth is opt-in** — the API enforces a Supabase JWT when `SUPABASE_*` env vars are
  set; without them it runs open (local dev). Per-user monthly limits are metered in
  the `profiles` table.

### Run locally

```bash
# backend  →  http://localhost:8000
uvicorn api:app --app-dir backend --host 0.0.0.0 --port 8000 --reload

# frontend →  http://localhost:5173   (app at /app.html)
npm --prefix frontend install
npm --prefix frontend run dev
```

The frontend reads `VITE_API_BASE` (defaults to `http://localhost:8000`) and the
`VITE_SUPABASE_*` keys from `frontend/.env.local`.

### Branches

- **`main`** — what's deployed to `redraft.dev`.
- **`redraft-dev`** — where work happens; open a PR to `main` when it's solid.

---

## Compose API — text → clean editable PDF

Turn plain text into a professionally-typeset, **editable & signable** PDF in one
HTTP call. No template, no design work. Built for contracts, letters, and agreements.

> **Status:** the endpoint currently requires a signed-in Redraft session. A public
> (no-login) version + an embeddable button is the planned next step — see
> "Making it a public button" below.

### Endpoint

```
POST https://dahuum-radraft.hf.space/compose
Content-Type: multipart/form-data
```

| Field   | Required | Description                                                        |
|---------|----------|--------------------------------------------------------------------|
| `text`  | ✅       | The document body. Blank lines separate paragraphs.                |
| `title` | optional | Document title (centered, bold, over a rule).                      |
| `meta`  | optional | Overrides the reference line. Defaults to `Référence : ___ · Date : <today>`. |

**Automatic formatting:** blank lines → justified paragraphs; a short line that is
`Article …` / `Section …` / `Clause …` / numbered / ALL-CAPS → a **bold heading**;
a signature block is appended; long documents paginate onto multiple A4 pages.

**Returns:** an `application/pdf` file — Classic Legal styling, ready to edit and sign.

### Examples

**curl**

```bash
curl -X POST https://dahuum-radraft.hf.space/compose \
  -F "title=CONTRAT DE PRESTATION DE SERVICE" \
  -F $'text=Entre les soussignés :\n\nLa société ACME SARL, ci-après « le Prestataire ».\n\nArticle 1 — Objet\n\nRéalisation d\'une plateforme de facturation.\n\nArticle 2 — Rémunération\n\n120 000,00 MAD hors taxes.' \
  -o contract.pdf
```

**JavaScript — a button on any website**

```html
<button id="make-contract">Create contract</button>
<script>
document.getElementById("make-contract").onclick = async () => {
  const fd = new FormData();
  fd.append("title", "Service Agreement");
  fd.append("text", document.getElementById("contract-text").value);
  const res = await fetch("https://dahuum-radraft.hf.space/compose", { method: "POST", body: fd });
  const blob = await res.blob();
  window.open(URL.createObjectURL(blob)); // preview / download the PDF
};
</script>
```

**Python**

```python
import requests
r = requests.post(
    "https://dahuum-radraft.hf.space/compose",
    data={"title": "Service Agreement", "text": open("contract.txt").read()},
)
open("contract.pdf", "wb").write(r.content)
```

### Making it a public button

To let **anyone** use this with no account:

1. Drop the login requirement on `/compose` (it's a cheap generate call).
2. Rate-limit by IP (e.g. 20 documents/hour) to prevent abuse.
3. Ship a hosted page `redraft.dev/new` — paste title + text → **Create** → the PDF.
4. Provide the embed snippet above for integrators.

Later: open the composed PDF in a **guest editor** so recipients can sign without an
account — the full "generate → edit → sign, embedded anywhere" flow.

---

## Legacy billing scripts

The repository also contains the original Streamlit invoice generator
(`main.py`, `generate_bills.py`, `reconstruct.py`, `invoices_db.csv`). These are kept
for reference and are independent of the `backend/` + `frontend/` app.
