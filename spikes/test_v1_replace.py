"""test_v1_replace.py — POST /v1/replace: find the text, change it, say exactly what happened.

The route is the one door every integration (a Power Automate connector, a watched folder,
a script) goes through, so what it REPORTS matters as much as what it edits:

  * a receipt that says "ok" when the page did not change is worse than an error — the
    "known-bad build" checks below stub the engine into doing nothing / the wrong thing and
    require the receipt to notice;
  * a text that is only part of a longer number ("240.00" inside "1,240.00") must not be
    changed;
  * every find is matched against what the document HAS, never against another change's
    output (A->B with B->C must not turn A into C);
  * a PDF is returned only if something changed, so an untouched document is never mistaken
    for a corrected one.

Fixtures are built here or are the fictional files in examples/ — nothing is downloaded.
"""
import base64
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
import find_replace as fr  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def spans(*texts, page=0):
    return [{"page": page, "text": t} for t in texts]


def raises_bad(raw):
    try:
        fr.parse_changes(raw)
    except fr.BadRequest:
        return True
    return False


def edits_of(sp, changes):
    return fr.plan(sp, fr.parse_changes(changes))["edits"]


# ── 1. what a request may contain ─────────────────────────────────────────────
check("an empty list is refused", raises_bad([]))
check("a non-list is refused", raises_bad({"find": "a", "replace": "b"}))
check("a blank find is refused", raises_bad([{"find": "  ", "replace": "b"}]))
check("a missing replace is refused", raises_bad([{"find": "a"}]))
check("a misspelt option is refused, not ignored", raises_bad([{"find": "a", "replace": "b", "pages": 2}]))
check("page 0 is refused", raises_bad([{"find": "a", "replace": "b", "page": 0}]))
check("too many changes are refused", raises_bad([{"find": "a", "replace": "b"}] * (fr.MAX_CHANGES + 1)))
check("an empty replace is allowed (delete)", not raises_bad([{"find": "a", "replace": ""}]))

# ── 2. what counts as a match ─────────────────────────────────────────────────
e = edits_of(spans("Total 1,240.00", "Total 240.00"), [{"find": "240.00", "replace": "540.00"}])
check("240.00 is not found inside 1,240.00", e == {1: "Total 540.00"}, e)
check("3,840 is not found inside 3,840.00",
      edits_of(spans("Total 3,840.00"), [{"find": "3,840", "replace": "4,140"}]) == {})
check("...unless whole_word is turned off",
      edits_of(spans("Total 3,840.00"), [{"find": "3,840", "replace": "4,140", "whole_word": False}])
      == {0: "Total 4,140.00"})
check("Inc is not found inside Incorporated",
      edits_of(spans("Acme Incorporated"), [{"find": "Inc", "replace": "Ltd"}]) == {})
check("Inc is found in 'Acme Inc.'",
      edits_of(spans("Acme Inc."), [{"find": "Inc", "replace": "Ltd"}]) == {0: "Acme Ltd."})
check("a number at a sentence end is found",
      edits_of(spans("pay 240.00."), [{"find": "240.00", "replace": "540.00"}]) == {0: "pay 540.00."})
check("a space typed matches a no-break or doubled space",
      edits_of(spans("Medina  Holdings SARL"), [{"find": "Medina Holdings", "replace": "Atlas"}])
      == {0: "Atlas SARL"})
check("a straight apostrophe finds a curly one",
      edits_of(spans("Mr O’Brien"), [{"find": "O'Brien", "replace": "Smith"}]) == {0: "Mr Smith"})
check("a curly apostrophe finds a straight one",
      edits_of(spans("Mr O'Brien"), [{"find": "O’Brien", "replace": "Smith"}]) == {0: "Mr Smith"})
check("matching is case-sensitive",
      edits_of(spans("medina"), [{"find": "Medina", "replace": "Atlas"}]) == {})
e = edits_of(spans("a 1 b 1"), [{"find": "1", "replace": "2"}])
check("every occurrence in a field is changed", e == {0: "a 2 b 2"}, e)
sp2 = spans("Medina SARL") + spans("Medina Ltd", page=1)
check("first_only changes only the first match",
      edits_of(sp2, [{"find": "Medina", "replace": "Atlas", "first_only": True}]) == {0: "Atlas SARL"})
