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

### Three more real-document bugs (found by downloading actual PDFs, not just synthetic fixtures)

Testing against a real LaTeX/pdfTeX paper (a classic, extremely common real
document type) surfaced three separate, previously-invisible bugs — synthetic
test fixtures hadn't reproduced any of them because they didn't reproduce how
real typesetters actually write content streams:

1. **Ligatures / custom `/Encoding /Differences`.** LaTeX Type1 fonts commonly
   remap byte codes to arbitrary glyph names — most famously, "fi" and "fl"
   collapse into a SINGLE byte (a ligature glyph), not two ASCII bytes. The
   old simple-font path assumed `text.encode("latin-1")`, which is right for
   most letters but silently wrong for ligature-affected words — extremely
   common in ordinary English ("specifically", "office", "flow", ...). Fixed
   the same way CID fonts were already handled: read the font's OWN
   `/ToUnicode` CMap empirically (`_simple_font_code_maps()`) and greedy-match
   the longest known sequence first, so "fi" resolves to its real one-byte
   code. Falls back to the old blind latin-1 behavior whenever a font has no
   ToUnicode or the map doesn't fully cover the text — this can only ever
   improve on the prior behavior, never regress it.
2. **Word spaces drawn as pure positioning, no space glyph at all.** The
   textbook dvips/pdfTeX pattern `[(Google)-250(Brain)]TJ` has NO space
   character anywhere — the gap is just a TJ-array number. A search for
   "Google Brain" (with its ordinary space) could never match. Fixed by
   treating a sufficiently negative gap between two string tokens in one TJ
   array as an inferred space for matching purposes (`_SPACE_GAP_THRESHOLD`),
   while generalizing `_locate`/`_apply` to safely rewrite a match that spans
   several WHOLE string tokens within one instruction — safe specifically
   because every boundary crossed is verified to be a real inter-word gap
   (large and negative), not an arbitrary in-word kerning nudge (which stays
   small in magnitude on every real document inspected). A synthetic
   worst-case (`[<A> -40 <B>]TJ`, a genuine mid-word kerning split) still
   refuses correctly — the discriminator, not just "spans multiple tokens",
   is what makes each case safe or not.
3. **PDF literal-string octal escapes (`\ddd`).** A non-printable font byte
   (like the ligature byte above) gets written in a literal string as
   `\002` — four literal characters (backslash, '0', '0', '2') — not the
   single real byte 0x02. The tokenizer was storing that substring verbatim
   instead of decoding it, so it never matched a decoded search sequence at
   all. Fixed with `_decode_pdf_literal()`, a real PDF-string-escape decoder
   (octal escapes + the standard `\n\r\t\b\f\(\)\\` set + line continuations).

Locked in permanently against the real documents that found them, not just
synthetic fixtures, in `spikes/test_real_world_pdfs.py` (downloads a LaTeX
paper and a headless-Chrome-exported Wikipedia page fresh each run; skips
cleanly without network).

### The "extend" tier — injecting a genuinely new glyph

A field's font is an embedded, SUBSETTED copy that only contains glyphs for
characters actually used *in that exact font/weight/style* — so typing a
character that's never appeared in, say, the Bold weight (even if it appears
elsewhere in Regular) genuinely isn't in that font file. `font_extend.py`
handles this for real: it copies the missing glyph's outline into the
embedded subset from a real, open-source donor font, extends the PDF's `/W`
width array (both inline- and indirect-reference forms), and patches the
`/ToUnicode` CMap so the new character also extracts/copies/searches
correctly — not just renders correctly.

