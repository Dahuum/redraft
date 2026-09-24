"""test_structure.py — an edited file must not say, in its STRUCTURE, that
another program saved it.

Nothing here is visible on screen; all of it is visible to anyone who opens
the file's structure (qpdf --show-object, pdffonts, a hex editor):

  * CONTAINER. Acrobat, Ghostscript and pdfTeX write object and xref
    streams, Acrobat linearizes; every edit came back without them.
  * TRAILER ID. The first half must be the input's; its hex case must be
    the input's (Acrobat upper, MuPDF lower); none where there was none
    (Chrome writes no /ID — MuPDF added one).
  * RESOURCE NAMES. /fzFrm0, /fullpage, /LiberationSans in documents that
    name their own /F147, /T1_0, /R8.
  * FONT NAMES. "HelveticaNeueLTStd-Roman Regula" — MuPDF's name for a new
    wrapper round the document's own font — and random subset tags where
    Chrome counts AAAAAA, BAAAAA, ...

Needs the audit corpus (spikes/audit/build_corpus.py); skips what is absent.
"""
import os
import re
import shutil
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []
CORPUS = os.path.expanduser("~/.cache/redraft-audit/corpus")
_ID = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
_MUPDF = re.compile(r"^(fz\w+|fullpage)$|\s")


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def edit(name, longer=True):
    path = os.path.join(CORPUS, name)
    if not os.path.exists(path):
        return None, None, None
    data = open(path, "rb").read()
    spans = [s for s in api.extract_spans(data) if len(s["text"].strip()) >= 12]
    sd = spans[len(spans) // 2]
    new = sd["text"].strip() + (" Wxqzkj ploum" if longer else "")[:max(0, 13)]
    out, rep = api.apply_replacements(data, [(sd, new)], try_inplace=True)
    return data, out, sd


def res_names(pdf):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return ({f[4] for p in d for f in p.get_fonts(full=True)}
                | {x[1] for p in d for x in p.get_xobjects()},
                {f[3] for p in d for f in p.get_fonts(full=True)})
    finally:
        d.close()


ran = False
# ── Acrobat: linearized, object streams, upper-case /ID; redrawn field ──
data, out, sd = edit("irs-w4.pdf")
if data:
    ran = True
    check("Acrobat: object streams kept", (b"/ObjStm" in data) == (b"/ObjStm" in out))
    if shutil.which("qpdf"):
        check("Acrobat: linearization kept", b"/Linearized" in out[:4096])
    i, o = _ID.findall(data)[-1], _ID.findall(out)[-1]
    check("Acrobat: first /ID half kept", i[0].lower() == o[0].lower(), f"{i[0][:8]} {o[0][:8]}")
    check("Acrobat: /ID in the input's hex case", o[0] == o[0].upper() and o[1] == o[1].upper(),
          f"{o[0][:8]}")
    rn, fn = res_names(out)
    rn0, fn0 = res_names(data)
    check("Acrobat: no MuPDF resource names", not [n for n in rn - rn0 if _MUPDF.search(n)],
          str(sorted(rn - rn0)))
    check("Acrobat: added font names are PostScript names (no spaces)",
          not [n for n in fn - fn0 if " " in n], str(sorted(fn - fn0)))

# ── Chrome/Skia: no /ID, counter subset tags; redrawn field ──
data, out, sd = edit("wiki-chrome.pdf")
if data:
    ran = True
    check("Chrome: no /ID added where the input had none", not _ID.search(out))
    rn, fn = res_names(out)
    rn0, fn0 = res_names(data)
    check("Chrome: no MuPDF resource names", not [n for n in rn - rn0 if _MUPDF.search(n)],
          str(sorted(rn - rn0)))
    new_tags = sorted(n[:6] for n in fn - fn0 if re.match(r"^[A-Z]{6}\+", n))
    old_max = max(sum((ord(c) - 65) * 26 ** k for k, c in enumerate(n[:6]))
                  for n in fn0 if re.match(r"^[A-Z]{6}\+", n))
    check("Chrome: an added font continues the producer's tag counter",
          all(sum((ord(c) - 65) * 26 ** k for k, c in enumerate(t)) == old_max + 1 + j
              for j, t in enumerate(new_tags)), str(new_tags))

# ── pdfTeX 2023: object + xref streams, lower-case /ID; in place ──
data, out, sd = edit("arxiv-latex.pdf")
if data:
    ran = True
    check("pdfTeX: object streams kept", (b"/ObjStm" in data) == (b"/ObjStm" in out))
    i, o = _ID.findall(data)[-1], _ID.findall(out)[-1]
    check("pdfTeX: /ID in the input's hex case", o[0] == o[0].lower(), f"{o[0][:8]}")

if not ran:
    print("SKIP - audit corpus not built")

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
