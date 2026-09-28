"""test_ligature_codes.py — a missing letter after a ligature gets its own code.

examples/contract-type3.pdf is a fictional agreement printed by Chrome: every font is Type3 and
the body face draws "ff" as ONE glyph (a ligature). Editing "…takes effect from 2 April 2026
(the “Effective Date”)…" to "15 May 2026" needs an 'M' the subset does not have.

The codes for missing letters used to be taken by pairing the replacement's characters with the
encoder's output one-to-one. A ligature encodes two characters as one code, so every code after
"ff" was shifted by one: the 'M' was handed an existing glyph's code, the extension was rightly
refused (type3_no_code), and the whole edit fell back to a look-alike redraw.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


raw = open(os.path.join(HERE, "..", "examples", "contract-type3.pdf"), "rb").read()
fonts_before = sorted((f[2], f[0]) for f in fitz.open(stream=raw, filetype="pdf")[0].get_fonts(full=True))
sd = next(s for s in api.extract_spans(raw) if s["text"].startswith("This Agreement takes effect from"))
check("(fixture) the sentence contains a ligature before the edit point", "Eﬀective" in sd["text"] or "Effective" in sd["text"])
new = sd["text"].replace("2 April 2026", "15 May 2026")
out, rep = api.apply_replacements(raw, [(sd, new)], try_inplace=True)
if rep["in_place"]["count"] == 0 and any("no_donor" in str(r) for r in rep["in_place"].get("refusals", [])):
    print("SKIP - no donor reachable (offline?)")
else:
    check("a date needing a new capital, after a ligature, edits in place",
          rep["in_place"]["count"] == 1, str(rep["in_place"]))
    txt = fitz.open(stream=out, filetype="pdf")[0].get_text()
    check("the new date reads back", "15 May 2026" in txt, txt[:300])
    check("no font was added", sorted((f[2], f[0]) for f in fitz.open(stream=out, filetype="pdf")[0].get_fonts(full=True)) == fonts_before)

print("RESULT:", "ALL PASS" if not FAIL else "FAILURES: %s" % FAIL)
sys.exit(1 if FAIL else 0)
