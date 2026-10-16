"""test_move.py — moving a field keeps its typeface and size and leaves the rest of its line alone.

A move is sent as {index, new_text: "", erase: true} plus a text stamp at the new place.
Two faults, both seen on the attestation:
  * the stamp was drawn in Helvetica: insert_text has no fontbuffer argument, so registering the
    document's font raised and every stamp fell back to the builtin — bigger, another typeface;
  * erasing the field closed the line up behind it: moving "Essaouira" pulled the comma Word had
    drawn over its end 56pt left, in front of the "à".
"""
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
city = [s for s in spans if s["text"] == "Essaouira"][0]
comma = [s for s in spans if s["text"] == ", " and 296 < s["bbox"][1] < 300][0]
date = [s for s in spans if s["text"] == "12/05/2001"][0]


def move(span, dx, dy, bold):
    st = {"kind": "text", "page": 0, "x": span["bbox"][0] + dx, "y": span["bbox"][1] + dy,
          "text": span["text"], "size": span["size"], "color": "#353333", "font": span["font"],
          "bold": bold, "italic": False}
    r = c.post("/edit", files={"file": ("a.pdf", pdf, "application/pdf")},
               data={"edits": json.dumps([{"index": span["id"], "new_text": "", "erase": True}]),
                     "stamps": json.dumps([st])})
    return fitz.open(stream=r.content, filetype="pdf")[0]


def drawn(page, text):
    for t in page.get_texttrace():
        s = "".join(chr(ch[0]) for ch in t["chars"])
        if text.strip() and text.strip() in s:
            return t
    return None


def char_x(page, ch, y0, y1):
    return [round(c_["origin"][0], 1) for b in page.get_text("rawdict")["blocks"] for l in b.get("lines", [])
            for sp in l["spans"] for c_ in sp["chars"] if c_["c"] == ch and y0 < c_["bbox"][1] < y1]


page = move(city, 60, -20, True)
t = drawn(page, "Essaouira")
check("moved word: drawn in the document's Tw Cen MT Bold, not Helvetica", t and t["font"] == "TwCenMT-Bold", t and t["font"])
check("moved word: same size", t and abs(t["size"] - city["size"]) < 0.01, t and t["size"])
check("moved word: baseline moved by exactly the drag",
      t and abs(t["chars"][0][2][1] - (city["origin"][1] - 20)) < 0.3, t and t["chars"][0][2][1])
check("moved word: the comma after it stays where it was", char_x(page, ",", 296, 300) == [round(comma["origin"][0], 1)],
      char_x(page, ",", 296, 300))
check("moved word: the date before it stays where it was",
      any(abs(w[0] - date["bbox"][0]) < 0.3 for w in page.get_text("words") if w[4] == "12/05/2001"))

page = move(comma, 14.5, 0, False)
check("moved comma: the line reads 'Essaouira,'", "Essaouira," in page.get_text().replace(" ", ""))
check("moved comma: 'Essaouira' itself did not move",
      any(abs(w[0] - city["bbox"][0]) < 0.3 for w in page.get_text("words") if w[4].startswith("Essaouira")))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
