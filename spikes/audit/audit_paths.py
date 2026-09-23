"""The product paths beyond single-field editing: /bulk, /annex, overlays, /compose.

Needs the API running (uvicorn api:app --port 8000, from backend/).
RD_API points it elsewhere — that is how a fix is A/B'd against the commit
before it, with a second uvicorn on 8001.
"""
import base64
import json
import os
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from _common import API, CORPUS, norm_ws  # noqa: E402
import fitz  # noqa: E402
from api import extract_spans  # noqa: E402

import warnings  # noqa: E402
warnings.simplefilter("ignore")

TMP = os.path.join(os.path.dirname(CORPUS), "tmp")
os.makedirs(TMP, exist_ok=True)
FAIL = []


def post(endpoint, out, fields, timeout="300"):
    args = ["curl", "-s", "-o", out, "-w", "%{http_code}", "-m", timeout, "-X", "POST",
            API + endpoint]
    for f in fields:
        args += ["-F", f]
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── /bulk: every row carries ONLY its own values ────────────────────────────
print("=== /bulk row isolation ===")
d = fitz.open(); p = d.new_page(width=595, height=842); y = 120
for lab, val in (("Client name", "PLACEHOLDERNAME"), ("Reference", "PLACEHOLDERREF"),
                 ("Amount due", "PLACEHOLDERAMOUNT"), ("Due date", "PLACEHOLDERDATE")):
    p.insert_text((70, y), lab + ":", fontsize=12, fontname="helv")
    p.insert_text((220, y), val, fontsize=12, fontname="helv")
    y += 34
p.insert_text((70, y + 20), "This line is never mapped and must survive unchanged.",
              fontsize=10, fontname="helv")
tmpl = d.tobytes(); d.close()
open(os.path.join(TMP, "bulk_tmpl.pdf"), "wb").write(tmpl)
idx = {sp["text"].strip(): i for i, sp in enumerate(extract_spans(tmpl))
       if sp["text"].strip().startswith("PLACEHOLDER")}
N = 12
csv = "client,name,ref,amount,date\n"
for r in range(1, N + 1):
    # rows 5-8 cannot be filled: that is the case the historical leak lived in,
    # where a row was delivered carrying the template's placeholder instead.
    pad = "X" * 90 if 5 <= r <= 8 else ""
    csv += "R%03d,NAME%03dZZ%s,REF%03dZZ%s,AMT%03dZZ,DATE%03dZZ\n" % (r, r, pad, r, pad, r, r)
open(os.path.join(TMP, "bulk.csv"), "w").write(csv)
mapping = {str(idx["PLACEHOLDERNAME"]): "name", str(idx["PLACEHOLDERREF"]): "ref",
           str(idx["PLACEHOLDERAMOUNT"]): "amount", str(idx["PLACEHOLDERDATE"]): "date"}
code = post("/bulk", os.path.join(TMP, "bulk.zip"),
            ["template=@" + os.path.join(TMP, "bulk_tmpl.pdf"),
             "data=@" + os.path.join(TMP, "bulk.csv"),
             "mapping=" + json.dumps(mapping), "filename_col=client"])
check("/bulk responds 200", code == "200", "HTTP " + code)
if code == "200":
    zf = zipfile.ZipFile(os.path.join(TMP, "bulk.zip"))
    names = zf.namelist()
    print("  rows delivered: %d of %d" % (len(names), N))
    leaks = 0
    for nm in names:
        dd = fitz.open(stream=zf.read(nm), filetype="pdf")
        txt = dd[0].get_text(); dd.close()
        mine = next((r for r in range(1, N + 1) if "NAME%03dZZ" % r in txt), None)
        if mine is None or "PLACEHOLDER" in txt:
            leaks += 1
            continue
        for r in range(1, N + 1):
            if r != mine and any("%s%03dZZ" % (pre, r) in txt for pre in ("NAME", "REF", "AMT", "DATE")):
                leaks += 1
                break
        if "never mapped and must survive" not in txt:
            leaks += 1
    check("no delivered row carries another row's or the template's text", leaks == 0,
          "%d of %d" % (leaks, len(names)))
    check("unfillable rows are failed, not delivered broken", len(names) < N,
          "all %d delivered" % len(names))

