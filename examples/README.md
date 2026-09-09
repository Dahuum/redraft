# examples/attestation-demo.pdf

A real "Attestation de Scolarité" (école 1337 / UM6P), anonymized by running
it through Redraft's own `/edit` endpoint — same layout, same logos, same
fonts, same positions as the source document. Only five fields were
replaced, all with fabricated values: full name, birthdate, birthplace,
CIN/passport number, and the record reference number. Everything else is
untouched.

The source PDF is intentionally **not** included in this repo — it belongs
to a real person and was only used locally to produce this anonymized copy.

This is also the real-world case behind the fix in
[#13](https://github.com/Dahuum/redraft/pull/13): the source document's Bold
font is an embedded subset that never contained the letters `h`/`m` or the
digit `4` (its original text never used them), which the in-place editor
used to silently corrupt instead of catching. The five replacement values
above were deliberately chosen from characters the subset already has, so
this fixture edits clean with no font substitution — upload it and try
changing one of the bold fields to something containing `h`, `m`, or `4`
(e.g. a name like "Hicham") to see the fallback (and its "fonts replaced
with lookalikes" notice) kick in on purpose.

Reproduce the same kind of anonymized fixture from any similar document:

```bash
curl -s -X POST http://localhost:8000/extract -F "file=@source.pdf" \
  | python3 -m json.tool   # find the span `index` for each field to replace

curl -s -X POST http://localhost:8000/edit \
  -F "file=@source.pdf" \
  -F 'edits=[{"index":N,"new_text":"..."}, ...]' \
  -F "stamps=[]" -F "final=true" \
  -o anonymized.pdf
```
