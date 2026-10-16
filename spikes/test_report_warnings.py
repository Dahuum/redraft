"""test_report_warnings.py — the warnings an edit reports are the engine's, not Python's.

The redraw records warnings while it runs, and recording catches everything raised meanwhile,
including what the garbage collector says about objects let go of anywhere in the process. Users
were shown "unclosed file <_io.BufferedReader name='/usr/share/fonts/liberation/...'>" and
"Implicitly cleaning up <HTTPError 404: 'Not Found'>" as notes about their document.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))
warnings.filterwarnings("ignore")

import api  # noqa: E402
import pdf_editor  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


pdf = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
by = {s["text"].strip(): s for s in api.extract_spans(pdf)}
api._ingest_embedded_fonts(pdf, "")

# what the redraw meets while it runs: its own note, and the runtime's noise
_orig = pdf_editor.PDFEditor.apply


def noisy_apply(self, *a, **k):
    warnings.warn("unclosed file <_io.BufferedReader name='/usr/share/fonts/x.ttf'>", ResourceWarning)
    warnings.warn("Implicitly cleaning up <HTTPError 404: 'Not Found'>", ResourceWarning)
    warnings.warn("some library is deprecated", DeprecationWarning)
    warnings.warn("[pdf_editor] an engine note the user should see")
    return _orig(self, *a, **k)


pdf_editor.PDFEditor.apply = noisy_apply
out, rep = api.apply_replacements(pdf, [(by["Essaouira"], "Marrakech")], try_inplace=False)
w = rep["warnings"]
check("the edit still went through", out != pdf)
check("the engine's own note is reported", any("an engine note" in x for x in w), w)
check("no ResourceWarning text reaches the report", not any("unclosed file" in x or "Implicitly cleaning" in x for x in w), w)
check("no DeprecationWarning text reaches the report", not any("deprecated" in x for x in w), w)

# and the refused-field branch of the same report
long_value = "Marrakech " * 40
out, rep = api.apply_replacements(pdf, [(by["Essaouira"], long_value)], try_inplace=False)
w = rep["warnings"]
check("refused field: still explained", any("left unchanged" in x or "off the page" in x.lower() for x in w) or out != pdf, w)
check("refused field: no runtime noise", not any("unclosed file" in x or "Implicitly cleaning" in x or "deprecated" in x
                                                 for x in w), w)
pdf_editor.PDFEditor.apply = _orig

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
