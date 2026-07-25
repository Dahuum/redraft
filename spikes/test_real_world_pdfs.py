"""
test_real_world_pdfs.py — regression test against real, downloaded documents
from three structurally different generation pipelines, locking in four bugs
found this way (synthetic fixtures alone didn't catch any of them):

  1. LaTeX/pdfTeX ligatures ("fi"/"fl" etc packed into a SINGLE byte via a
     custom /Encoding /Differences array) — the naive `text.encode("latin-1")`
     assumption silently split them into two ASCII bytes that never appear
     together in the real content stream.
  2. Word spacing drawn as pure TJ-array positioning with no space glyph at
     all ("[(Google)-250(Brain)]TJ") — extremely common in dvips/pdfTeX
     output; a search for the phrase WITH its normal space could never match.
  3. PDF literal-string octal escapes ("\\002" as four literal characters
     instead of the one real byte 0x02) — silently broke matching for ANY
     text containing an escaped byte, which is exactly how ligature bytes
     (and other non-printable font codes) get written into a literal string.
  4. /Encoding /WinAnsiEncoding (declared directly, no /Differences, no
     /ToUnicode at all — an Acrobat/Distiller-generated form is a real
     example) treated as if it were Latin-1 — wrong for the ubiquitous
     smart-quote/en-dash/bullet characters WinAnsiEncoding (~= Windows-1252)
     assigns to the 0x80-0x9F byte range, which Latin-1 can't encode at all.

Downloads three real PDFs (a LaTeX paper, a headless-Chrome print-to-PDF
Wikipedia export, an IRS fillable form) fresh each run rather than committing
them — consistent with this repo's convention of not committing test/private
PDFs. Skips cleanly if network is unavailable.
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import inplace_spike as sp  # noqa: E402
import fitz  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "redraft-test/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


try:
    arxiv = fetch("https://arxiv.org/pdf/1706.03762")
except Exception as e:  # noqa: BLE001
    print(f"SKIP — couldn't download the arXiv test PDF ({e}).")
    sys.exit(0)

try:
    wiki = fetch("https://en.wikipedia.org/api/rest_v1/page/pdf/Python_(programming_language)")
except Exception as e:  # noqa: BLE001
    wiki = None
    print(f"[wiki export unavailable, skipping that part: {e}]")

try:
    irs_form = fetch("https://www.irs.gov/pub/irs-pdf/fw9.pdf")
except Exception as e:  # noqa: BLE001
    irs_form = None
    print(f"[IRS form unavailable, skipping that part: {e}]")

print("=== arXiv paper (LaTeX/pdfTeX, dvips-style word spacing + ligatures) ===")
r = sp.analyze(arxiv)
editable = sum(1 for f in r["fields"] if f["editable"])
pct = editable / r["count"] * 100
print(f"  {editable}/{r['count']} editable ({pct:.1f}%)")
check("arXiv: majority of fields editable (>= 60%)", pct >= 60, f"{pct:.1f}%")

r1 = sp.edit(arxiv, "Google Brain", "Google DeepM")
print("  [word-space-gap edit]", {k: v for k, v in r1.items() if k != "pdf_b64"})
check("arXiv: word-space-gap edit ok", r1.get("ok") is True)
check("arXiv: word-space-gap edit guarantee", r1.get("guarantee") is True)

r2 = sp.edit(arxiv, "tables and figures", "tables and pictures")
print("  [ligature edit]", {k: v for k, v in r2.items() if k != "pdf_b64"})
check("arXiv: ligature-crossing edit ok", r2.get("ok") is True)
check("arXiv: ligature-crossing edit guarantee", r2.get("guarantee") is True)
if r2.get("ok"):
    import base64
    edited = base64.b64decode(r2["pdf_b64"])
    d = fitz.open(stream=edited, filetype="pdf")
    txt = d[r2["page"]].get_text()
    d.close()
    check("arXiv: ligature edit extracts as real text", "tables and pictures" in sp._norm(txt), repr(txt[:200]))

if wiki is not None:
    print("\n=== Wikipedia export (headless-Chrome print-to-PDF, mostly CID fonts) ===")
    rw = sp.analyze(wiki)
    editable_w = sum(1 for f in rw["fields"] if f["editable"])
    pctw = editable_w / rw["count"] * 100
    print(f"  {editable_w}/{rw['count']} editable ({pctw:.1f}%)")
    check("wiki: majority of fields editable (>= 90%)", pctw >= 90, f"{pctw:.1f}%")

    re_ = sp.edit(wiki, "Multi-paradigm: object-", "Multi-paradigm: purpose-")
    print("  [edit]", {k: v for k, v in re_.items() if k != "pdf_b64"})
    check("wiki: edit ok", re_.get("ok") is True)
    check("wiki: edit guarantee", re_.get("guarantee") is True)

if irs_form is not None:
    print("\n=== IRS W-9 form (Acrobat/Distiller, WinAnsiEncoding, no /ToUnicode) ===")
    ri = sp.analyze(irs_form)
    editable_i = sum(1 for f in ri["fields"] if f["editable"])
    pcti = editable_i / ri["count"] * 100
    print(f"  {editable_i}/{ri['count']} editable ({pcti:.1f}%)")
    check("IRS form: majority of fields editable (>= 90%)", pcti >= 90, f"{pcti:.1f}%")

    ri_edit = sp.edit(irs_form, "What’s New", "What’s Changed")
    print("  [smart-quote edit]", {k: v for k, v in ri_edit.items() if k != "pdf_b64"})
    check("IRS form: smart-quote edit ok", ri_edit.get("ok") is True)
    check("IRS form: smart-quote edit guarantee", ri_edit.get("guarantee") is True)

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