check("page limits the search to that page (1 = first)",
      edits_of(sp2, [{"find": "Medina", "replace": "Atlas", "page": 2}]) == {1: "Atlas Ltd"})

# ── 3. changes do not feed each other ─────────────────────────────────────────
e = edits_of(spans("Alpha", "Beta"), [{"find": "Alpha", "replace": "Beta"}, {"find": "Beta", "replace": "Gamma"}])
check("A->B with B->C does not turn A into C", e == {0: "Beta", 1: "Gamma"}, e)
pl = fr.plan(spans("Medina Holdings SARL"), fr.parse_changes(
    [{"find": "Medina Holdings", "replace": "X"}, {"find": "Holdings SARL", "replace": "Y"}]))
check("two changes on the same words: the first wins, the second says so",
      pl["edits"] == {0: "X SARL"} and [m["status"] for m in pl["matches"]] == ["pending", "unchanged"]
      and pl["matches"][1]["reason"] == "overlaps_another_change", pl)

# ── 4. why a text was not found ───────────────────────────────────────────────
def why(sp, find, **kw):
    ch = fr.parse_changes([{"find": find, "replace": "x", **kw}])
    return fr.plan(sp, ch)["missing"].get(0)


check("not found: it is there in another case", why(spans("medina"), "Medina") == "case_differs")
check("not found: it is only inside a longer word",
      why(spans("Acme Incorporated"), "Inc") == "only_inside_a_longer_word_or_number")
check("not found: it runs over two lines",
      why(spans("Medina Holdings", "SARL"), "Holdings SARL") == "spans_several_lines")
check("not found: it is not in the document", why(spans("Medina"), "Atlas") == "absent")

# ── 5. the receipt cannot say ok about a page that does not show the change ───
ch1 = fr.parse_changes([{"find": "Medina", "replace": "Atlas"}])
p1 = fr.plan(spans("Medina SARL"), ch1)
fr.settle(p1, spans("Medina SARL"), {}, set())
good = fr.receipt(ch1, p1, {}, lambda pg, t, before=False: 1, {})
check("reads back -> ok", good["status"] == "ok" and good["changes"][0]["verified"] is True, good)
p1b = fr.plan(spans("Medina SARL"), ch1)
fr.settle(p1b, spans("Medina SARL"), {}, set())
bad = fr.receipt(ch1, p1b, {}, lambda pg, t, before=False: 0, {})
check("does NOT read back -> not ok", bad["ok"] is False and bad["changes"][0]["verified"] is False, bad)

# ── 6. through the real route ─────────────────────────────────────────────────
client = TestClient(api.app)
EX = os.path.join(HERE, "..", "examples")
INV = open(os.path.join(EX, "invoice-type3.pdf"), "rb").read()


def norm_pages(pdf):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return [" ".join(pg.get_text().split()) for pg in d]
    finally:
        d.close()


def call(pdf, changes, **form):
    return client.post("/v1/replace", files={"file": ("t.pdf", pdf)},
                       data={"changes": json.dumps(changes), **form})


def mk(pages):
    d = fitz.open()
    for items in pages:
        p = d.new_page(width=595, height=842)
        for (x, y, t) in items:
            p.insert_text((x, y), t, fontsize=12, fontname="helv")
    b = d.tobytes()
    d.close()
    return b


INV_CH = [{"find": "Medina Studio SARL", "replace": "Atlas Digital SARL"},
          {"find": "240.00", "replace": "540.00"},
          {"find": "3,840.00", "replace": "4,140.00"}]
r = call(INV, INV_CH)
j = r.json()
rc = j["receipt"]
check("invoice: 200 with a PDF and an ok receipt",
      r.status_code == 200 and j["pdf"] and rc["ok"] and rc["status"] == "ok" and rc["edited"] == 3, rc)
check("invoice: each change found once, edited once, read back",
      all(c["found"] == 1 and c["edited"] == 1 and c["verified"] is True for c in rc["changes"]), rc["changes"])
