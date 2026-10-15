"""test_ui_serving.py — the one-container build serves the editor page from the API process.

What must hold when a built page sits in REDRAFT_UI_DIR (the container image puts it in /app/ui):

  * the page and its files load WITHOUT the API key (a browser fetching them cannot send one),
    while every API route still refuses a call without it, including look-alike paths
    (/app-x, /assets-x, a traversal out of the page folder) — the page being public must not
    become a hole;
  * a browser opening / is sent to the editor, an API client opening / still gets the JSON;
  * with no page built, nothing changes: no /app, / is JSON for everyone, and the gate has
    no extra open paths.

Each scenario runs in a child process because api.py reads the folder once, at import.
The gate is calibrated first: the same checks against a gate that opens everything, and against
one that opens nothing, must FAIL.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


CHILD = r'''
import json, os, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.getcwd())
import api
from fastapi.testclient import TestClient
mode = os.environ.get("GATE", "real")
if mode == "open":
    api._key_open = lambda p: True
elif mode == "shut":
    api._key_open = lambda p: False
c = TestClient(api.app, follow_redirects=False)
KEY = "k-live-0123456789abcdef"
api.API_KEYS = [KEY.encode()] if os.environ.get("KEYED") == "1" else []
BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
def g(path, headers=None, key=False):
    h = dict(headers or {})
    if key: h["X-API-Key"] = KEY
    r = c.get(path, headers=h)
    return [r.status_code, r.headers.get("content-type", ""), r.text[:60], r.headers.get("location", "")]
out = {"ui_on": api.UI_ON}
for name, path in [("app", "/app"), ("app_deep", "/app/some/route"), ("js", "/assets/index-abc.js"),
                   ("fonts_css", "/fonts.css"), ("font", "/fonts/a.woff2"), ("favicon", "/favicon.js"),
                   ("app_html_file", "/app.html"),
                   ("lookalike_app", "/app-x"), ("lookalike_assets", "/assets-x/index.js"),
                   ("unlisted_file", "/assets/not-built.js"), ("traversal", "/assets/../../etc/passwd"),
                   ("api_me", "/me"), ("health", "/health"), ("docs", "/docs"), ("openapi", "/openapi.json")]:
    out[name] = g(path)
out["root_browser"] = g("/", BROWSER)
out["root_client"] = g("/")
out["root_json_accept"] = g("/", {"Accept": "application/json"})
out["replace_nokey"] = c.post("/v1/replace", json={"pdf": "", "changes": []}).status_code
out["replace_lookalike"] = c.post("/app/../v1/replace", json={"pdf": "", "changes": []}).status_code
out["edit_nokey"] = c.post("/edit").status_code
out["me_key"] = g("/me", key=True)[0]
print("CHILD_JSON " + json.dumps(out))
'''


def run(ui, keyed, gate="real"):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("REDRAFT_", "SUPABASE_"))}
    env["REDRAFT_UI_DIR"] = ui
    env["KEYED"] = "1" if keyed else "0"
    env["GATE"] = gate
    p = subprocess.run([sys.executable, "-c", CHILD], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    lines = [l for l in p.stdout.splitlines() if l.startswith("CHILD_JSON ")]
    if not lines:
        print((p.stderr or p.stdout)[-800:])
        return None
    return json.loads(lines[-1][11:])


def build_ui(root):
    os.makedirs(os.path.join(root, "assets"))
    os.makedirs(os.path.join(root, "fonts"))
    files = {"app.html": "<!doctype html><title>Redraft</title>PAGE",
             "assets/index-abc.js": "console.log('editor')", "fonts.css": "@font-face{}",
             "fonts/a.woff2": "wOF2", "favicon.js": "// icon"}
    for rel, body in files.items():
        with open(os.path.join(root, rel), "w") as f:
            f.write(body)


with tempfile.TemporaryDirectory(prefix="redraft_ui_") as ui, tempfile.TemporaryDirectory(prefix="redraft_noui_") as empty:
    build_ui(ui)

    # ── calibration ──────────────────────────────────────────────────────────
    wide = run(ui, True, "open")
    shut = run(ui, True, "shut")
    check("calibration: the children run", bool(wide) and bool(shut))
    if not (wide and shut):
        sys.exit(1)
    check("calibration: a gate that opens everything is caught (the API answers without a key)",
          wide["replace_nokey"] != 401 and wide["edit_nokey"] != 401)
    check("calibration: a gate that opens nothing is caught (the page does not load)",
          shut["app"][0] == 401 and shut["js"][0] == 401)

    # ── page built, key set ──────────────────────────────────────────────────
    r = run(ui, True)
    check("page built: the child sees it", r and r["ui_on"] is True)
    if not r:
        sys.exit(1)
    check("keyed: /app loads without a key", r["app"][0] == 200 and "PAGE" in r["app"][2] and "html" in r["app"][1], r["app"])
    check("keyed: a deep link under /app loads the same page", r["app_deep"][0] == 200 and "PAGE" in r["app_deep"][2], r["app_deep"])
    check("keyed: the built script loads without a key", r["js"][0] == 200 and "editor" in r["js"][2], r["js"])
    check("keyed: fonts.css, a font file and favicon.js load without a key",
          r["fonts_css"][0] == 200 and r["font"][0] == 200 and r["favicon"][0] == 200,
          (r["fonts_css"], r["font"], r["favicon"]))
    check("keyed: /health and the docs stay open", r["health"][0] == 200 and r["docs"][0] == 200 and r["openapi"][0] == 200)
    check("keyed: the API refuses a call with no key", r["replace_nokey"] == 401 and r["edit_nokey"] == 401 and r["api_me"][0] == 401,
          (r["replace_nokey"], r["edit_nokey"], r["api_me"]))
    check("keyed: ... and accepts it", r["me_key"] != 401, r["me_key"])
    check("keyed: a look-alike path is not opened by the page's prefix",
          r["lookalike_app"][0] == 401 and r["lookalike_assets"][0] == 401, (r["lookalike_app"], r["lookalike_assets"]))
    check("keyed: a file that is not in the build is not open", r["unlisted_file"][0] == 401, r["unlisted_file"])
    check("keyed: a path out of the page folder is refused", r["traversal"][0] in (401, 404) and "root:" not in r["traversal"][2], r["traversal"])
    check("keyed: a path that climbs out of /app back to the API does not reach it",
          r["replace_lookalike"] in (401, 404, 405), r["replace_lookalike"])
    check("keyed: a browser at / is sent to /app", r["root_browser"][0] in (302, 307) and r["root_browser"][3].endswith("/app"), r["root_browser"])
    check("keyed: an API client at / still gets the JSON",
          r["root_client"][0] == 200 and "json" in r["root_client"][1], r["root_client"])
    check("keyed: Accept: application/json at / is the JSON", r["root_json_accept"][0] == 200 and "json" in r["root_json_accept"][1], r["root_json_accept"])

    # ── page built, no key ───────────────────────────────────────────────────
    r = run(ui, False)
    check("unkeyed: the page loads", r and r["app"][0] == 200 and r["js"][0] == 200)
    check("unkeyed: a look-alike path is just a 404, not served", r and r["lookalike_app"][0] == 404, r and r["lookalike_app"])
    check("unkeyed: the API is open as before (not 401)", r and r["replace_nokey"] != 401, r and r["replace_nokey"])

    # ── no page built ────────────────────────────────────────────────────────
    r = run(empty, True)
    check("no page: the child sees none", r and r["ui_on"] is False)
    check("no page: /app does not exist (refused for want of a key, not served)", r and r["app"][0] == 401, r and r["app"])
    check("no page: / is the JSON for a browser too", r and r["root_browser"][0] == 200 and "json" in r["root_browser"][1],
          r and r["root_browser"])
    check("no page: the API still refuses a call with no key", r and r["replace_nokey"] == 401 and r["me_key"] != 401)
    r = run(empty, False)
    check("no page, no key: /app is a 404", r and r["app"][0] == 404, r and r["app"])

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
