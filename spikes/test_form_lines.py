"""test_form_lines.py — a longer value on a hand-broken form line is not reported as a paragraph fault.

The attestation's "M /Mme <name> ," / "Né le ... à <city>" / "CIN/Passeport ..." are three lines
broken by hand. Changing "Sara Idrissi" to "Abdurrahman Chahrour" was reported "overrun" (the
editor drew an orange warning on the name), because the three lines were taken as one paragraph
whose longest line was its margin. A real wrapped paragraph's warnings are covered by
test_paragraphs.py; this is the case that must stay quiet.
"""
import base64
import json
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
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


pdf = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
spans = c.post("/extract", files={"file": ("a.pdf", pdf, "application/pdf")}).json()["spans"]
by = {s["text"].strip(): s for s in spans}
for old, new in [("Sara Idrissi", "Abdurrahman Chahrour"), ("Essaouira", "Casablanca-Settat")]:
    r = c.post("/edit", files={"file": ("a.pdf", pdf, "application/pdf")},
               data={"edits": json.dumps([{"index": by[old]["id"], "new_text": new}])})
    rep = json.loads(base64.b64decode(r.headers["x-redraft-font-report"]))
    txt = fitz.open(stream=r.content, filetype="pdf")[0].get_text()
    check(f"{old} -> {new}: edited", new in txt)
    check(f"{old} -> {new}: not reported as a paragraph fault",
          not rep["in_place"].get("not_rewrapped"), rep["in_place"].get("not_rewrapped"))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