before_t = norm_pages(INV)[0]
want = before_t
for c in INV_CH:
    want = want.replace(c["find"], c["replace"])
out = base64.b64decode(j["pdf"])
check("invoice: the page reads as the original with exactly those three changes",
      norm_pages(out)[0] == want, (norm_pages(out)[0][:200], want[:200]))
check("invoice: receipt hashes are the real ones",
      rc["sha256"]["before"] == __import__("hashlib").sha256(INV).hexdigest()
      and rc["sha256"]["after"] == __import__("hashlib").sha256(out).hexdigest())

rj = client.post("/v1/replace", json={"pdf": base64.b64encode(INV).decode(), "changes": INV_CH})
check("the JSON door gives the same answer as the multipart door",
      rj.status_code == 200 and rj.json()["pdf"] == j["pdf"], rj.status_code)

rp = client.post("/v1/replace", files={"file": ("t.pdf", INV)}, data={"changes": json.dumps(INV_CH)},
                 headers={"Accept": "application/pdf"})
hdr = json.loads(base64.b64decode(rp.headers.get("x-redraft-receipt", "e30=")))
check("Accept: application/pdf returns the PDF itself with the receipt in a header",
      rp.status_code == 200 and rp.headers["content-type"] == "application/pdf"
      and rp.content == out and hdr.get("status") == "ok", (rp.status_code, hdr.get("status")))

# a text that is not there: the rest still happens, and the receipt says which one failed
r = call(INV, INV_CH + [{"find": "Nonexistent Corp", "replace": "X"}])
j, rc = r.json(), r.json()["receipt"]
check("one text missing: partial, not ok, the other three still made",
      r.status_code == 200 and rc["status"] == "partial" and rc["ok"] is False and rc["edited"] == 3
      and j["pdf"], rc["status"])
check("...and it names the one that was not found",
      rc["changes"][3]["found"] == 0 and rc["changes"][3]["why_not_found"] == "absent", rc["changes"][3])
r = call(INV, INV_CH + [{"find": "Nonexistent Corp", "replace": "X"}], strict="true")
check("strict: 422, no PDF, receipt says why",
      r.status_code == 422 and "pdf" not in r.json() and r.json()["receipt"]["status"] == "partial", r.status_code)

# nothing to change: no PDF is handed back to be mistaken for a corrected one
r = call(INV, [{"find": "Nonexistent Corp", "replace": "X"}])
check("nothing found: 200, pdf is null, status unchanged",
      r.status_code == 200 and r.json()["pdf"] is None and r.json()["receipt"]["status"] == "unchanged", r.json())
r = client.post("/v1/replace", files={"file": ("t.pdf", INV)},
                data={"changes": json.dumps([{"find": "Nonexistent Corp", "replace": "X"}])},
                headers={"Accept": "application/pdf"})
check("nothing found, PDF asked for: 422 with the receipt", r.status_code == 422 and "receipt" in r.json())

# a value that cannot be placed: left as it was and SAID so
two_col = mk([[(70, 100, "Name: Sara"), (300, 100, "Date: 12 May 2026")]])
long_name = "Maximiliana Alexandrina Bartholomew Montgomery-Fitzgerald the Third of Westchester"
r = call(two_col, [{"find": "Sara", "replace": long_name}])
rc = r.json()["receipt"]
pr = (rc["changes"][0].get("problems") or [{}])[0]
check("too long for its place: nothing changed, reason given, no PDF",
      r.status_code == 200 and r.json()["pdf"] is None and rc["status"] == "unchanged"
      and rc["changes"][0]["edited"] == 0 and pr.get("reason") in api._UNSHIPPABLE_MSG, rc)

# deletion, two pages
sentence = mk([[(70, 100, "Dear valued customer,")]])
r = call(sentence, [{"find": "valued ", "replace": ""}])
rc = r.json()["receipt"]
check("deleting a word: ok and the word is gone",
      rc["status"] == "ok" and rc["changes"][0]["verified"] is True
      and norm_pages(base64.b64decode(r.json()["pdf"]))[0] == "Dear customer,", rc)
