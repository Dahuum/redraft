# Redraft Compose API

Turn plain text into a clean, professionally-typeset, **editable & signable PDF** —
in one HTTP call. No template, no design work. Built for contracts, letters,
agreements, and any formal document.

> **Status:** the endpoint currently requires a signed-in Redraft session.
> The public (no-login) version + embeddable button described below is the next
> step — see "Making it public" at the bottom.

---

## Endpoint

```
POST https://dahuum-radraft.hf.space/compose
Content-Type: multipart/form-data
```

### Parameters

| Field   | Required | Description                                                        |
|---------|----------|--------------------------------------------------------------------|
| `text`  | ✅       | The document body. Blank lines separate paragraphs.                |
| `title` | optional | Document title (centered, bold, over a rule).                      |
| `meta`  | optional | Overrides the reference line. Defaults to `Référence : ___ · Date : <today>`. |

### How the text is formatted (automatic)

- **Paragraphs** — separated by a blank line, justified.
- **Headings** — a short line that is `Article …`, `Section …`, `Clause …`,
  numbered (`1.`), or ALL-CAPS becomes a **bold heading**.
- **Signature block** — two signature lines are added at the end.
- **Pagination** — long documents flow onto multiple A4 pages automatically.

### Returns

A `application/pdf` file — clean, A4, ready to open in the Redraft editor to
fill, edit, and sign.

---

## Examples

### curl

```bash
curl -X POST https://dahuum-radraft.hf.space/compose \
  -F "title=CONTRAT DE PRESTATION DE SERVICE" \
  -F $'text=Entre les soussignés :\n\nLa société ACME SARL, ci-après « le Prestataire ».\n\nArticle 1 — Objet\n\nRéalisation d\'une plateforme de facturation.\n\nArticle 2 — Rémunération\n\n120 000,00 MAD hors taxes.' \
  -o contract.pdf
```

### JavaScript (a button on any website)

```html
<button id="make-contract">Create contract</button>

<script>
document.getElementById("make-contract").onclick = async () => {
  const fd = new FormData();
  fd.append("title", "Service Agreement");
  fd.append("text", document.getElementById("contract-text").value);

  const res = await fetch("https://dahuum-radraft.hf.space/compose", {
    method: "POST",
    body: fd,
  });
  const blob = await res.blob();

  // download it…
  const url = URL.createObjectURL(blob);
  window.open(url); // or a download link
};
</script>
```

### Python

```python
import requests
r = requests.post(
    "https://dahuum-radraft.hf.space/compose",
    data={"title": "Service Agreement", "text": open("contract.txt").read()},
)
open("contract.pdf", "wb").write(r.content)
```

---

## Making it public (the shareable button)

To let **anyone** use this — no account — the plan is:

1. **Drop the login requirement on `/compose`** (it's a cheap generate call).
2. **Rate-limit by IP** (e.g. 20 documents/hour) to prevent abuse.
3. **Lock CORS** so only approved sites can call it from the browser, *or* leave
   it open for a public demo.
4. Ship a hosted page `redraft.dev/new` — paste title + text → **Create** →
   the finished PDF, ready to edit and sign.
5. Provide an **embed snippet** (the button above) integrators paste into their site.

Later (Level 2): the composed PDF opens in a **guest editor** so recipients can
sign without an account — the full "generate → edit → sign, embedded anywhere"
flow.
