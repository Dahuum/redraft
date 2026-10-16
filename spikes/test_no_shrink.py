"""test_no_shrink.py — an edit is never drawn smaller than the document set it (product decision 2026-10).

A value that does not fit was redrawn down to 70% of its size: always placed, visibly smaller than
everything around it. Now it keeps its own size where there is room, and where there is not — off
the page, over other text — the field is left unchanged and the response says to shorten it.

Also guards the read-back gap this exposed: glyphs past the paper's edge are not extracted, so a
value drawn from x=524 to 647 on a 595pt page read back as its first half and shipped cut off.
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
by = {s["text"].strip(): s for s in c.post("/extract", files={"file": ("a.pdf", pdf, "application/pdf")}).json()["spans"]}


def edit(old, new, data=pdf, spans=by):
    r = c.post("/edit", files={"file": ("a.pdf", data, "application/pdf")},
               data={"edits": json.dumps([{"index": spans[old]["id"], "new_text": new}])})
    return fitz.open(stream=r.content, filetype="pdf")[0], json.loads(base64.b64decode(r.headers["x-redraft-font-report"]))


def sizes_of(page, text):
    return sorted({round(t["size"], 2) for t in page.get_texttrace()
                   if text[:6] in "".join(chr(ch[0]) for ch in t["chars"])})


page, rep = edit("Sara Idrissi", "Abdurrahamn Chahrour Al-Fassi")
check("a longer name is placed", "Abdurrahamn Chahrour Al-Fassi" in page.get_text())
check("... at its original size", sizes_of(page, "Abdurrahamn") == [14.04], sizes_of(page, "Abdurrahamn"))

long = "Abdurrahamn Chahrour Al-Fassi Idrissi Benjelloun El Amrani Tazi Bennani Sqalli"
page, rep = edit("Sara Idrissi", long)
check("a value too long for the page is not drawn smaller", not rep.get("resized_to_fit"), rep.get("resized_to_fit"))
check("... the field is left as it was", "Sara Idrissi" in page.get_text() and "Benjelloun" not in page.get_text())
check("... and the response says to shorten it",
      any("left unchanged" in w and "shorten" in w for w in rep.get("warnings", [])), rep.get("warnings"))
check("no text on the page is smaller than the document set it",
      all(t["size"] >= 13.9 for t in page.get_texttrace()
          if any(w in "".join(chr(ch[0]) for ch in t["chars"]) for w in ("Idrissi", "Sara"))))

lf = os.path.expanduser("~/.cache/redraft-audit/corpus/leaderform.pdf")
if os.path.exists(lf):
    data = open(lf, "rb").read()
    api._ingest_embedded_fonts(data)
    sd = [s for s in api.extract_spans(data) if s["text"].strip() == "3,210.00"][0]
    out, rep2 = api.apply_replacements(data, [(sd, "Zoé Ångström-Ñuñez 3,210.00")], preserve_size=True, try_inplace=True)
    p2 = fitz.open(stream=out, filetype="pdf")[sd["page"]]
    off = [t for t in p2.get_texttrace() if any(ch[3][2] > p2.rect.width + 0.5 for ch in t["chars"])]
    check("a value that would run past the paper's edge is not shipped cut off", not off and "3,210.00" in p2.get_text(),
          [("".join(chr(ch[0]) for ch in t["chars"]))[:30] for t in off])
else:
    print("SKIP - leaderform case: audit corpus not built")

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