**The donor is resolved GENERICALLY against the whole Google Fonts catalog,
not a hardcoded per-family list.** An earlier version had a tiny dict of ~3
families; every other family (the vast majority of the ~1800 on Google Fonts)
hit "unknown font family" on first contact — the same failure, on a
different font, every time a user's PDF used something not yet in the list.
The fix: normalize the requested family into a candidate repo folder,
**list** that folder via the GitHub Contents API (so nothing about a family's
own naming quirks needs to be guessed — "PT_Sans-Web-Bold.ttf" for "PT
Sans", full per-weight static files for older families like Lato/Poppins/
Barlow, one variable file with 1–3 axes for modern ones), then parse each
returned filename's own weight/style suffix to pick the best match — an
exact variable-axis instance where available, else the nearest-weight static
file with the CORRECT style (a wrong slant is a visible defect, so style is a
hard requirement; only weight is allowed to be "nearest"). Falls back through
a small table of known Google-side renames (e.g. "Source Sans Pro" → "Source
Sans 3"), then the MAIN engine's existing `_FONT_SUBSTITUTES` table
(commercial fonts → open-source lookalikes, e.g. Arial → Arimo) — reusing
tables the product already trusts instead of inventing new ones. Listings
are cached (memory + a small on-disk index) since the GitHub API is
rate-limited unauthenticated. Only genuinely custom/commercial fonts with no
open-source relative reach the honest "unknown font family" refusal now.

This was found the hard way, twice, by the SAME tier: a first pass reported
success while silently corrupting text-extraction for the new character
(visually correct, but extracted as U+FFFD) — caught by re-verifying against
a **fresh reopen** of the edited bytes rather than the live in-memory
session, which is now how every check in this tier works. A second pass
under-verified length-changing edits against a bbox sized for the *old* text,
flagging the field's own legitimate extra width as a false violation — fixed
by using the union of the old and new field extents as the exclusion zone. A
third: CFF/PostScript-outline fonts (`/FontFile3`) were reported as an
"unknown font family" — the same generic message as a genuinely-unresolvable
one — even though the real cause (not a glyf-outline font, not supported yet)
was already known internally; `_font_stream_refs()` and `_try_extend()` now
thread a specific, named reason (`extend_reason`) through to the caller
instead of a single catch-all.

### Verified

`spikes/test_inplace.py` — 10 synthetic adversarial cases:

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
| **Word space as pure TJ positioning, no space glyph** | pixel-perfect, `case=multi_token` |
| **PDF octal-escaped literal-string byte (`\ddd`)** | pixel-perfect |

`spikes/test_font_extend_broad.py` — the FULL edit() pipeline (not just
resolve_donor) against 4 structurally different real families: Roboto
(variable, multi-axis), Lato (static, full per-weight file set), PT Sans
(static, unusual "PT_Sans-Web-" filename prefix — proves the resolver parses
by suffix, not by assuming the family name is the prefix), and Oswald
(variable, no italic file exists at all). All pixel-perfect, `tier=extend`,
ToUnicode correct. Also confirms a real CFF-outline font (`/FontFile3`)
reports `extend_reason="not_glyf"`, not a generic refusal.

`spikes/test_real_world_pdfs.py` — real, freshly-downloaded documents (see
above): a LaTeX/pdfTeX paper (67% of its 2610 text fields editable, up from
completely broken on this document class before this round of fixes) and a
headless-Chrome-exported Wikipedia page (98% of 2892 fields editable),
plus one real pixel-perfect edit against each.

Run them: `python3 spikes/test_inplace.py`, `spikes/test_font_extend.py`,
`spikes/test_font_extend_broad.py`, and `spikes/test_real_world_pdfs.py` (the
last three need network — skip cleanly if unavailable).

### Known, named limits (as of this version)

- Only glyf-outline (TrueType) subset fonts are supported for the extend tier;
  CFF/OpenType-CFF embeds refuse cleanly with a specific reason
  (`extend_reason="not_glyf"`), and donor files that are CFF-flavored are also
  skipped when picking a donor.
- A donor is only found if the family is somewhere on Google Fonts (under its
  own name, a known rename, or a substitute) — a genuinely custom/commercial
  font with no open-source relative still refuses honestly.
- Only one level of Form XObject nesting is walked from the page.
- The inferred-word-space fix (`_SPACE_GAP_THRESHOLD`) is scoped to simple
  (non-CID) fonts — that's the class of document it was found on. A CID font
  that also draws word spaces as pure TJ positioning isn't yet covered.
- The remaining un-editable fields on a real LaTeX document are mostly
  genuine mid-word kerning splits (justification adjusting spacing WITHIN a
  word, not between two words) — correctly, honestly refused rather than
  reconstructed, since dropping that adjustment would be a visible change.
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
