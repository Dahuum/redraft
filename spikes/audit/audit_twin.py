"""audit_twin.py — could anyone tell this PDF was edited?

The strongest test there is. For a document whose SOURCE we have, make the
same change twice:

  * edit the finished PDF with Redraft, and
  * make the change in the source and let the original producer
    (LibreOffice) typeset it from scratch — the document as its author
    would have produced it with the new text.

Then compare the two pages the way a reader would. Anything that differs is
something a careful eye could notice; a perfect editor produces the twin.

Reported per edit:
  words   — words whose position differs from the twin by more than 0.5pt
            (layout: did anything land somewhere the author's tool would not
            put it?)
  ink     — fraction of the page whose rendered pixels differ, and the
            largest differing region (typography: kerning, weight, size)
  verdict — IDENTICAL (no word off, ink diff at antialiasing level),
            CLOSE (words in place, sub-glyph ink differences), PRODUCER
            RE-LAYOUT (the author's tool would have re-laid the table; the
            edit kept the original), or VISIBLE.

Control: each unedited original against its regenerated source — the
producer is deterministic, so this must be 0, or the test measures
LibreOffice rather than Redraft. It is 0.000% on all five.
"""
import os
import re
import shutil
import subprocess
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402,F401  (sets up the backend path)
from _common import CORPUS  # noqa: E402

warnings.simplefilter("ignore")
import fitz  # noqa: E402
from api import extract_spans, apply_replacements  # noqa: E402

SRC = os.path.join(os.path.dirname(CORPUS), "src")
WORK = os.path.join(os.path.dirname(CORPUS), "twin")
os.makedirs(WORK, exist_ok=True)

# (document, old, new) — the old text must appear once in the source.
CASES = [
    ("invoice", "Nadia Benali", "Nadia Benkirane"),
    ("invoice", "14/03/2024", "28/11/2025"),
    ("invoice", "Atlas Consulting SARL", "Atlas Group"),
    ("invoice", "Implementation support", "Implementation audit"),
    ("invoice", "MA9911223", "MA4455667"),
    ("form", "Benjelloun", "Alaoui"),
    ("form", "Othmane", "Youssef"),
    ("form", "JB884512", "JC112233"),
    ("form", "34,500.00 MAD", "29,900.00 MAD"),
    ("letter", "BK447120", "BE901234"),
    ("letter", "Marrakech", "Agadir"),
    ("letter", "18 500,00", "21 750,00"),
    ("report", "Hana Ouazzani", "Hana Berrada"),
    ("report", "214", "209"),
    ("sales", "Widget A", "Widget Q"),
    ("sales", "North", "West"),
    # Harder: letters the subset never had, and growth that would re-wrap.
    ("invoice", "Nadia Benali", "Øyvind Ljøkjel"),
    ("form", "Othmane", "Zoé-Ångström"),
    ("letter", "Marrakech", "Fès"),
    ("letter", "Karim El Amrani", "Karim Mohammed El Amrani Benjelloun"),
    ("report", "Hana Ouazzani", "Hana Ouazzani-Berrada El Idrissi"),
    ("sales", "Widget A", "Widget Pro X"),
]

DPI = 110


CHROME = shutil.which("google-chrome-stable") or shutil.which("chromium")


def producer_twin(doc: str, old: str, new: str, producer: str = "libreoffice") -> bytes | None:
    """The document as *producer* typesets its source with old -> new."""
    ext = "csv" if doc == "sales" else "html"
    if producer == "chrome" and ext != "html":
        return None
    src = open(os.path.join(SRC, f"{doc}.{ext}"), encoding="utf-8").read()
    if old and src.count(old) != 1:
        return None
    d = os.path.join(WORK, producer)
    os.makedirs(d, exist_ok=True)
    name = f"{doc}.{ext}"
    open(os.path.join(d, name), "w", encoding="utf-8").write(src.replace(old, new) if old else src)
    out = os.path.join(d, f"{doc}.pdf")
    if os.path.exists(out):
        os.remove(out)
    if producer == "chrome":
        # Skia/PDF: Type0 CID fonts, Identity-H — a different pipeline from
        # LibreOffice's simple TrueType fonts, on the same source.
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--print-to-pdf=" + out, "file://" + os.path.join(d, name)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    else:
        subprocess.run(["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", d, name],
                       cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=200)
    return open(out, "rb").read() if os.path.exists(out) else None


