"""Stress: adversarial replacement text, repeated edits, and scale.

Three things the corpus sweep cannot see:
  * text a user could paste that is hostile to a PDF content stream
  * editing the same field again and again, each round on an already-edited doc
  * a 100-page document and a 500-row bulk run
"""
import json
import os
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from _common import API, CORPUS, EXAMPLES, corpus_docs, first_inplace_span, notdef_glyphs  # noqa: E402
import fitz  # noqa: E402
from api import extract_spans, apply_replacements  # noqa: E402

import warnings  # noqa: E402
warnings.simplefilter("ignore")

TMP = os.path.join(os.path.dirname(CORPUS), "tmp")
os.makedirs(TMP, exist_ok=True)
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── adversarial text ────────────────────────────────────────────────────────
# A PDF string literal is delimited by parentheses and escaped with
# backslashes, so a replacement containing ')' spliced in unescaped would close
# the literal early and have the rest read as OPERATORS.
INJECTION = ") Tj ET BT /F1 40 Tf 100 700 Td (INJECTED"
CASES = [
    ("plain control", "Ordinary replacement"),
    ("close paren", "Total ) due"),
    ("balanced parens", "Total (net) due"),
    ("backslash", "Path C:\\Users\\test"),
    ("stream operators", ") Tj 1 0 0 RG 0 0 500 500 re f ("),
    ("BT ET injection", INJECTION),
    ("q Q imbalance", ") Tj Q Q Q q q q ("),
    ("nul byte", "Before\x00After"),
    ("crlf", "Line one\r\nLine two"),
    ("zero width", "In\u200bvis\u200bible"),
    ("bidi override", "abc\u202edef\u202c"),
    ("combining marks", "e\u0301a\u0300i\u0302"),
    ("emoji astral", "Invoice \U0001F600 done"),
    ("empty", ""),
    ("very long", "Wxqzkj " * 1400),
    ("high unicode", "日本語テキスト"),
]
print("=== adversarial replacement text ===")
demo = os.path.join(EXAMPLES, "attestation-demo.pdf")
data = open(demo, "rb").read()
spans = [s for s in extract_spans(data)
         if len(s["text"].strip()) >= 6 and s.get("size", 0) > 5]
# A field that ACTUALLY edits in place, or every case comes back "refused" for
# reasons having nothing to do with the text under test.
sd = first_inplace_span(data, spans)
check("found a field that edits in place to test against", sd is not None)
crashed, broken, injected = [], [], []
if sd is not None:
    base = _common.page_facts(data, sd.get("page", 0))
    for name, new in CASES:
        try:
            out, rep = apply_replacements(data, [(sd, new)], preserve_size=True, try_inplace=True)
        except Exception as exc:  # noqa: BLE001
            crashed.append("%s (%s)" % (name, type(exc).__name__))
            continue
        try:
            d = fitz.open(stream=out, filetype="pdf")
            pg = d[sd.get("page", 0)]
            txt = pg.get_text()
            pg.get_pixmap(dpi=36)          # a torn content stream fails here
            ndraw = len(pg.get_drawings())
            d.close()
        except Exception as exc:  # noqa: BLE001
            broken.append("%s (%s)" % (name, type(exc).__name__))
            continue
        if name == "BT ET injection" and rep["in_place"]["count"]:
            # the payload appearing AS TEXT is the correct outcome: it was
            # escaped and drawn, not executed
            if INJECTION not in txt:
                injected.append(name)
    base_draw = len(fitz.open(demo)[0].get_drawings())
    d = fitz.open(stream=apply_replacements(data, [(sd, ") Tj 1 0 0 RG 0 0 500 500 re f (")],
                                            preserve_size=True, try_inplace=True)[0],
                  filetype="pdf")
    after_draw = len(d[0].get_drawings())
    # the payload's 500x500 rectangle, if it were executed
    painted = any(dr["rect"].width > 400 and dr["rect"].height > 400 for dr in d[0].get_drawings())
    d.close()
    check("no adversarial input crashes the engine", not crashed, str(crashed[:3]))
    check("every output still opens and renders", not broken, str(broken[:3]))
    check("the injection payload is drawn as text, never executed", not injected, str(injected))
    # No drawing ADDED. One may go: when the payload is redrawn, the field's
    # own link underline goes with the text it underlined.
    check("an injected 're f' paints no rectangle", after_draw <= base_draw and not painted,
          "%d -> %d drawings" % (base_draw, after_draw))

