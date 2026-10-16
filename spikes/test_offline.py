"""test_offline.py — REDRAFT_OFFLINE=1: the engine never opens a connection.

A container on a customer's network may have no route out at all. Two things must hold:

  * nothing the engine does reaches for the network — not the font catalogue listing, not a
    donor font, not a script fallback, not the Computer Modern originals — and a font that is
    not on disk is a final, immediate "no", never a timeout and never a retry timer;
  * everything that IS on disk is used: a font bundled at build time (warm_fonts.py) resolves
    with the network cut.

Each scenario runs in a child process on a throw-away copy of backend/ (an empty font cache, so
the developer's own cache cannot hide a download) with every socket call recorded and refused.
The guard is calibrated first: the same calls with the flag OFF must be seen attempting to connect.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


CHILD = r'''
import base64, json, os, socket, sys, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.getcwd())
attempts = []
def _refuse(*a, **k):
    attempts.append(repr(a)[:80]); raise OSError("network is cut")
socket.socket.connect = _refuse
socket.getaddrinfo = _refuse
socket.create_connection = _refuse

import fitz
SEED = os.environ.get("SEED") == "1"
if SEED:                                    # what warm_fonts.py leaves in an image
    import pdf_editor as _pe0
    dv = _pe0._find_system_font("DejaVu Sans", 400, "normal")
    assert dv, "no system font to seed with"
    for rel in ("ofl/zorblax/Zorblax-Regular.ttf", "ofl/notosansarabic/NotoSansArabic[wdth,wght].ttf"):
        path = os.path.join(_pe0._RAW_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(dv)
    with open(os.path.join(_pe0._FONT_CACHE_DIR, "_gf_repo_listing_cache.json"), "w") as f:
        json.dump({"ofl/zorblax": ["Zorblax-Regular.ttf"]}, f)

import pdf_editor as pe, font_extend as fe, type1_extend as t1
out = {}
def timed(label, fn):
    t = time.time(); v = fn(); out[label] = [v, round(time.time() - t, 2)]
timed("donor_missing", lambda: [x is not None for x in fe.resolve_donor_detailed("Quuxfont-Regular")])
timed("donor_bundled", lambda: [len(fe.resolve_donor_detailed("Zorblax-Regular")[0] or b""),
                                 fe.resolve_donor_detailed("Zorblax-Regular")[1]])
timed("fallback_script", lambda: len(pe._fetch_google_font("Noto Sans Arabic", 400, "normal") or b""))
timed("full_font_missing", lambda: pe.resolve_full_font("Quuxfont-Regular") is not None)
timed("type1", lambda: t1.donor_program("cmr10") is not None)
out["retry_timer_set"] = bool(fe._donor_retry_at)

import api
from fastapi.testclient import TestClient
d = fitz.open(); d.new_page(width=595, height=842).insert_text((70, 120), "Invoice 4821 total due", fontsize=12, fontname="helv")
pdf = d.tobytes()
c = TestClient(api.app)
def call(find, replace):
    r = c.post("/v1/replace", json={"pdf": base64.b64encode(pdf).decode(),
                                    "changes": [{"find": find, "replace": replace}]})
    return [r.status_code, (r.json().get("receipt") or {}).get("status")]
timed("edit_plain", lambda: call("4821", "4822"))
timed("edit_needing_a_font", lambda: call("Invoice", "فاتورة"))
out["attempts"] = len(attempts)
print("CHILD_JSON " + json.dumps(out))
'''


def run_child(offline, seed=False):
    tmp = tempfile.mkdtemp(prefix="redraft_offline_")
    try:
        for n in os.listdir(BACKEND):
            if n.endswith(".py") or n == "donor_manifest.json":
                shutil.copy(os.path.join(BACKEND, n), tmp)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("REDRAFT_", "SUPABASE_"))}
        env["SEED"] = "1" if seed else "0"
        if offline:
            env["REDRAFT_OFFLINE"] = "1"
        t = time.time()
        p = subprocess.run([sys.executable, "-c", CHILD], cwd=tmp, env=env, capture_output=True,
                           text=True, timeout=300)
        lines = [l for l in p.stdout.splitlines() if l.startswith("CHILD_JSON ")]
        if not lines:
            return None, (p.stderr or p.stdout)[-600:]
        return json.loads(lines[-1][11:]), round(time.time() - t, 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── calibration: the guard must SEE a network attempt when the flag is off ────
res, info = run_child(offline=False)
check("calibration: the child runs", res is not None, info)
if res is None:
    sys.exit(1)
check("calibration: with the flag OFF the same calls are seen reaching for the network",
      res["attempts"] > 0, res["attempts"])

# ── 1. offline, nothing on disk ───────────────────────────────────────────────
res, info = run_child(offline=True)
check("offline: the child runs", res is not None, info)
if res is None:
    sys.exit(1)
check("offline: not one connection attempt, through every font path", res["attempts"] == 0, res["attempts"])
check("offline: a donor that is not on disk is a final 'no'", res["donor_missing"][0] == [False, False], res["donor_missing"])
check("offline: ... with no retry timer left behind", res["retry_timer_set"] is False)
check("offline: a script fallback that is not on disk is a clean miss", res["fallback_script"][0] == 0)
check("offline: a full font that is not on disk is a clean miss", res["full_font_missing"][0] is False)
check("offline: a Computer Modern original that is not on disk is a clean miss", res["type1"][0] is False)
slow = {k: v[1] for k, v in res.items() if isinstance(v, list) and len(v) == 2 and v[1] > 3.0}
check("offline: every lookup answers at once (no timeouts)", not slow, slow)
check("offline: an ordinary edit still works", res["edit_plain"][0] == [200, "ok"], res["edit_plain"])
check("offline: an edit that needs a font it lacks is answered, not a server error",
      res["edit_needing_a_font"][0][0] < 500, res["edit_needing_a_font"])

# ── 2. offline, fonts bundled on disk ─────────────────────────────────────────
res, info = run_child(offline=True, seed=True)
check("bundled: the child runs", res is not None, info)
if res is None:
    sys.exit(1)
check("bundled: not one connection attempt", res["attempts"] == 0, res["attempts"])
check("bundled: a donor font from disk is used, as the same typeface",
      res["donor_bundled"][0][0] > 10000 and res["donor_bundled"][0][1] == "family", res["donor_bundled"])
check("bundled: a script fallback from disk is used", res["fallback_script"][0] > 10000, res["fallback_script"])
check("bundled: a font that is still not on disk is still a clean miss", res["donor_missing"][0] == [False, False])

# ── 3. online behaviour is unchanged, and downloads are kept ──────────────────
sys.path.insert(0, BACKEND)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")
import pdf_editor as pe  # noqa: E402
import urllib.request  # noqa: E402

tmp = tempfile.mkdtemp(prefix="redraft_raw_")
real_dir, real_open = pe._RAW_DIR, urllib.request.urlopen
pe._RAW_DIR = tmp
calls = []


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


body = {"v": b"F" * 5000}
urllib.request.urlopen = lambda req, timeout=None: calls.append(req.full_url) or Resp(body["v"])
os.environ.pop("REDRAFT_OFFLINE", None)
URL = pe._GF_RAW + "/ofl/zorblax/Zorblax[wght].ttf"
try:
    a = pe.fetch(URL)
    b = pe.fetch(URL)
    check("online: a download is fetched once and kept", a == b == body["v"] and len(calls) == 1, calls)
    check("online: the copy sits under the catalogue's own layout",
          os.path.exists(os.path.join(tmp, "ofl", "zorblax", "Zorblax[wght].ttf")))
    body["v"] = b"404: Not Found"
    calls.clear()
    pe.fetch(pe._GF_RAW + "/ofl/zorblax/Missing.ttf")
    pe.fetch(pe._GF_RAW + "/ofl/zorblax/Missing.ttf")
    check("online: a too-small answer (an error page) is not kept", len(calls) == 2, calls)
    check("online: _gh_get answers None on failure, as before",
          (setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))) or
           pe._gh_get(pe._GF_RAW + "/ofl/zorblax/Other.ttf")) is None)
    os.environ["REDRAFT_OFFLINE"] = "1"
    calls.clear()
    urllib.request.urlopen = lambda req, timeout=None: calls.append(req.full_url) or Resp(b"F" * 5000)
    try:
        pe.fetch(pe._GF_RAW + "/ofl/zorblax/NotOnDisk.ttf")
        raised = False
    except OSError:
        raised = True
    check("offline: fetch of a font not on disk raises and asks nobody", raised and not calls, calls)
    check("offline: fetch of a font on disk returns it", pe.fetch(URL) == b"F" * 5000)
    for v, want in (("1", True), ("true", True), (" YES ", True), ("on", True), ("0", False),
                    ("", False), ("no", False), ("off", False)):
        os.environ["REDRAFT_OFFLINE"] = v
        check(f"REDRAFT_OFFLINE={v!r} reads as {want}", pe.offline() is want)
finally:
    pe._RAW_DIR, urllib.request.urlopen = real_dir, real_open
    os.environ.pop("REDRAFT_OFFLINE", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
