"""test_api_guards.py — a file we cannot read must be REFUSED, never a 500.

Locks in two bugs found by feeding the upload endpoints deliberately hostile
files:

  1. A password-protected PDF returned HTTP 500 Internal Server Error.
     PyMuPDF opens such a file without complaining and only fails later,
     inside font ingestion — which runs BEFORE the endpoint's try block — as
     a bare ValueError. It is not a server error; it is a file we cannot
     read, and the user can fix it in one step if we say so.

  2. A truncated PDF opened with zero pages and was reported as a document
     that simply has no text, which sends the user looking for the wrong
     problem.

Builds its fixtures locally — nothing is downloaded and nothing is committed.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
from fastapi import HTTPException  # noqa: E402
import api as api_mod  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def status_of(data, filename):
    """The HTTP status _check_readable would produce, or None if it accepts."""
    try:
        api_mod._check_readable("The PDF", data, filename)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    return None, ""


d = fitz.open()
p = d.new_page(width=595, height=842)
p.insert_text((70, 120), "Readable document for the guard tests", fontsize=12, fontname="helv")
good = d.tobytes()
d.close()

code, _ = status_of(good, "good.pdf")
check("a normal PDF is accepted", code is None, f"got {code}")

# A structurally valid PDF that declares no pages at all. Truncating a real
# file is not a reliable way to reach this branch — PyMuPDF often reconstructs
# a cut file and hands back its pages — so the fixture asks for it directly.
EMPTY = (b"%PDF-1.4\n"
         b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
         b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
         b"trailer<</Root 1 0 R/Size 3>>\n%%EOF\n")
code, detail = status_of(EMPTY, "empty.pdf")
check("a PDF with no pages is refused, not read as 'no text'", code == 400, f"got {code}")
check("the no-pages message says the file is damaged",
      "damaged" in detail.lower() or "incomplete" in detail.lower(), repr(detail))

# Truncation itself must never crash, whatever PyMuPDF manages to recover.
for cut in (400, 900, 2000):
    try:
        status_of(good[:cut], "truncated.pdf")
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("   truncation at", cut, "raised", type(exc).__name__)
    check(f"a PDF truncated at {cut} bytes is handled without crashing", ok)

code, detail = status_of(b"this is not a pdf at all " * 40, "notapdf.pdf")
check("a non-PDF is refused", code == 400, f"got {code}")

if subprocess.call(["which", "qpdf"], stdout=subprocess.DEVNULL) == 0:
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.pdf")
        enc = os.path.join(td, "enc.pdf")
        open(src, "wb").write(good)
        rc = subprocess.call(
            ["qpdf", "--encrypt", "--user-password=u", "--owner-password=o", "--bits=256",
             "--", src, enc],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc == 0:
            code, detail = status_of(open(enc, "rb").read(), "encrypted.pdf")
            check("a password-protected PDF is refused, not a 500", code == 400, f"got {code}")
            check("the message names the password as the problem",
                  "password" in detail.lower(), repr(detail))
        else:
            print("SKIP — qpdf could not produce an encrypted fixture.")
else:
    print("SKIP — qpdf not installed, encryption case not covered.")

# An image-only page has no text layer; that is not an error, it is a document
# with nothing to edit, and it must still be accepted.
d = fitz.open()
p = d.new_page(width=595, height=842)
src = fitz.open()
sp_ = src.new_page(width=595, height=842)
sp_.insert_text((70, 120), "SCANNED", fontsize=20, fontname="hebo")
pix = sp_.get_pixmap(dpi=72)
src.close()
p.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
img_only = d.tobytes()
d.close()
code, _ = status_of(img_only, "scan.pdf")
check("an image-only page is accepted (nothing to edit is not an error)", code is None, f"got {code}")

print(f"\n{'=' * 70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
