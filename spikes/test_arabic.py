"""test_arabic.py — Arabic edits are placed, read back, and keep the line's punctuation.

examples/arabic-certificate.pdf is Chrome's print of arabic-certificate.html (Amiri, plus a French
line carrying an Arabic name). Before this, every Arabic edit was drawn correctly in the document's
own Amiri and then DROPPED: the read-back compared the typed letters with the shaped presentation
forms the page reports, never matched, and called the field "cannot_place".
"""
import os
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
try:
    import arabic_reshaper  # noqa: F401  (the server installs it: backend/requirements.txt)
    import bidi  # noqa: F401
except ImportError:
    venv = os.path.join(BACKEND, ".venv")
    # sys.prefix, not the interpreter path: a venv's python is a symlink to the system one
    if os.path.exists(venv) and os.path.realpath(sys.prefix) != os.path.realpath(venv):
        sys.exit(subprocess.call([os.path.join(venv, "bin", "python"), os.path.abspath(__file__)] + sys.argv[1:]))
    print("FAIL - arabic-reshaper / python-bidi are not installed (backend/requirements.txt)")
    print("\nRESULT: FAIL ['shaping libraries']")
    sys.exit(1)
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import unicodedata  # noqa: E402
import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


pdf = open(os.path.join(HERE, "..", "examples", "arabic-certificate.pdf"), "rb").read()
api._ingest_embedded_fonts(pdf, "")
spans = api.extract_spans(pdf)
before_text = fitz.open(stream=pdf, filetype="pdf")[0].get_text()

para = next(s for s in spans if "سارة" in s["text"] and "تشهد" in s["text"])
check("the paragraph is extracted in reading order, its full stop LAST",
      para["text"].rstrip().endswith(".") and not para["text"].startswith("."), para["text"])

CASES = [("الصويرة", "مراكش"), ("سارة", "ياسمين"), ("المدرسة", "الجامعة"),
         ("Sara Idrissi", "Yasmine Bennani"), ("الإدريسي", "بناني")]
for old, new in CASES:
    sd = next(s for s in spans if old in s["text"])
    out, rep = api.apply_replacements(pdf, [(sd, sd["text"].replace(old, new, 1))], try_inplace=True)
    page = fitz.open(stream=out, filetype="pdf")[0]
    text = page.get_text()
    dropped = [w for w in rep["warnings"] if "unchanged" in w]
    check(f"{old} -> {new}: applied, not dropped", not dropped, dropped)
    check(f"{old} -> {new}: the new value is in the text layer as letters", new in text,
          [l for l in text.splitlines() if new[:2] in unicodedata.normalize("NFKC", l)])
    check(f"{old} -> {new}: the old value is gone", text.count(old) < before_text.count(old))
    check(f"{old} -> {new}: found by search", bool(page.search_for(new)))
    if sd is para:
        # the stop stays where it was printed: the LEFT end of the right-to-left line
        chars = [c for b in page.get_text("rawdict")["blocks"] for l in b.get("lines", [])
                 for s in l["spans"] for c in s["chars"] if abs(c["origin"][1] - sd["origin"][1]) < 1]
        stop = [c["origin"][0] for c in chars if c["c"] == "."]
        letters = [c["origin"][0] for c in chars if c["c"].isalpha()]
        check(f"{old} -> {new}: the full stop is still at the left end of the line",
              stop and letters and max(stop) < min(letters), (stop, letters and min(letters)))
        fonts = {s["font"] for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
                 for s in l["spans"] if abs(s["origin"][1] - sd["origin"][1]) < 1}
        check(f"{old} -> {new}: drawn in the document's own Amiri", any("Amiri" in f for f in fonts), fonts)

# the service: /v1/replace finds Arabic text and its receipt verifies it, a lam-alef included
# ("سلا" draws its last two letters as one ligature, which reads back as U+FEFB)
import base64  # noqa: E402
import json  # noqa: E402
import asyncio  # noqa: E402


def post_json(path, payload):
    """One request straight into the ASGI app (the shaping libraries live in the backend's
    environment, which need not have the HTTP test client)."""
    body = json.dumps(payload).encode()
    sent, out = [False], {"status": 0, "body": b""}

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": [(b"content-type", b"application/json"),
                                          (b"content-length", str(len(body)).encode())],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}
    asyncio.run(api.app(scope, receive, send))
    return out["status"], json.loads(out["body"] or b"{}")


status, res = post_json("/v1/replace", {"pdf": base64.b64encode(pdf).decode(),
                                        "changes": [{"find": "الصويرة", "replace": "سلا"}]})
rc = res.get("receipt", {})
row = (rc.get("changes") or [{}])[0]
check("/v1/replace: the Arabic text is found and changed", status == 200 and row.get("edited") == 1,
      (status, json.dumps(res, ensure_ascii=False)[:300]))
check("/v1/replace: the receipt reads it back (verified)", row.get("verified") is True and rc.get("ok") is True,
      json.dumps(rc, ensure_ascii=False)[:300])

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
