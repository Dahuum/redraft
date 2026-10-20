"""test_webfonts_cff.py — /webfonts hands the browser CFF, Type 1 and standard-14 fonts too.

The editor draws text being typed in the document's own fonts. Browsers load TrueType and
OpenType only, so a bare CFF program (every IRS form), a Type 1 program (LaTeX's Computer Modern)
and a standard-14 font the PDF leaves out (MuPDF draws it with its own CFF) were "missing", and
every field in them was typed in the app's own font: all 898 fields of the W-9.

webfont_convert rebuilds them as OpenType-CFF. What is checked: they come back, they draw pixel for
pixel like the program they came from (same text, same size, rendered by MuPDF), they carry the
tables a browser's font sanitizer requires, and a TeX font gets a space of a third of an em
instead of the fallback face's.
"""
import base64
import io
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import fitz  # noqa: E402
import api  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

FAIL = []
CORPUS = os.path.expanduser("~/.cache/redraft-audit/corpus")
REQUIRED = {"CFF ", "OS/2", "cmap", "head", "hhea", "hmtx", "maxp", "name", "post"}


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def program(doc, base):
    for page in doc:
        for f in page.get_fonts(full=True):
            if f[3].split("+")[-1] == base:
                return doc.extract_font(f[0])[3]
    return None


def picture(font_buffer, text):
    d = fitz.open()
    p = d.new_page(width=900, height=60)
    tw = fitz.TextWriter(p.rect)
    tw.append((10, 40), text, font=fitz.Font(fontbuffer=font_buffer), fontsize=20)
    tw.write_text(p)
    return p.get_pixmap(matrix=fitz.Matrix(3, 3)).samples


def same_drawing(original, woff, text):
    """MuPDF draws *text* identically in the original program and in the served font."""
    otf = TTFont(io.BytesIO(woff))
    otf.flavor = None
    buf = io.BytesIO()
    otf.save(buf)
    have = otf.getBestCmap()
    text = "".join(c for c in text if ord(c) in have)
    return bool(text) and picture(original, text) == picture(buf.getvalue(), text), text


cases = [("../examples/latex-cm.pdf", "CMR10", "Type 1 (LaTeX)")]
if os.path.isdir(CORPUS):
    cases += [(os.path.join(CORPUS, "irs-w9.pdf"), "HelveticaNeueLTStd-Roman", "bare CFF (IRS W-9)"),
              (os.path.join(CORPUS, "irs-w9.pdf"), "HelveticaNeueLTStd-Bd", "bare CFF (IRS W-9)"),
              (os.path.join(CORPUS, "multipage.pdf"), "Helvetica", "standard 14, not embedded")]
else:
    print("SKIP - the audit corpus is not built: only the LaTeX example is checked")

for path, base, kind in cases:
    data = open(path, "rb").read()
    got = api._web_fonts_for(data, "", complete=False)
    served = {f["font"].split("+")[-1]: f for f in got["fonts"]}
    check(f"{base} ({kind}): served, not missing", base in served, got["missing"])
    if base not in served:
        continue
    woff = base64.b64decode(served[base]["woff"])
    tt = TTFont(io.BytesIO(woff))
    check(f"{base}: a WOFF with every table a browser requires", tt.flavor == "woff" and REQUIRED <= set(tt.keys()),
          REQUIRED - set(tt.keys()))
    with fitz.open(stream=data, filetype="pdf") as doc:
        original = program(doc, base) or fitz.Font(api._pe._base14_builtin(base)).buffer
        used = "".join(sorted({c for s in api.extract_spans(data) if s["font"] == base for c in s["text"]} - {" "}))
    ok, drawn = same_drawing(original, woff, used)
    check(f"{base}: draws pixel for pixel like the program it came from ({len(drawn)} characters)", ok, drawn[:40])
    if kind.startswith("Type 1"):
        cmap, hmtx, upm = tt.getBestCmap(), tt["hmtx"], tt["head"].unitsPerEm
        check(f"{base}: a space a third of an em wide (TeX fonts draw none)",
              32 in cmap and abs(hmtx[cmap[32]][0] - upm / 3) <= 1, cmap.get(32))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
