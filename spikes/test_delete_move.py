"""test_delete_move.py — deleting or moving a field works, including one Word drew over its neighbour.

On the attestation Word placed the comma after the city at a fixed spot, inside "Essaouira".
Moving or deleting it (the editor's move = erase the span + stamp it elsewhere) was refused:
  * verification looked for the replacement's beginning, which a deletion doesn't have, so no
    deletion could ever pass in place;
  * the search over occurrences of ", " (ten in that font) stopped at the first ragged match
    instead of trying the comma that was clicked;
  * the fallback redraw then (rightly) refused, since erasing that rectangle eats "ra".
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
comma = [s for s in spans if s["text"] == ", " and 296 < s["bbox"][1] < 300][0]
city = [s for s in spans if s["text"] == "Essaouira"][0]


def edit(edits, stamps=()):
    r = c.post("/edit", files={"file": ("a.pdf", pdf, "application/pdf")},
               data={"edits": json.dumps(edits), "stamps": json.dumps(list(stamps))})
    rep = json.loads(base64.b64decode(r.headers["x-redraft-font-report"]))
    return fitz.open(stream=r.content, filetype="pdf")[0], rep


def line_text(page, y0, y1):
    return " ".join(w[4] for w in sorted(page.get_text("words"), key=lambda w: w[0]) if y0 < w[1] < y1)


# delete the comma
page, rep = edit([{"index": comma["id"], "new_text": ""}])
chars = [ch for b in page.get_text("rawdict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
         for ch in s["chars"] if 296 < ch["bbox"][1] < 300]
check("delete: done in place, no warning", rep["in_place"]["count"] == 1 and not rep["warnings"], rep["warnings"])
check("delete: no comma left on the city line", not any(ch["c"] == "," for ch in chars))
check("delete: 'Essaouira' intact", "Essaouira" in line_text(page, 296, 300))

# move it after the city (what the editor sends: erase + stamp)
stamp = {"kind": "text", "page": 0, "x": city["bbox"][2] + 0.5, "y": comma["bbox"][1], "text": ", ",
         "size": comma["size"], "color": "#353333", "font": comma["font"], "bold": False, "italic": False}
page, rep = edit([{"index": comma["id"], "new_text": ""}], [stamp])
check("move: the original is erased in place", rep["in_place"]["count"] == 1 and not rep["warnings"], rep["warnings"])
check("move: the line now reads 'Essaouira,'", "Essaouira," in page.get_text().replace(" ", ""), page.get_text()[:300])

# an ordinary deletion of a whole word
page, rep = edit([{"index": city["id"], "new_text": ""}])
check("delete a word: done in place", rep["in_place"]["count"] == 1, rep)
check("delete a word: it is gone, its neighbours are not",
      "Essaouira" not in page.get_text() and "12/05/2001" in page.get_text())

# calibration: the name comma (first occurrence) must not be the one removed
check("calibration: deleting the city comma leaves the name line's comma",
      line_text(edit([{"index": comma["id"], "new_text": ""}])[0], 282, 286).endswith(","),
      line_text(edit([{"index": comma["id"], "new_text": ""}])[0], 282, 286))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