pages2 = mk([[(70, 100, "Client: Medina Holdings SARL")], [(70, 100, "Signed for Medina Holdings SARL by CEO")]])
r = call(pages2, [{"find": "Medina Holdings SARL", "replace": "Atlas Digital SARL"}])
rc = r.json()["receipt"]
t = norm_pages(base64.b64decode(r.json()["pdf"]))
check("the same text on two pages: both changed, both read back",
      rc["changes"][0]["found"] == 2 and rc["changes"][0]["edited"] == 2 and rc["changes"][0]["verified"] is True
      and t == ["Client: Atlas Digital SARL", "Signed for Atlas Digital SARL by CEO"], (rc["changes"][0], t))
r = call(pages2, [{"find": "Medina Holdings SARL", "replace": "Atlas Digital SARL", "page": 2}])
t = norm_pages(base64.b64decode(r.json()["pdf"]))
check("page: 2 leaves page 1 alone", t == ["Client: Medina Holdings SARL", "Signed for Atlas Digital SARL by CEO"], t)
r = call(pdf := mk([[(70, 100, "Alpha"), (70, 130, "Beta")]]),
         [{"find": "Alpha", "replace": "Beta"}, {"find": "Beta", "replace": "Gamma"}])
check("through the route too: A->B, B->C does not chain",
      norm_pages(base64.b64decode(r.json()["pdf"])) == ["Beta Gamma"], r.json()["receipt"])

# bad requests are refused with a reason, never a 500
check("unknown option -> 400", call(INV, [{"find": "a", "replace": "b", "pages": 1}]).status_code == 400)
check("empty changes -> 400", call(INV, []).status_code == 400)
check("changes that are not JSON -> 400",
      client.post("/v1/replace", files={"file": ("t.pdf", INV)}, data={"changes": "{nope"}).status_code == 400)
check("no file -> 400", client.post("/v1/replace", files={"changes": (None, "[]")}).status_code == 400)
check("not a PDF -> 400", call(b"hello", [{"find": "a", "replace": "b"}]).status_code == 400)
check("bad base64 -> 400",
      client.post("/v1/replace", json={"pdf": "!!!", "changes": INV_CH}).status_code == 400)
check("another content type -> 415",
      client.post("/v1/replace", content=b"x", headers={"content-type": "text/plain"}).status_code == 415)

# ── 7. known-bad builds: the receipt must notice ──────────────────────────────
real = api.apply_replacements
try:
    api.apply_replacements = lambda pdf, reps, **kw: (pdf, {"in_place": {"refusals": []}, "warnings": []})
    r = call(INV, INV_CH)
    rc = r.json()["receipt"]
    check("an engine that changes nothing but says it did: not ok, no PDF",
          rc["ok"] is False and rc["status"] == "unchanged" and r.json()["pdf"] is None, rc["status"])

    api.apply_replacements = lambda pdf, reps, **kw: real(pdf, [(sd, "ZZZ") for sd, _ in reps], **kw)
    r = call(INV, INV_CH)
    rc = r.json()["receipt"]
    check("an engine that writes the wrong text: the read-back says so",
          rc["ok"] is False and any(c.get("verified") is False for c in rc["changes"]), rc["status"])
    r = call(INV, INV_CH, strict="true")
    check("...and strict turns that into 422", r.status_code == 422)
finally:
    api.apply_replacements = real

# ── 8. who may call it, and how it is described ───────────────────────────────
was = api.AUTH_ON
api.AUTH_ON = True
try:
    check("with accounts on, no token -> 401", call(INV, INV_CH).status_code == 401)
finally:
    api.AUTH_ON = was

spec = client.get("/openapi.json").json()
op = spec["paths"].get("/v1/replace", {}).get("post", {})
body = op.get("requestBody", {}).get("content", {})
check("the OpenAPI file describes both ways in",
      "application/json" in body and "multipart/form-data" in body, list(body))
check("...and the changes list", "find" in json.dumps(body.get("application/json", {})) and
      "first_only" in json.dumps(body.get("application/json", {})))
check("...and the answers", {"200", "422"} <= set(op.get("responses", {})), list(op.get("responses", {})))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