def words(pdf, pno=0):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return d[pno].get_text("words")
    finally:
        d.close()


def gray(pdf, pno=0):
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        return d[pno].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
    finally:
        d.close()


def compare(a: bytes, b: bytes):
    wa, wb = words(a), words(b)
    pool = list(wb)
    off = []
    for w in wa:
        k = next((i for i, v in enumerate(pool) if v[4] == w[4]
                  and abs(v[0] - w[0]) <= 0.5 and abs(v[1] - w[1]) <= 0.5), None)
        if k is None:
            near = min((v for v in wb if v[4] == w[4]),
                       key=lambda v: abs(v[0] - w[0]) + abs(v[1] - w[1]), default=None)
            off.append((w[4], round(near[0] - w[0], 1) if near else None,
                        round(near[1] - w[1], 1) if near else None))
        else:
            pool.pop(k)
    pa, pb = gray(a), gray(b)
    if (pa.width, pa.height) != (pb.width, pb.height):
        return off, 1.0, None
    sa, sb = pa.samples, pb.samples
    diff = [i for i in range(len(sa)) if abs(sa[i] - sb[i]) > 48]
    frac = len(diff) / len(sa)
    box = None
    if diff:
        xs = [i % pa.width for i in diff]
        ys = [i // pa.width for i in diff]
        k = 72.0 / DPI
        box = (round(min(xs) * k), round(min(ys) * k), round(max(xs) * k), round(max(ys) * k))
    return off, frac, box


if not shutil.which("libreoffice"):
    print("SKIP - libreoffice not installed")
    sys.exit(0)

rows = []
PRODUCERS = ["libreoffice"] + (["chrome"] if CHROME else [])
for producer in PRODUCERS:
    for doc, old, new in CASES:
        # The original is the producer's own output of the untouched source
        # (the corpus copy for LibreOffice, a fresh print for Chrome).
        orig = producer_twin(doc, "", "", producer)
        twin = producer_twin(doc, old, new, producer)
        tag = f"{doc}/{producer[:6]}"
        if orig is None or twin is None:
            continue
        spans = extract_spans(orig)
        sd = next((s for s in spans if old in s["text"]), None)
        if sd is None:
            rows.append((tag, old, new, "not found", "", "", ""))
            continue
        out, rep = apply_replacements(orig, [(sd, sd["text"].replace(old, new))],
                                      preserve_size=True, try_inplace=True)
        reflowed = bool(rep["in_place"].get("reflowed"))
        engine = ("reflow" if reflowed else "in-place") if rep["in_place"]["count"] else (
            "refused" if out == orig else "redraw")
        if out == orig:
            rows.append((tag, old, new, engine, "", "", "refused: " + str(
                [r.get("reason") for r in rep["in_place"]["refusals"]][:1])))
            continue
        off, frac, box = compare(out, twin)
        relayout, _, _ = compare(orig, twin)
        relayout = [w for w in relayout if w[0] not in old.split()]
        if not off and frac < 0.0005:
            verdict = "IDENTICAL"
        elif not off:
            verdict = "CLOSE"
        elif relayout and not [w for w in off if w[0] not in new.split()
                               and w not in relayout]:
            verdict = "PRODUCER RE-LAYOUT (edit kept the original layout)"
        else:
            verdict = "VISIBLE"
        rows.append((tag, old, new, engine, len(off), f"{frac * 100:.3f}%", verdict +
                     (f"  e.g. {off[:3]}" if off else "") + (f"  ink box {box}" if box else "")))

    # Control: the producer is deterministic — two prints of one source match.
    for doc in ("invoice", "form", "letter", "report", "sales"):
        a1 = producer_twin(doc, "", "", producer)
        a2 = producer_twin(doc, "", "", producer)
        if a1 and a2:
            off, frac, box = compare(a1, a2)
            rows.append((f"{doc}/{producer[:6]}", "(control)", "(unedited)", "-", len(off),
                         f"{frac * 100:.3f}%", "baseline noise" + (f"  ink box {box}" if box else "")))

print(f"{'doc':15} {'old':24} {'new':18} {'engine':9} {'off':>4} {'ink':>8}  verdict")
for r in rows:
    print(f"{r[0]:15} {r[1][:24]:24} {r[2][:18]:18} {r[3]:9} {str(r[4]):>4} {str(r[5]):>8}  {r[6]}")
