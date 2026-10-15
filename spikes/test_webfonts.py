"""test_webfonts.py — POST /webfonts gives the editor the document's own fonts, complete enough to
draw what a user types.

Text typed into the editor is drawn on the page before the exact render returns. It was drawn in
the app's UI font, so every edit looked like the font had not been recognised. /webfonts returns
each font as WOFF; an embedded subset is completed with the common Latin letters it lacks, from
the same donors the edit engines use. Calibration: a font that exists nowhere is reported missing.
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
from fastapi.testclient import TestClient  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

api.AUTH_ON = False
c = TestClient(api.app)
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def web(data):
    r = c.post("/webfonts", files={"file": ("x.pdf", data, "application/pdf")})
    return r.status_code, (r.json() if r.status_code == 200 else {})


att = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
s, j = web(att)
check("attestation: answered for a guest", s == 200, s)
fonts = {f["font"].split("+")[-1]: f for f in j.get("fonts", [])}
check("attestation: both Tw Cen MT faces are returned", set(fonts) == {"TwCenMT-Bold", "TwCenMT-Regular"}, list(fonts))
for name, f in fonts.items():
    tt = TTFont(io.BytesIO(base64.b64decode(f["woff"])))
    cmap = tt.getBestCmap()
    check(f"{name}: is a WOFF the browser can load", tt.flavor == "woff")
    check(f"{name}: letters the PDF never drew are in it (Y m z B q w)",
          all(ord(ch) in cmap for ch in "YmzBqw"), [ch for ch in "YmzBqw" if ord(ch) not in cmap])
    check(f"{name}: small", len(f["woff"]) * 3 // 4 < 120_000, len(f["woff"]))
check("attestation: families are distinct", len({f["family"] for f in fonts.values()}) == 2)

raw = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj "
       b"4 0 obj<</Type/Font/Subtype/TrueType/BaseFont/ZzqNoSuchFontAnywhere/FirstChar 32/LastChar 126"
       b"/Encoding/WinAnsiEncoding>>endobj 5 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td (Hello world) Tj ET"
       b"\nendstream endobj trailer<</Root 1 0 R>>")
s, j = web(fitz.open(stream=raw, filetype="pdf").tobytes())
check("calibration: a font that exists nowhere is reported missing, not invented",
      s == 200 and not j.get("fonts") and any("ZzqNoSuch" in m for m in j.get("missing", [])), j)

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
