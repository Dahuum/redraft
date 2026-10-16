"""test_connector.py — the Power Automate connector and /v1/replace say the same thing.

integrations/power-automate/apiDefinition.swagger.json is what a flow builder sees: the fields
they can fill, the outputs they can bind. The failures that matter are silent ones in a tenant:
a field the connector offers that the server rejects (every run fails with a 400), or an output
the server sends that the connector never declared (the flow cannot use it). Both directions are
checked against the real app, under a key, the way the connector calls it.

Also the rules the Power Platform import enforces that a generic OpenAPI validator does not:
OpenAPI 2.0, one security definition whose name the connection parameter matches, and a label
(x-ms-summary) and description on every field.
"""
import base64
import hashlib
import json
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.filterwarnings("ignore")

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import api  # noqa: E402
import find_replace  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


DIR = os.path.join(HERE, "..", "integrations", "power-automate")
spec = json.load(open(os.path.join(DIR, "apiDefinition.swagger.json")))
props = json.load(open(os.path.join(DIR, "apiProperties.json")))

# ── the definition itself ────────────────────────────────────────────────────
check("OpenAPI 2.0 (custom connectors do not import 3.x)", spec.get("swagger") == "2.0")
check("https only", spec.get("schemes") == ["https"])
sec = spec.get("securityDefinitions", {})
check("one API-key header", len(sec) == 1 and all(v == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
                                                  for v in sec.values()), sec)
check("the connection parameter is the security definition",
      set(props["properties"]["connectionParameters"]) == set(sec), (props["properties"]["connectionParameters"], sec))
op = spec["paths"]["/v1/replace"]["post"]
check("operationId is a plain identifier", op["operationId"].isalnum(), op["operationId"])
check("summary has no trailing period (the designer shows it as the action name)",
      not op["summary"].endswith("."))

unlabelled = []


def walk(node, path, named=False):
    """Every NAMED field (a key under properties) needs a label and a description."""
    if named and (not node.get("x-ms-summary") or not node.get("description")):
        unlabelled.append(path)
    for name, sub in node.get("properties", {}).items():
        walk(sub, f"{path}.{name}", named=True)
    if "items" in node:
        walk(node["items"], f"{path}[]")


body = op["parameters"][0]["schema"]
walk(body, "body")
for code, r in op["responses"].items():
    if "schema" in r:
        walk(r["schema"], f"{code}")
check("every field has a label and a description", not unlabelled, unlabelled)

# ── against the server ───────────────────────────────────────────────────────
KEY = "connector-test-key-123456"
api.API_KEYS = [KEY.encode()]
client = TestClient(api.app)
pdf = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()

offered = set(body["properties"]["changes"]["items"]["properties"])
check("the connector offers every change field the server takes", offered == set(find_replace._KEYS),
      offered ^ set(find_replace._KEYS))
change = {"find": "Essaouira", "replace": "Marrakech", "page": 1, "first_only": False, "whole_word": True}
full = {"pdf": base64.b64encode(pdf).decode(), "filename": "attestation.pdf", "changes": [change], "strict": False}
check("the test fills every body field the connector offers", set(full) == set(body["properties"]))


def call(payload, key=KEY):
    return client.post("/v1/replace", json=payload, headers={"X-API-Key": key} if key else {})


r = call(full)
res = r.json()
check("every field filled: 200", r.status_code == 200, (r.status_code, str(res)[:200]))
resp = op["responses"]["200"]["schema"]["properties"]
check("the response's top level is as declared", set(res) == set(resp), set(res) ^ set(resp))
declared = set(resp["receipt"]["properties"])
check("every receipt field sent is declared", set(res["receipt"]) <= declared, set(res["receipt"]) - declared)
row_declared = set(resp["receipt"]["properties"]["changes"]["items"]["properties"])
check("every change-row field sent is declared", all(set(c) <= row_declared for c in res["receipt"]["changes"]),
      [set(c) - row_declared for c in res["receipt"]["changes"]])
check("the change was made and read back", res["receipt"]["ok"] and res["receipt"]["changes"][0].get("verified"),
      res["receipt"])
out = base64.b64decode(res["pdf"])
check("Edited PDF is a PDF, as base64 (what base64ToBinary expects)", out[:5] == b"%PDF-")
check("its checksum is the receipt's", hashlib.sha256(out).hexdigest() == res["receipt"]["sha256"]["after"])
fitz.open(stream=out, filetype="pdf")

missing = {"pdf": full["pdf"], "changes": [{"find": "text that is not in this document", "replace": "x"}]}
r = call(missing)
check("nothing changed: 200, Edited PDF empty, status unchanged",
      r.status_code == 200 and r.json()["pdf"] is None and r.json()["receipt"]["status"] == "unchanged", r.text[:200])
r = call(dict(missing, strict=True))
d422 = op["responses"]["422"]["schema"]["properties"]
check("strict and not every change made: 422 with the declared body",
      r.status_code == 422 and set(r.json()) == set(d422), (r.status_code, r.text[:200]))
check("no key: 401", call(full, key=None).status_code == 401)
check("a wrong key: 401", call(full, key=KEY + "x").status_code == 401)

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
