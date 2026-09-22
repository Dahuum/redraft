# audit/ — measuring "can Redraft edit anything without problems?"

Not unit tests. These drive the **real product paths** over a corpus of real
and deliberately awkward PDFs, and judge the result by looking at the
**rendered output**, never at the engine's own report.

Everything here is rebuildable: no PDF is committed. `build_corpus.py`
generates what it can locally and downloads the rest.

```
python3 build_corpus.py                  # ~26 documents into $RD_CORPUS
PER_DOC=4 python3 audit_edit.py          # single-field, the main sweep
python3 audit_paths.py                   # /bulk, /annex, overlays, /compose
python3 audit_stress.py                  # adversarial text, successive edits, scale
```

`audit_paths.py` and the bulk half of `audit_stress.py` need the API running (`uvicorn api:app --port 8000` from
`backend/`). `RD_BACKEND` and `RD_API` override what is measured, which is how
a fix is A/B'd against the commit before it.

## The four bugs these caught

Each was invisible to every text-level check, because the extracted text was
perfect while the page was wrong.

1. **Notdef boxes on every Adobe document.** Coverage was read with `TTFont`,
   which raises on a bare CFF `/FontFile3`; the handler turned that into
   "cannot tell, do not block", so the missing-glyph check was skipped
   entirely and the viewer painted boxes. `/ToUnicode` had been updated, so
   the text read back perfectly.
2. **Restyling text the user never edited.** The redraw fallback re-stamped
   shifted neighbours through `fc-match`, which answers *every* weight of a
   family with the same Bold file, gated only by a basename substring test.
   Regular text came back bold and wider, and collided.
3. **A third font-program format.** pdfTeX embeds PostScript Type 1; nothing
   could read its coverage. Fixing that alone changed nothing, because the
   missing-glyph check only ran after the *fallback* encoder — the primary one
   was trusted on the grounds that `/ToUnicode` only names things the document
   renders. It does not: it is an extraction map.
4. **The annex inventing money.** `parse_num`/`fmt_num` were hard-wired to
   fr-MA, so an English annex read `"950.00"` as 95000, made a 12x line
   `1.140.000,00` instead of `11,400.00`, and recomputed the Total HT to
   agree. Internally consistent, looks finished, 100x wrong.

## Two rules, both learned the hard way

**"Will this draw?" has exactly one honest answer: `page.get_texttrace()`,
where glyph id 0 is `.notdef`.** Reading the text is blind — `/ToUnicode`
reports the right characters over a page of boxes. Reading the font's cmap is
the *wrong question* for a simple font, which renders by code through
`/Encoding /Differences` and need not have a cmap entry for an injected glyph;
that reported 10 false defects on a document that renders perfectly, and
crashed on a font that was neither SFNT nor CFF.

**Every check must be a DELTA against the same page unedited.** Ghostscript's
`/ebook` distill leaves a `0x1e` in the file before anything is touched; an
absolute notdef count called all 20 edits on it defects. Documents also
legitimately set text past their own media box.

## Run a new detector against a KNOWN-BAD build before believing a pass

Every audit here was worthless on first write — six times, and three of those
looked like clean passes:

| detector | how it was worthless |
|---|---|
| font coverage | `io.BytesIO` in a module that never imported `io`; the `NameError` was swallowed by its own `except`, so it reported 0 defects on an engine demonstrably painting boxes |
| batch edits | tested only *shortening* — the one shape that never fails |
| `/bulk` isolation | every value fit, so it passed on a build with a known leak |
| annex arithmetic | paired an alphabetically-sorted ZIP against input order, reporting all six as broken |
| successive edits | tracked the field by bbox; a centred field legitimately moves 58pt when its text shortens |
| `/compose` | compared raw characters against output that correctly uses `fi` ligatures |

The tell was always a **control** failing: plain text refusing, the unchanged
row breaking, a document that visibly works being flagged.

```bash
git worktree add --detach /tmp/wt <commit-before-the-fix>
RD_BACKEND=/tmp/wt/backend python3 audit_edit.py     # must FAIL here
python3 audit_edit.py                                # must pass here
```

For API-level audits, run a second uvicorn from the worktree on port 8001 and
point `RD_API` at it.

## Where editing stands

515 single-field edits over 26 documents from 13 pipelines, plus batches,
bulk, annex, overlays, adversarial text and scale: **0 defects, 0 crashes.**

Shortening never fails. Same-length is ~96%. The rest of the refusals are
dominated by one missing capability — text that no longer fits with nothing on
the line able to move, which **line wrapping** would convert. That remains
unbuilt and is a product decision, not a bug.