# ── /annex: removal and recomputation, in BOTH number conventions ───────────
ITEMS = [("Prestation de conseil", 1200.00), ("Support technique", 950.00),
         ("Formation equipe", 600.00), ("Audit securite", 1500.00),
         ("Maintenance annuelle", 400.00), ("Licence logicielle", 250.00)]
ROWS = [("unchanged", [4, 12, 2, 3, 6, 10]), ("two_removed", [4, 0, 2, 0, 6, 10]),
        ("only_first", [7, 0, 0, 0, 0, 0]), ("all_rescaled", [1, 2, 3, 4, 5, 6]),
        ("all_removed", [0, 0, 0, 0, 0, 0]), ("blank_removes", [5, "", 1, "", 2, ""])]


def money(v, style):
    s = "{:,.2f}".format(v)
    return s if style == "en" else s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


for style in ("en", "eu"):
    print("=== /annex arithmetic (%s numbers) ===" % style)
    d = fitz.open(); p = d.new_page(width=595, height=842)
    p.insert_text((70, 80), "ANNEXE A LA FACTURE", fontsize=16, fontname="hebo")
    p.insert_text((70, 108), "Facture N: FA-2024-0188", fontsize=11, fontname="helv")
    y = 190
    for h, x in (("Designation", 70), ("Qte", 300), ("PU HT", 370), ("Montant HT", 460)):
        p.insert_text((x, y), h, fontsize=10, fontname="hebo")
    y += 8; p.draw_line(fitz.Point(70, y), fitz.Point(540, y)); y += 20
    total = 0.0
    for name, pu in ITEMS:
        q = dict(zip([i[0] for i in ITEMS], ROWS[0][1]))[name]
        amt = q * pu; total += amt
        p.insert_text((70, y), name, fontsize=10, fontname="helv")
        p.insert_text((300, y), str(q), fontsize=10, fontname="helv")
        p.insert_text((370, y), money(pu, style), fontsize=10, fontname="helv")
        p.insert_text((460, y), money(amt, style), fontsize=10, fontname="helv")
        y += 22
    y += 6; p.draw_line(fitz.Point(70, y), fitz.Point(540, y)); y += 22
    p.insert_text((370, y), "Total HT", fontsize=11, fontname="hebo")
    p.insert_text((460, y), money(total, style), fontsize=11, fontname="hebo")
    ann = os.path.join(TMP, "annex_%s.pdf" % style)
    d.save(ann); d.close()
    csv = "client," + ",".join("q%d" % i for i in range(len(ITEMS))) + "\n"
    for nm, qs in ROWS:
        csv += nm + "," + ",".join(str(q) for q in qs) + "\n"
    open(os.path.join(TMP, "annex.csv"), "w").write(csv)
    code = post("/annex/generate", os.path.join(TMP, "annex_%s.zip" % style),
                ["template=@" + ann, "data=@" + os.path.join(TMP, "annex.csv"),
                 "mapping=" + json.dumps({str(i): "q%d" % i for i in range(len(ITEMS))}),
                 "filename_col=client"])
    check("/annex/generate responds 200 (%s)" % style, code == "200", "HTTP " + code)
    if code != "200":
        continue
    zf = zipfile.ZipFile(os.path.join(TMP, "annex_%s.zip" % style))
    want = {nm: qs for nm, qs in ROWS}
    wrong = 0
    for nm in zf.namelist():
        qs = want.get(nm.rsplit(".", 1)[0])       # pair by FILENAME: the zip is sorted
        if qs is None:
            wrong += 1
            continue
        dd = fitz.open(stream=zf.read(nm), filetype="pdf")
        txt = dd[0].get_text(); dd.close()
        expect = sum((0 if q in ("", 0) else int(q)) * pu for q, (_, pu) in zip(qs, ITEMS))
        problems = []
        for (item, pu), q in zip(ITEMS, qs):
            gone = q in ("", 0)
            if gone != (item not in txt):
                problems.append(item)
            if not gone and money(int(q) * pu, style) not in txt:
                problems.append(item + " amount")
        if money(expect, style) not in txt:
            problems.append("Total HT should be " + money(expect, style))
        if problems:
            wrong += 1
            print("    %-14s %s" % (nm, problems[:3]))
    check("every annex removes the right lines and totals correctly (%s)" % style, wrong == 0,
          "%d wrong" % wrong)

