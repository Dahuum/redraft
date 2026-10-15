"""test_shrink_tear.py — shortening a word in a LaTeX line does not tear the line.

pdfTeX draws a paragraph as one text object: "can be handled ... the indices in equation (" in one
TJ, the "∗" by its own relative Td, ") depending ..." after it. Shortening "indices" to "ind" slid
the rest of its TJ back while the "∗" stayed, printing "equation (   ∗)" — and the line extracted
as two. Pushing the "∗" is impossible (its Td is relative, the next lines would follow), and the
growth-only tear check never fired for a shrink. Now the TJ's word gaps take up the difference,
as a typesetter re-justifies, and the segment still ends at the "(" — or the edit is refused.

Needs the audit corpus (spikes/audit/build_corpus.py).
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []
DOC = os.path.expanduser("~/.cache/redraft-audit/corpus/arxiv-old.pdf")


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


if not os.path.exists(DOC):
    print("SKIP - audit corpus not built")
    sys.exit(0)

data = open(DOC, "rb").read()
api._ingest_embedded_fonts(data, "")
sp = [s for s in api.extract_spans(data) if s["text"].strip() == "indices"][0]
x0, y0, x1, y1 = sp["bbox"]
row = fitz.Rect(-1e4, y0 + 1, 1e4, y1 - 1)
before = fitz.open(stream=data, filetype="pdf")[sp["page"]]

out, rep = api.apply_replacements(data, [(sp, "ind")], try_inplace=True)
after = fitz.open(stream=out, filetype="pdf")[sp["page"]]
txt_b, txt_a = before.get_text(clip=row), after.get_text(clip=row)

check("the edit is made", " ind in equation" in txt_a.replace("\n", " "), txt_a)
check("the line still extracts as one line", txt_a.strip().count("\n") == 0, repr(txt_a))
check("'(∗)' is intact", "(∗)" in txt_a, repr(txt_a))


def x_of(page, word):
    return [round(w[0], 1) for w in page.get_text("words", clip=row) if w[4].startswith(word)]


check("text after the '∗' did not move", x_of(after, "depending") == x_of(before, "depending"),
      (x_of(before, "depending"), x_of(after, "depending")))
check("the line keeps its right edge", abs(max(w[2] for w in after.get_text("words", clip=row))
                                           - max(w[2] for w in before.get_text("words", clip=row))) < 0.5)


def others(page):
    return {(round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), w[4])
            for w in page.get_text("words") if not (y0 - 1 < w[1] < y1 + 1)}


check("every other line of the page is unchanged", others(before) == others(after),
      (sorted(others(before) - others(after))[:3], sorted(others(after) - others(before))[:3]))

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
