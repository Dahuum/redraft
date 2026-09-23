"""test_justify.py — an edit inside a justified line re-justifies the line.

fpdf2's multi_cell justifies by default, ReportLab and Word often do too:
the producer sets word spacing (Tw, or TJ adjustments before each space for
two-byte fonts) so every line but the last ends on the margin. An edit that
changed a word's width left the line ending short of, or past, the margin,
every later word on it off by a growing amount. The producer's own re-print
respaces the line instead; so does the engine now, within a typesetter's
bounds and only when the line breaks would not change.

Needs fpdf2; skips otherwise.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402

try:
    from fpdf import FPDF
except ImportError:
    print("SKIP - fpdf2 not installed")
    sys.exit(0)

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


PARA = ("This statement confirms that {n} has settled every invoice issued by "
        "Atlas Consulting SARL for the period ending 14/03/2024, and that no "
        "further amount is due on this account.")


def make(name):
    import datetime
    p = FPDF(format="A4")
    p.set_creation_date(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc))
    p.add_page()
    p.set_font("Helvetica", "", 11)
    p.multi_cell(0, 6, PARA.format(n=name))
    return bytes(p.output())


orig, twin = make("Nadia Benali"), make("Zoé Ångström")
sd = next(s for s in api.extract_spans(orig) if "Nadia Benali" in s["text"])
out, rep = api.apply_replacements(orig, [(sd, sd["text"].replace("Nadia Benali", "Zoé Ångström"))],
                                  try_inplace=True)


def line1(pdf):
    ws = fitz.open(stream=pdf, filetype="pdf")[0].get_text("words")
    y = min(w[1] for w in ws)
    return [(w[4], w[0], w[2]) for w in ws if abs(w[1] - y) < 1]


a, b = line1(out), line1(twin)
check("edited in place", rep["in_place"]["count"] == 1, str(rep["in_place"]))
check("the justified line still ends on the margin",
      abs(a[-1][2] - b[-1][2]) < 0.1, f"{a[-1][2]:.2f} vs {b[-1][2]:.2f}")
worst = max(abs(x[1] - y[1]) for x, y in zip(a, b)) if len(a) == len(b) else 99
check("every word of the line where fpdf2's own re-print puts it (0.1pt)",
      [w[0] for w in a] == [w[0] for w in b] and worst < 0.1, f"worst {worst:.2f}pt")

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
