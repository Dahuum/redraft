"""test_kerning.py — new text is kerned exactly when its producer kerns.

Chrome/Skia shapes with HarfBuzz and kerns: in "Fès" the F advances 6.24pt
rather than 6.90. An edit set at nominal widths therefore sat 0.66pt off
the producer's own re-print, and so did everything after it on the line.
LibreOffice does not kern the same HTML, so kerning cannot be switched on
blindly: whether a document kerns is MEASURED from its own text.
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
import kerning  # noqa: E402
import pdf_editor as pe  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


full = pe.resolve_full_font("DejaVuSans")
k = kerning.font_kern(full)
check("the font's own kerning is read (GPOS class pairs)",
      k is not None and abs(kerning.pair_kern_pt(k, "F", "è", 12) - (-0.656)) < 0.01,
      str(k and kerning.pair_kern_pt(k, "F", "è", 12)))
check("a pair the font does not kern reads as zero",
      kerning.pair_kern_pt(k, "x", "x", 12) == 0.0)

SRC = os.path.expanduser("~/.cache/redraft-audit/src/letter.html")
CHROME = shutil.which("google-chrome-stable") or shutil.which("chromium")
LO = os.path.expanduser("~/.cache/redraft-audit/corpus/letter.pdf")
if not (CHROME and os.path.exists(SRC)):
    print("SKIP - needs Chrome and the audit corpus sources")
else:
    work = os.path.expanduser("~/.cache/redraft-audit/twin/kerning")
    os.makedirs(work, exist_ok=True)

    def chrome(html_text, name):
        h = os.path.join(work, name + ".html")
        out = os.path.join(work, name + ".pdf")
        open(h, "w", encoding="utf-8").write(html_text)
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--print-to-pdf=" + out, "file://" + h],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        return open(out, "rb").read()

    src = open(SRC, encoding="utf-8").read()
    orig = chrome(src, "orig")
    twin = chrome(src.replace("Marrakech", "Fès"), "twin")
    d = fitz.open(stream=orig, filetype="pdf")
    check("a Chrome print is measured as kerning",
          kerning.producer_kerns(d[0], "DejaVuSans", k, d.metadata.get("producer")))
    d.close()
    if os.path.exists(LO):
        d = fitz.open(LO)
        check("a LibreOffice print of the same source is measured as NOT kerning",
              not kerning.producer_kerns(d[0], "DejaVuSans", k, d.metadata.get("producer")))
        d.close()

    sd = next(s for s in api.extract_spans(orig) if "Marrakech" in s["text"])
    out, rep = api.apply_replacements(orig, [(sd, sd["text"].replace("Marrakech", "Fès"))],
                                      try_inplace=True)

    def row(pdf):
        ws = fitz.open(stream=pdf, filetype="pdf")[0].get_text("words")
        y = next(w[1] for w in ws if w[4].startswith("Fès"))
        return [(w[4], w[0]) for w in ws if abs(w[1] - y) < 2]
    a, b = row(twin), row(out)
    worst = max(abs(x[1] - y[1]) for x, y in zip(a, b)) if len(a) == len(b) else 99
    check("the edit lands where Chrome itself puts the new text, to 0.05pt",
          worst < 0.05 and rep["in_place"]["count"] == 1, f"worst {worst:.3f}pt")

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
