#!/usr/bin/env bash
# Build the one-container image, then prove from INSIDE a container with no network that it works:
#   the editor page and every file it links load with no third-party host in it, the API refuses a
#   call without the key, /v1/replace edits a PDF (also one needing a bundled script font), and the
#   watched folder turns a PDF dropped in in/ into one in out/.
#
#   bash spikes/verify_container.sh        # exit 0 only if every check passes
#
# --network host for the BUILD only: it is a build-time need (npm, font downloads), and the default
# bridge has no route on a host with IPv4 forwarding off. The RUN is --network none.
set -euo pipefail
cd "$(dirname "$0")/.."

IMG=${IMG:-redraft:test}
NAME=redraft-verify
KEY="verify-$RANDOM$RANDOM$RANDOM"
WORK=$(mktemp -d)
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "disk before:"; df -h / | tail -1
docker build --network host -t "$IMG" .
echo "image: $(docker images "$IMG" --format '{{.Size}}')"

mkdir -p "$WORK/data"
chmod 777 "$WORK/data"
docker run -d --name "$NAME" --network none \
  -e REDRAFT_OFFLINE=1 -e REDRAFT_API_KEY="$KEY" -e REDRAFT_WATCH=/data \
  -v "$WORK/data:/data" "$IMG" >/dev/null
echo "font cache in the image: $(docker exec "$NAME" du -sh /app/.font_cache | cut -f1)"

if ! docker exec -i -e KEY="$KEY" "$NAME" python - <<'PY'
import base64, json, os, re, socket, sys, time, urllib.error, urllib.request
import fitz

KEY, B, bad = os.environ["KEY"], "http://127.0.0.1:7860", []


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", "-", name, ("  " + str(detail)[:200]) if (detail and not cond) else "")
    if not cond:
        bad.append(name)


def call(path, body=None, key=False, accept=None):
    h = {"Content-Type": "application/json"} if body is not None else {}
    if key:
        h["X-API-Key"] = KEY if key is True else key
    if accept:
        h["Accept"] = accept
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(B + path, data, h), timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def pdf(text):
    d = fitz.open()
    d.new_page(width=595, height=842).insert_text((70, 120), text, fontsize=12, fontname="helv")
    return d.tobytes()


def text_of(b):
    return "".join(p.get_text() for p in fitz.open(stream=b, filetype="pdf"))


for _ in range(60):
    try:
        if call("/health")[0] == 200:
            break
    except Exception:
        time.sleep(1)
else:
    print("FAIL - the server never came up")
    sys.exit(1)

try:
    socket.create_connection(("1.1.1.1", 443), timeout=3)
    out = True
except OSError:
    out = False
check("the container really has no route out", not out)

s, b = call("/app")
html = b.decode("utf-8", "replace")
check("the editor page loads without a key", s == 200 and "<html" in html.lower(), s)
check("the page names no host outside the container", not re.findall(r"https?://(?!www\.w3\.org)[^\"'\s)<]+", html),
      re.findall(r"https?://(?!www\.w3\.org)[^\"'\s)<]+", html))
links = re.findall(r'(?:src|href)="(/[^"]+)"', html)
check("the page links its own script and styles", any(l.startswith("/assets/") for l in links), links)
for l in links:
    if l != "/app":
        check(f"linked file loads: {l}", call(l)[0] == 200)
if "/fonts.css" in links:
    css = call("/fonts.css")[1].decode()
    fonts = re.findall(r"url\((/fonts/[^)]+)\)", css)
    check("the fonts are served from the container", bool(fonts) and all(call(f)[0] == 200 for f in fonts), fonts[:2])

body = {"pdf": base64.b64encode(pdf("Invoice 4821 total due")).decode(), "changes": [{"find": "4821", "replace": "4822"}]}
check("the API refuses a call with no key", call("/v1/replace", body)[0] == 401)
check("the API refuses a wrong key", call("/v1/replace", body, key="not-the-key")[0] == 401)
t = time.time()
s, b = call("/v1/replace", body, key=True)
r = json.loads(b) if s == 200 else {}
check("/v1/replace edits a PDF", s == 200 and r.get("receipt", {}).get("status") == "ok", (s, b[:200]))
check("the new text is in the PDF and the old is gone", s == 200 and "4822" in text_of(base64.b64decode(r["pdf"])) and "4821" not in text_of(base64.b64decode(r["pdf"])))
print(f"  ({time.time() - t:.1f} s)")

body["changes"] = [{"find": "Invoice", "replace": "Счёт"}]   # Cyrillic: Helvetica has none, so a bundled donor must supply it
s, b = call("/v1/replace", body, key=True)
r = json.loads(b) if s == 200 else {}
check("an edit needing a bundled script font works with no network", s == 200 and r.get("receipt", {}).get("status") == "ok", (s, b[:300]))

with open("/data/replace.json", "w") as f:
    json.dump([{"find": "4821", "replace": "4822"}], f)
time.sleep(3)
with open("/data/in/drop.pdf", "wb") as f:
    f.write(pdf("Invoice 4821 total due"))
for _ in range(40):
    if os.path.exists("/data/out/drop.pdf") and os.path.exists("/data/out/drop.receipt.json"):
        break
    time.sleep(1)
check("watched folder: the edited PDF and its receipt reach out/", os.path.exists("/data/out/drop.pdf") and os.path.exists("/data/out/drop.receipt.json"), os.listdir("/data/in"))
check("watched folder: the original moves to done/", os.path.exists("/data/done/drop.pdf") and not os.path.exists("/data/in/drop.pdf"))
check("watched folder: the output is edited", os.path.exists("/data/out/drop.pdf") and "4822" in text_of(open("/data/out/drop.pdf", "rb").read()))

print("\nRESULT:", "FAIL " + str(bad) if bad else "all passed")
sys.exit(1 if bad else 0)
PY
then
  echo "--- container log ---"; docker logs --tail 40 "$NAME" 2>&1 || true
  exit 1
fi
