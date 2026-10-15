"""test_edit_speed.py — the speed-ups never change what an edit produces.

Three were added after one Chrome PDF took 21 s for 4 edits (16.5 s of it re-saving the whole file
with garbage=4 after every step) and the attestation's 7 edits read the same page 132 times over:
  * textcache: page text reused while the document it came from is unchanged;
  * pdfsave: intermediate saves through a file, garbage=1;
  * /edit remembers recent results, so a download right after its preview is not computed twice.
"""
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import fitz  # noqa: E402
import api  # noqa: E402
import pdfsave  # noqa: E402
import textcache  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

api.AUTH_ON = False
c = TestClient(api.app)
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


pdf = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()

# ── textcache ───────────────────────────────────────────────────────────────
d = fitz.open(stream=pdf, filetype="pdf")
first = d[0].get_text("dict")
first["blocks"].clear()
check("textcache: a caller emptying its result does not change the next read",
      len(d[0].get_text("dict")["blocks"]) > 0)
before = d[0].get_text("text")
d[0].insert_text((60, 60), "ZQXJ marker", fontsize=12)
check("textcache: a changed document is read fresh, not from the cache", "ZQXJ marker" in d[0].get_text("text"))
d2 = fitz.open(stream=pdf, filetype="pdf")
check("textcache: the original bytes still read as the original", d2[0].get_text("text") == before)
check("textcache: it is actually installed and used", textcache.stats()["hit"] > 0, textcache.stats())

# ── pdfsave ─────────────────────────────────────────────────────────────────
d3 = fitz.open(stream=pdf, filetype="pdf")
d3[0].insert_text((60, 80), "saved through a file", fontsize=11)
fast = pdfsave.to_bytes(d3, garbage=1, deflate=True)
slow = d3.tobytes(garbage=1, deflate=True)
a, b = fitz.open(stream=fast, filetype="pdf")[0], fitz.open(stream=slow, filetype="pdf")[0]
check("pdfsave: same text as tobytes", a.get_text("text") == b.get_text("text"))
check("pdfsave: same pixels as tobytes", a.get_pixmap().samples == b.get_pixmap().samples)

# ── /edit result reuse ──────────────────────────────────────────────────────
spans = c.post("/extract", files={"file": ("a.pdf", pdf, "application/pdf")}).json()["spans"]
by = {s["text"].strip(): s for s in spans}
edits = json.dumps([{"index": by["Sara Idrissi"]["id"], "new_text": "Sara Idriss"},
                    {"index": by["2025/2026"]["id"], "new_text": "2025/2099"}])


def edit(e, final=False):
    t = time.perf_counter()
    r = c.post("/edit", files={"file": ("a.pdf", pdf, "application/pdf")},
               data={"edits": e, **({"final": "1"} if final else {})})
    return time.perf_counter() - t, r


api._EDIT_RESULTS.clear()
t1, r1 = edit(edits)
t2, r2 = edit(edits, final=True)
check("reuse: the preview edits", r1.status_code == 200 and "2025/2099" in fitz.open(stream=r1.content, filetype="pdf")[0].get_text())
check("reuse: the download of the same edits is the same document", r2.content == r1.content)
check("reuse: ... and is not computed again", t2 < max(0.2, t1 / 5), (round(t1, 2), round(t2, 2)))
t3, r3 = edit(json.dumps([{"index": by["Sara Idrissi"]["id"], "new_text": "Sara Idrissa"}]))
check("reuse: different edits are computed, not served from another request",
      "Sara Idrissa" in fitz.open(stream=r3.content, filetype="pdf")[0].get_text())

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
