"""test_reflow.py — re-wrapping a paragraph the way its author's software would.

Acceptance is the producer twin: the same change made in the SOURCE and
re-typeset by LibreOffice. The re-wrapped PDF must break its lines where the
producer did, move what follows by the same amount, and put every word
within half a point of where the producer put it.

Needs the audit corpus (spikes/audit/build_corpus.py); the twin check also
needs LibreOffice. Skips what it cannot run.
"""
import os
import shutil
import subprocess
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
import reflow  # noqa: E402

FAIL = []
CORPUS = os.path.expanduser("~/.cache/redraft-audit/corpus")
SRC = os.path.join(os.path.dirname(CORPUS), "src")


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def lines_of(pdf):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return [l for l in d[0].get_text().splitlines() if l.strip()]
    finally:
        d.close()


letter = os.path.join(CORPUS, "letter.pdf")
if not os.path.exists(letter):
    print("SKIP - audit corpus not built")
    sys.exit(0)

raw = open(letter, "rb").read()
sd = next(s for s in api.extract_spans(raw) if "Karim El Amrani" in s["text"])
NEW = "Karim Mohammed El Amrani Benjelloun"
out, rep = api.apply_replacements(raw, [(sd, NEW)], try_inplace=True)
rf = rep["in_place"].get("reflowed") or []
check("a name too long for its line re-wraps its paragraph instead of shrinking",
      rf and rf[0]["lines"] == [2, 3] and not rep["in_place"]["refusals"], str(rep["in_place"]))
ls = lines_of(out)
check("the new lines break where a word processor would",
      ls[2:5] == ["Je soussigné, Karim Mohammed El Amrani Benjelloun, certifie que Madame ",
                  "Salma Bouzidi, née le 03/11/1994 à Marrakech, CIN BK447120, est employée ",
                  "depuis 2021."], str(ls[2:5]))
check("reading order is preserved (the next paragraph still comes after it)",
      ls[-1].startswith("Son salaire"), str(ls[-1]))

d0 = fitz.open(stream=raw, filetype="pdf")
d1 = fitz.open(stream=out, filetype="pdf")
y_before = next(w[1] for w in d0[0].get_text("words") if w[4] == "salaire")
y_after = next(w[1] for w in d1[0].get_text("words") if w[4] == "salaire")
check("what follows moves down exactly one leading",
      abs((y_after - y_before) - rf[0]["shift"]) < 0.05 and abs(rf[0]["shift"] - 22.4) < 0.05,
      f"{y_after - y_before:.3f}")
nd = sum(1 for sp in d1[0].get_texttrace() for g in sp["chars"] if g[1] == 0)
check("the bold letters the subset lacked were injected, not boxed", nd == 0, f"{nd} notdef")
check("no font was added to the document",
      len(d1[0].get_fonts()) >= len(d0[0].get_fonts()) and
      {f[3].split("+")[-1] for f in d1[0].get_fonts()} == {f[3].split("+")[-1] for f in d0[0].get_fonts()},
      str([f[3] for f in d1[0].get_fonts()]))
d0.close()
d1.close()

# ── refusals: nothing to measure a margin against, or a layout it can't model ──
for doc, txt in (("irs-1040.pdf", "Line 3a"), ("nasa-tm.pdf", "CALIF.,")):
    p = os.path.join(CORPUS, doc)
    if not os.path.exists(p):
        continue
    r0 = open(p, "rb").read()
    s0 = next(s for s in api.extract_spans(r0) if s["text"].strip() == txt)
    r = reflow.reflow(r0, s0, s0["text"].strip() + " Wxqzkj Wxqzkj Wxqzkj Wxqzkj Wxqzkj")
    check(f"{doc}: a one-line field with no established margin is refused",
          not r["ok"] and r["reason"] == "unknown_layout", str({k: v for k, v in r.items() if k != "pdf"}))
p = os.path.join(CORPUS, "arxiv-latex.pdf")
if os.path.exists(p):
    r0 = open(p, "rb").read()
    sp = [s for s in api.extract_spans(r0) if len(s["text"]) > 60 and s.get("page", 0) == 1]
    if sp:
        r = reflow.reflow(r0, sp[0], sp[0]["text"] + " and a good deal more text besides")
        check("justified LaTeX body text is refused, not re-set ragged",
              not r["ok"], str({k: v for k, v in r.items() if k != "pdf"}))

# ── the twin ──
if shutil.which("libreoffice") and os.path.exists(os.path.join(SRC, "letter.html")):
    work = os.path.join(os.path.dirname(CORPUS), "twin", "reflow")
    os.makedirs(work, exist_ok=True)
    src = open(os.path.join(SRC, "letter.html"), encoding="utf-8").read()
    open(os.path.join(work, "letter.html"), "w", encoding="utf-8").write(
        src.replace("Karim El Amrani", NEW))
    subprocess.run(["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", work,
                    "letter.html"], cwd=work, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=200)
    twin = open(os.path.join(work, "letter.pdf"), "rb").read()

    def glyphs(pdf):
        d = fitz.open(stream=pdf, filetype="pdf")
        g = sorted((round(c["origin"][1], 1), c["c"], c["origin"][0])
                   for b in d[0].get_text("rawdict")["blocks"] for l in b.get("lines", [])
                   for s in l["spans"] for c in s["chars"])
        d.close()
        return g
    a, b = glyphs(out), glyphs(twin)
    same = len(a) == len(b) and all(x[:2] == y[:2] for x, y in zip(a, b))
    check("twin: every glyph on the same line as the producer put it", same,
          f"{len(a)} vs {len(b)} glyphs")
    if same:
        worst = max(abs(x[2] - y[2]) for x, y in zip(a, b))
        check("twin: every glyph within half a point of the producer's position",
              worst < 0.5, f"worst {worst:.3f}pt")
else:
    print("SKIP - LibreOffice twin")

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
