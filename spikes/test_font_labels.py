"""test_font_labels.py — the font panel says what the edit will actually look like.

POST /fonts labels each font builtin | match | embedded | substitute | fallback, and the panel asks
the user to upload the real file for substitute/fallback. Fonts the engines edit in the document's
own font (an adopted embedded font, an embedded subset the engines extend, a Type3 font with a
family donor) were reported "fallback · missing", so the panel told users their font was not
recognised on documents that edited perfectly. A font that really is nowhere must still be
"fallback": that case is the calibration.
"""
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

api.AUTH_ON = False
c = TestClient(api.app)
EX = os.path.join(HERE, "..", "examples")
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def labels(data):
    r = c.post("/fonts", files={"file": ("x.pdf", data, "application/pdf")})
    return {f["font"]: f["status"] for f in r.json().get("fonts", [])} if r.status_code == 200 else {"HTTP": r.status_code}


def ex(name):
    return open(os.path.join(EX, name), "rb").read()


# calibration: a font the PDF does not carry and no source has
raw = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj "
       b"4 0 obj<</Type/Font/Subtype/TrueType/BaseFont/ZzqNoSuchFontAnywhere/FirstChar 32/LastChar 126"
       b"/Encoding/WinAnsiEncoding>>endobj 5 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td (Hello world) Tj ET"
       b"\nendstream endobj trailer<</Root 1 0 R>>")
missing = labels(fitz.open(stream=raw, filetype="pdf").tobytes())
check("calibration: a font that is nowhere is still flagged", missing.get("ZzqNoSuchFontAnywhere") == "fallback", missing)

att = labels(ex("attestation-demo.pdf"))
check("attestation: its embedded Tw Cen MT is a match, not missing",
      att and all(v == "match" for v in att.values()), att)

cm = labels(ex("latex-cm.pdf"))
check("LaTeX: the embedded Computer Modern subset is the document's own font", cm.get("CMR10") == "embedded", cm)

inv = labels(ex("invoice-type3.pdf"))
check("Type3 invoice: fonts with a family donor are the document's own", inv and all(v == "embedded" for v in inv.values()), inv)

con = labels(ex("contract-type3.pdf"))
# With its full name recovered (test_font_names.py) the genuine family is found: "match".
check("contract: the signature font is found despite its name being cut short",
      con.get("HerrVonMuellerhoff-Regul") in ("match", "embedded"), con.get("HerrVonMuellerhoff-Regul"))
check("contract: the two one-glyph Type3 fonts an edit really draws in Helvetica stay flagged",
      con.get("Type3 (5 0 R)") == "fallback" and con.get("Type3 (17 0 R)") == "fallback",
      (con.get("Type3 (5 0 R)"), con.get("Type3 (17 0 R)")))

ann = labels(ex("annex-cell.pdf"))
check("annex: fonts found on Google Fonts are still a match", ann and all(v == "match" for v in ann.values()), ann)

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
