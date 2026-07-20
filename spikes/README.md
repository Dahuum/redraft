# spikes/ — research prototypes (not production)

## inplace_spike.py — true in-place PDF text editing

The real engine behind `/spike/analyze` and `/spike/edit` (see `backend/api.py`)
and the `/lab` test page. Goal: edit PDF text with a real guarantee — change
ONLY the characters, leave font/size/position/weight/color byte-for-byte
identical — replacing today's editor's erase-and-restamp approach, which
re-derives all of that and can drift.

### Why the first version failed on real documents

The naive approach — "find this exact sequence of glyph codes sitting
together in one place" — breaks constantly, because real PDF generators
(Word, InDesign, Canva, precise-typesetting tools) very often DON'T draw a
field's text as one instruction. A field is frequently split across **several
separate drawing instructions** — one per word, sometimes one per letter —
each with its own exact position. This is exactly how custom letter-spacing /
"styled" text gets produced.

The engine now:
1. Parses the actual content-stream tokens with its own tokenizer.
2. Builds every text-showing run (`Tj`/`TJ`/`'`/`"`), broken into individual
   string tokens (so kerning numbers and unrelated tokens are never touched).
3. **Flattens character codes across ALL runs**, in document order, regardless
   of what operators sit between them — this is what finds text split across
   many small instructions.
4. Locates the target sequence and decides precisely how far it can safely
   reach:
   - fully inside **one string token** → splice just that token
   - exactly spans **one or more whole runs** → rewrite the first, blank the
     rest
   - anything messier (kerning splits a run internally; a match starts/ends
     partway through a run that also carries unrelated text) → **refuse, with
     a specific named reason** — never a silently-wrong splice.

### Verified (spikes/test_inplace.py — 8 adversarial cases, all pass)

| Case | Result |
|---|---|
| One clean instruction (baseline) | pixel-perfect |
| **Split one-Tj-per-word** (the real-world break) | pixel-perfect, `case=multi_run` |
| Split one-Tj-per-**character** (extreme) | pixel-perfect, `case=multi_run` |
| Kerning split inside one instruction | honestly refused |
| Ragged boundary (unrelated text sharing a token) | honestly refused |
| Simple (non-CID) font, split across instructions | pixel-perfect |
| New character not in the subset | honestly refused ("extend") |
| `analyze()` per-field editability probe | correct on all of the above |

Run it: `backend/.venv/bin/python spikes/test_inplace.py`

### Known, named limits (as of this version)

- **Form XObjects** (logos/stamps/repeated headers drawn as embedded objects)
  aren't searched — only the page's own top-level content stream.
- **Vector-outlined "text"** (converted to filled curves, no text operator at
  all) can't be found or edited as text by *any* tool — it no longer exists as
  text in the file. If a field looks editable but styling is clearly custom
  vector art, this is why.
- Duplicate occurrences of the exact same text on a page aren't disambiguated
  by position — the first match is used.
- Two different embedded copies of "the same" font (by name) could in theory
  assign different glyph-ids to the same character; glyph maps are keyed by
  font name, not by the specific embedded font object.

### Try it live

```bash
# backend (from repo root, using the local venv)
backend/.venv/bin/python -m uvicorn api:app --app-dir backend --host 127.0.0.1 --port 8000
# frontend
npm --prefix frontend run dev
```
Open **http://localhost:5173/lab** — drop a PDF, see every field marked
`ready` or `blocked` (with the specific reason) before you even try editing.

Generated PDFs/PNGs are git-ignored.
