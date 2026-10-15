"""test_api_key.py — REDRAFT_API_KEY: a container with a key answers only to callers holding it.

The one-container deployment stays closed on the customer's network by this key, so the
failure that matters is a route that answers WITHOUT it. The checks go both ways:

  * with no key configured nothing changes (the hosted app and every other test rely on it);
  * with one, every route — not just /v1/replace — refuses a missing or wrong key, including
    near-misses (a prefix, a longer string, other case, the key in the query string);
  * the right key opens the door by either header, and a rotated pair of keys both work;
  * a refused call is refused BEFORE its body is read;
  * a key replaces the Supabase login: no metering, no attribution, no call to Supabase.

Fixtures are built here; nothing is downloaded.
"""
import base64
import json
import os
import subprocess
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.filterwarnings("ignore")

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import api  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


d = fitz.open()
d.new_page(width=595, height=842).insert_text((70, 120), "Invoice 4821 total due", fontsize=12, fontname="helv")
PDF = d.tobytes()
d.close()
BODY = {"pdf": base64.b64encode(PDF).decode(), "changes": [{"find": "4821", "replace": "4822"}]}

client = TestClient(api.app)
KEY = "k-live-0123456789abcdef"
ROUTES = [("POST", "/v1/replace"), ("POST", "/edit"), ("POST", "/extract"), ("POST", "/bulk"),
          ("POST", "/compose"), ("GET", "/me"), ("POST", "/fonts")]


def call(method, path, **kw):
    return client.request(method, path, **kw)


def replace(headers=None, params=None):
    return client.post("/v1/replace", json=BODY, headers=headers or {}, params=params)


# ── 1. no key configured: open, exactly as before ────────────────────────────
api.API_KEYS = []
r = replace()
check("no key set: /v1/replace answers without credentials", r.status_code == 200, r.text[:200])
check("no key set: the edit is made", r.status_code == 200 and r.json()["receipt"]["status"] == "ok")
check("no key set: the root reports key=false", client.get("/").json().get("key") is False)
check("no key set: the OpenAPI document carries no security scheme",
      "security" not in client.get("/openapi.json").json())

# ── 2. a key is set: every route refuses a missing or wrong key ───────────────
api.API_KEYS = [KEY.encode()]
api.app.openapi_schema = None
for method, path in ROUTES:
    r = call(method, path)
    check(f"{method} {path}: refused without a key", r.status_code == 401, f"{r.status_code} {r.text[:120]}")

BAD = {
    "wrong key": {"X-API-Key": "nope"},
    "empty key": {"X-API-Key": ""},
    "prefix of the key": {"X-API-Key": KEY[:-1]},
    "key plus a character": {"X-API-Key": KEY + "x"},
    "other case": {"X-API-Key": KEY.upper()},
    "key in the wrong scheme": {"Authorization": f"Basic {KEY}"},
    "bearer with no token": {"Authorization": "Bearer "},
    "bearer of another token": {"Authorization": "Bearer eyJhbGciOi.fake.jwt"},
}
for label, headers in BAD.items():
    r = replace(headers)
    check(f"/v1/replace refuses {label}", r.status_code == 401, f"{r.status_code} {r.text[:100]}")
r = replace(params={"api_key": KEY, "key": KEY, "X-API-Key": KEY})
check("/v1/replace refuses the key sent in the query string", r.status_code == 401, r.status_code)
r = replace({"X-API-Key": "nope", "Authorization": "Bearer nope"})
check("two wrong keys are still wrong", r.status_code == 401)
check("the refusal names the headers that work",
      "X-API-Key" in replace().json()["detail"] and replace().headers.get("www-authenticate") == "Bearer")
check("a refused call never reaches the engine", "receipt" not in replace().json())

# ── 3. the right key ──────────────────────────────────────────────────────────
for label, headers in (("X-API-Key", {"X-API-Key": KEY}),
                       ("Authorization: Bearer", {"Authorization": f"Bearer {KEY}"}),
                       ("bearer in lower case", {"Authorization": f"bearer {KEY}"}),
                       ("key with stray spaces", {"X-API-Key": f"  {KEY}  "})):
    r = replace(headers)
    check(f"the right key opens /v1/replace by {label}",
          r.status_code == 200 and r.json()["receipt"]["status"] == "ok", f"{r.status_code} {r.text[:150]}")
r = replace({"X-API-Key": "nope", "Authorization": f"Bearer {KEY}"})
check("the right key is enough even beside a wrong one", r.status_code == 200, r.status_code)
out = base64.b64decode(replace({"X-API-Key": KEY}).json()["pdf"])
check("the edited file really changed", "4822" in fitz.open(stream=out, filetype="pdf")[0].get_text())
check("the other routes accept the key too", client.get("/me", headers={"X-API-Key": KEY}).status_code == 200)