# ── overlays: added text lands where asked, disturbs nothing ────────────────
print("=== overlays ===")
d = fitz.open(); p = d.new_page(width=595, height=842)
for yy in (100, 400, 800):
    p.insert_text((70, yy), "Existing line at %d, must not move." % yy, fontsize=12, fontname="helv")
ovl = os.path.join(TMP, "ovl.pdf"); d.save(ovl); d.close()
before = _common.page_facts(open(ovl, "rb").read(), 0)
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
STAMPS = [{"kind": "text", "page": 0, "x": 300, "y": 200, "text": "STAMPTOP", "size": 14},
          {"kind": "text", "page": 0, "x": 120, "y": 600, "text": "STAMPMID", "size": 14},
          {"kind": "sign", "page": 0, "x": 350, "y": 700, "w": 120, "h": 50,
           "data": "data:image/png;base64," + base64.b64encode(png).decode()}]
open(os.path.join(TMP, "stamps.json"), "w").write(json.dumps(STAMPS))
code = post("/edit", os.path.join(TMP, "ovl_out.pdf"),
            ["file=@" + ovl, "edits=[]", "stamps=<" + os.path.join(TMP, "stamps.json"), "final=1"])
check("/edit with stamps responds 200", code == "200", "HTTP " + code)
if code == "200":
    dd = fitz.open(os.path.join(TMP, "ovl_out.pdf"))
    pg = dd[0]
    boxes = {}
    for b in pg.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                t = "".join(c["c"] for c in sp["chars"]).strip()
                if t:
                    boxes[t[:20]] = [round(v, 1) for v in sp["bbox"]]
    nimg = len(pg.get_images(full=True))
    dd.close()
    off = []
    for st in STAMPS:
        if st["kind"] != "text":
            continue
        bb = boxes.get(st["text"])
        if bb is None or abs(bb[0] - st["x"]) > 3.0 or abs(bb[1] - st["y"]) > 6.0:
            off.append(st["text"])
    check("every text stamp lands where it was asked", not off, str(off))
    check("the signature image is embedded", nimg == 1, "%d images" % nimg)
    after = _common.page_facts(open(os.path.join(TMP, "ovl_out.pdf"), "rb").read(), 0)
    kept = all(norm_ws(k) in after["text"] for k in ("Existing line at 100", "Existing line at 400"))
    check("original lines survive the stamp", kept)

# ── /compose: does pasted text come back whole? ─────────────────────────────
print("=== /compose ===")
CASES = [("plain letter", "Dear Ms Bouzidi,\n\nThis letter confirms your appointment.\n\nYours sincerely,\nK. El Amrani"),
         ("accented", "Attestation de travail\n\nJe soussigné, Karim El Amrani, certifie que Madame Salma Bouzidi, née le 03/11/1994 à Marrakech."),
         ("long word", "Reference " + "W" * 300),
         ("many lines", "\n".join("Line %d of the document." % i for i in range(1, 61))),
         ("parens", "Amount (net) is 1,200.00 ) and ( more"),
         ("unicode", "Ünïcödé — em dash, ‘quotes’, “double”, … ellipsis")]
bad = 0
for name, text in CASES:
    open(os.path.join(TMP, "compose.txt"), "w").write(text)
    code = post("/compose", os.path.join(TMP, "compose.pdf"),
                ["text=<" + os.path.join(TMP, "compose.txt"), "title=Audit"])
    if code != "200":
        print("  %-14s HTTP %s" % (name, code)); bad += 1; continue
    dd = fitz.open(os.path.join(TMP, "compose.pdf"))
    got = norm_ws("".join(pg.get_text() for pg in dd))
    nd = sum(1 for pg in dd for sp in pg.get_texttrace() for c in sp["chars"]
             if c[1] == 0 and not chr(c[0]).isspace())
    dd.close()
    lost = None
    for line in [l.strip() for l in text.split("\n") if l.strip()]:
        probe = norm_ws(line[:40])
        if len(probe) > 6 and probe not in got:
            lost = probe[:24]
            break
    if lost or nd:
        bad += 1
        print("  %-14s lost=%r notdef=%d" % (name, lost, nd))
check("every composed document keeps its text and draws every glyph", bad == 0,
      "%d of %d" % (bad, len(CASES)))

print("\n" + "=" * 70)
if FAIL:
    print("RESULT: %d FAILED -> %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("RESULT: ALL PASS")
