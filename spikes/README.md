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

Text is ALSO searched inside **Form XObjects**, not just the page's own
top-level content stream — many real PDFs (headless-browser/print-to-PDF
exports, many resume builders) draw the ENTIRE page as one embedded object,
so skipping this made every field on such a document fail identically. See
`_content_streams()` in inplace_spike.py.

### The "extend" tier — injecting a genuinely new glyph

A field's font is an embedded, SUBSETTED copy that only contains glyphs for
characters actually used *in that exact font/weight/style* — so typing a
character that's never appeared in, say, the Bold weight (even if it appears
elsewhere in Regular) genuinely isn't in that font file. `font_extend.py`
handles this for real: it downloads/instances a matching open-source donor
font (currently Source Sans 3 and IBM Plex Sans — the families this has been
tested against), copies the missing glyph's outline into the embedded subset,
extends the PDF's `/W` width array (both inline- and indirect-reference
forms), and patches the `/ToUnicode` CMap so the new character also
extracts/copies/searches correctly — not just renders correctly. Falls back to
an honest refusal for unknown families or anything that fails to resolve.

This was found the hard way: a first pass reported success while silently
corrupting text-extraction for the new character (visually correct, but
extracted as U+FFFD) — caught by re-verifying against a **fresh reopen** of
the edited bytes rather than the live in-memory session, which is now how
every check in this tier works. A second pass under-verified length-changing
edits against a bbox sized for the *old* text, flagging the field's own
legitimate extra width as a false violation — fixed by using the union of the
old and new field extents as the exclusion zone.

### Verified (spikes/test_inplace.py — 8 cases; spikes/test_font_extend.py — extend tier)

| Case | Result |
|---|---|
| One clean instruction (baseline) | pixel-perfect |
| **Split one-Tj-per-word** (the real-world break) | pixel-perfect, `case=multi_run` |
| Split one-Tj-per-**character** (extreme) | pixel-perfect, `case=multi_run` |
| Kerning split inside one instruction | honestly refused |
| Ragged boundary (unrelated text sharing a token) | honestly refused |
| Simple (non-CID) font, split across instructions | pixel-perfect |
| `analyze()` per-field editability probe | correct on all of the above |
| **New character, donor family known** (extend tier) | pixel-perfect, `tier=extend`, ToUnicode correct |
| New character, unknown/unresolvable family | honestly refused |
| Extend + text gets shorter/longer in the same edit | pixel-perfect (fair bbox) |

Run them: `backend/.venv/bin/python spikes/test_inplace.py` and
`backend/.venv/bin/python spikes/test_font_extend.py` (needs network — skips
cleanly if unavailable).

### Known, named limits (as of this version)

- **Only 2 donor font families known** (Source Sans 3 / Source Sans Pro, IBM
  Plex Sans) — any other family's missing-glyph case still refuses honestly
  rather than guess. Adding a family means one entry in
  `font_extend._DONOR_FONTS`.
- Only glyf-outline (TrueType) subset fonts are supported for the extend tier;
  CFF/OpenType-CFF embeds refuse cleanly.
- Only one level of Form XObject nesting is walked from the page.
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