# ── 4. what stays reachable ───────────────────────────────────────────────────
for path in ("/", "/health", "/docs", "/openapi.json"):
    check(f"{path} answers without a key", client.get(path).status_code == 200, path)
check("the root reports key=true", client.get("/").json().get("key") is True)
spec = client.get("/openapi.json").json()
check("the OpenAPI document declares the key (a connector needs it)",
      spec.get("security") == [{"ApiKey": []}, {"Bearer": []}]
      and spec["components"]["securitySchemes"]["ApiKey"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"})
check("a path that only starts like an open one is not open",
      client.get("/healthcheck").status_code == 401 and client.get("/docs-extra").status_code == 401)
r = client.options("/v1/replace", headers={"Origin": "http://localhost:5173",
                                           "Access-Control-Request-Method": "POST",
                                           "Access-Control-Request-Headers": "x-api-key"})
check("a browser preflight is answered without a key", r.status_code == 200, r.status_code)
r = client.post("/v1/replace", json=BODY, headers={"Origin": "http://localhost:5173"})
check("a refusal still carries CORS headers, so a browser can read it",
      r.status_code == 401 and r.headers.get("access-control-allow-origin") == "http://localhost:5173")

# ── 5. refused before the body is read ────────────────────────────────────────
r = client.post("/v1/replace", content=b"--not multipart--", headers={"Content-Type": "multipart/form-data; boundary=zz"})
check("a malformed upload is refused for the key, not parsed first", r.status_code == 401, r.status_code)
r = client.post("/v1/replace", content=b"{not json", headers={"Content-Type": "application/json"})
check("a malformed JSON body is refused for the key, not parsed first", r.status_code == 401, r.status_code)
r = client.post("/v1/replace", content=b"{not json", headers={"Content-Type": "application/json", "X-API-Key": KEY})
check("with the key the same body reaches the parser and is a 400", r.status_code == 400, r.status_code)

# ── 6. rotation: two keys at once ─────────────────────────────────────────────
api.API_KEYS = [b"old-key-aaaaaaaaaaaa", b"new-key-bbbbbbbbbbbb"]
check("rotation: the old key works", replace({"X-API-Key": "old-key-aaaaaaaaaaaa"}).status_code == 200)
check("rotation: the new key works", replace({"X-API-Key": "new-key-bbbbbbbbbbbb"}).status_code == 200)
check("rotation: a retired key does not", replace({"X-API-Key": KEY}).status_code == 401)

# ── 7. the environment variable, as the container sets it ────────────────────
CODE = ("import warnings; warnings.filterwarnings('ignore'); import api; "
        "print(json_dumps([[k.decode() for k in api.API_KEYS], api.AUTH_ON]))")


def env_state(**env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("REDRAFT_", "SUPABASE_"))}
    base.update(env)
    out = subprocess.run([sys.executable, "-c", "from json import dumps as json_dumps; " + CODE],
                         cwd=os.path.join(HERE, "..", "backend"), env=base, capture_output=True, text=True)
    return json.loads(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else out.stderr[-300:]


check("unset: no keys", env_state() == [[], False], env_state())
check("blank or only commas: no keys", env_state(REDRAFT_API_KEY=" , ,") == [[], False])
check("a list is split and trimmed", env_state(REDRAFT_API_KEY=" a-key ,b-key,") == [["a-key", "b-key"], False])
SB = dict(SUPABASE_URL="https://x.supabase.co", SUPABASE_ANON_KEY="a", SUPABASE_SERVICE_KEY="s")
check("Supabase configured and no key: login stays on (the hosted app)", env_state(**SB) == [[], True])
check("a key beside Supabase: the key wins and login is off", env_state(REDRAFT_API_KEY="kk", **SB) == [["kk"], False])

# ── 8. no account to meter, no footer ─────────────────────────────────────────
api.API_KEYS = [KEY.encode()]
calls = []
real = api._sb_http
api._sb_http = lambda *a, **k: calls.append(a) or (0, None)
try:
    r = replace({"X-API-Key": KEY})
    out = base64.b64decode(r.json()["pdf"])
finally:
    api._sb_http = real
check("a keyed call makes no Supabase request", not calls, calls)
check("a keyed call's output carries no 'Made with Redraft' footer",
      "Made with Redraft" not in fitz.open(stream=out, filetype="pdf")[0].get_text())

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