# ── successive edits to the same field ──────────────────────────────────────
print("=== successive edits ===")
VALUES = ["Alpha One", "Beta Two", "Gamma Three", "Delta Four"]
docs = corpus_docs()
bad_docs = []
tested = 0
for label, path in docs[:14]:
    data = open(path, "rb").read()
    try:
        spans = [s for s in extract_spans(data)
                 if len(s["text"].strip()) >= 8 and s.get("size", 0) > 5]
    except Exception:  # noqa: BLE001
        continue
    sd = first_inplace_span(data, spans)
    if sd is None:
        continue
    tested += 1
    cur, problems = data, []
    for i, v in enumerate(VALUES):
        # Track the field by CONTENT. A centred field legitimately moves when
        # its text shortens — 58pt on a LibreOffice table — so matching on
        # geometry calls a working edit "field vanished".
        want = VALUES[i - 1] if i else sd["text"].strip()
        match = [s for s in extract_spans(cur)
                 if s.get("page") == sd.get("page") and want[:12] in s["text"]]
        if not match:
            problems.append("round %d: field vanished" % i)
            break
        cur, _rep = apply_replacements(cur, [(match[0], v)], preserve_size=True, try_inplace=True)
        d = fitz.open(stream=cur, filetype="pdf")
        txt = d[sd.get("page", 0)].get_text()
        nd = notdef_glyphs(d, sd.get("page", 0), v[:5])
        d.close()
        if v not in txt:
            problems.append("round %d: %r missing" % (i, v))
        if nd:
            problems.append("round %d: notdef %s" % (i, nd[:3]))
        stale = [o for o in VALUES[:i] if o in txt]
        if stale:
            problems.append("round %d: stale %r" % (i, stale[0]))
    if problems:
        bad_docs.append((label, problems[:2]))
check("four successive edits leave only the final value", not bad_docs, str(bad_docs[:3]))
print("  documents exercised: %d" % tested)

# ── scale ───────────────────────────────────────────────────────────────────
print("=== scale ===")
d = fitz.open()
for n in range(1, 101):
    p = d.new_page(width=595, height=842)
    p.insert_text((60, 110), "Section %d: Operating covenants" % n, fontsize=15, fontname="hebo")
    for i in range(22):
        p.insert_text((60, 150 + i * 28),
                      "%d.%d  Amount payable is %s.00 EUR, reference R%03d%02d."
                      % (n, i + 1, format(1000 * n + i * 37, ","), n, i),
                      fontsize=9, fontname="helv")
big = os.path.join(TMP, "bigdoc.pdf"); d.save(big); d.close()
data = open(big, "rb").read()
t = time.time(); spans = extract_spans(data); t_ex = time.time() - t
print("  100 pages: %d spans extracted in %.1fs" % (len(spans), t_ex))
ok = 0
targets = []
for want in (0, 50, 99):
    c = [s for s in spans if s.get("page") == want and len(s["text"].strip()) >= 10]
    if c:
        targets.append(c[0])
for sd in targets:
    out, _r = apply_replacements(data, [(sd, sd["text"].strip()[:20] + " EDITEDZZ")],
                                 preserve_size=True, try_inplace=True)
    d = fitz.open(stream=out, filetype="pdf")
    good = ("EDITEDZZ" in d[sd["page"]].get_text() and d.page_count == 100
            and not notdef_glyphs(d, sd["page"], "EDITEDZZ"))
    d.close()
    ok += good
check("editing page 1, 50 and 100 of a 100-page document is clean", ok == len(targets),
      "%d of %d" % (ok, len(targets)))

tmpl = os.path.join(TMP, "bulk_tmpl.pdf")
if os.path.exists(tmpl):
    idx = {sp["text"].strip(): i for i, sp in enumerate(extract_spans(open(tmpl, "rb").read()))
           if sp["text"].strip().startswith("PLACEHOLDER")}
    csv = "client,name,ref,amount,date\n" + "".join(
        "C%04d,NAME%04dZZ,REF%04dZZ,AMT%04dZZ,DATE%04dZZ\n" % (r, r, r, r, r)
        for r in range(1, 501))
    open(os.path.join(TMP, "bulk500.csv"), "w").write(csv)
    mapping = {str(idx["PLACEHOLDERNAME"]): "name", str(idx["PLACEHOLDERREF"]): "ref",
               str(idx["PLACEHOLDERAMOUNT"]): "amount", str(idx["PLACEHOLDERDATE"]): "date"}
    t = time.time()
    code = subprocess.run(
        ["curl", "-s", "-o", os.path.join(TMP, "bulk500.zip"), "-w", "%{http_code}", "-m", "1500",
         "-X", "POST", API + "/bulk", "-F", "template=@" + tmpl,
         "-F", "data=@" + os.path.join(TMP, "bulk500.csv"),
         "-F", "mapping=" + json.dumps(mapping), "-F", "filename_col=client"],
        capture_output=True, text=True).stdout.strip()
    el = time.time() - t
    if code == "200":
        zf = zipfile.ZipFile(os.path.join(TMP, "bulk500.zip"))
        names = zf.namelist()
        print("  500 rows in %.1fs (%.3fs/row), delivered %d" % (el, el / max(1, len(names)), len(names)))
        import random
        wrong = 0
        for nm in random.Random(3).sample(names, min(25, len(names))):
            dd = fitz.open(stream=zf.read(nm), filetype="pdf")
            txt = dd[0].get_text(); dd.close()
            num = "".join(ch for ch in nm.rsplit(".", 1)[0] if ch.isdigit())
            if "NAME%sZZ" % num not in txt or "PLACEHOLDER" in txt:
                wrong += 1
        check("500 bulk rows deliver and carry their own values", len(names) == 500 and wrong == 0,
              "%d delivered, %d sampled wrong" % (len(names), wrong))
    else:
        check("500-row bulk responds 200", False, "HTTP " + code)
else:
    print("  (run audit_paths.py first for the bulk template)")

print("\n" + "=" * 70)
if FAIL:
    print("RESULT: %d FAILED -> %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("RESULT: ALL PASS")
