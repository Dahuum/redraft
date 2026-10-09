"""test_tail_in_span.py — a word edited in the MIDDLE of a span must carry the rest of the span with it.

Word writes every word of a line as a text object of its own at an absolute x, and MuPDF merges
them into one span. The engine looked for "the text that follows the field" among OTHER spans
only, so the words after the edited one in the same span counted as empty gutter: a longer word
printed over them ("service" over "notre") and a shorter one left a hole. The report said
in_place 1/1 with no warning either way.

Fixture: the repo's own examples/attestation-demo.pdf (fictional).
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


PDF = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
spans = api.extract_spans(PDF)
LINE = next(s for s in spans if s["text"].startswith("Est inscrit(e) dans notre"))
BAND = (LINE["bbox"][1] - 2, LINE["bbox"][3] + 2)


def word_rect(page, word, left_of=None, right_of=None):
    """The first hit of `word` on the edited line, optionally beyond an x."""
    for r in page.search_for(word):
        if r.y0 >= BAND[0] and r.y1 <= BAND[1] and (right_of is None or r.x0 > right_of):
            return r
    return None


def edit(old_word, new_word):
    out, rep = api.apply_replacements(
        PDF, [(LINE, LINE["text"].replace(old_word, new_word, 1))], try_inplace=True)
    return out, rep, fitz.open(stream=out, filetype="pdf")[0]


def line_text(page):
    return " ".join(
        s["text"] for s in api.extract_spans(page.parent.tobytes())
        if s["page"] == 0 and BAND[0] <= s["bbox"][1] <= BAND[1])


orig = fitz.open(stream=PDF, filetype="pdf")[0]
old_dans = word_rect(orig, "dans", right_of=100)
old_notre = word_rect(orig, "notre")
old_year = word_rect(orig, "2025/2026")
old_gap = old_notre.x0 - old_dans.x1
other = {s["text"]: s["bbox"] for s in spans if not (BAND[0] <= s["bbox"][1] <= BAND[1])}

for old, new, label in (("dans", "service", "longer word"), ("dans", "en", "shorter word")):
    out, rep, pg = edit(old, new)
    nw = word_rect(pg, new, right_of=100)
    nn = word_rect(pg, "notre")
    yr = word_rect(pg, "2025/2026")
    check(f"{label}: the edit is made and reads back", nw is not None and nn is not None,
          line_text(pg)[:120])
    if nw is None or nn is None or yr is None:
        continue
    growth = nw.x1 - old_dans.x1
    check(f"{label}: 'notre' does not overlap the new word", nn.x0 >= nw.x1 - 0.2,
          f"{new} ends {nw.x1:.1f}, notre starts {nn.x0:.1f}")
    check(f"{label}: the gap before 'notre' is the one the document had",
          abs((nn.x0 - nw.x1) - old_gap) < 1.0, f"{nn.x0 - nw.x1:.2f} vs {old_gap:.2f}")
    check(f"{label}: the rest of the line moved by the same amount ({growth:+.1f}pt)",
          abs((yr.x0 - old_year.x0) - growth) < 1.0, f"{yr.x0 - old_year.x0:.2f}")
    check(f"{label}: every other line is where it was",
          all(any(abs(s["bbox"][0] - other[s["text"]][0]) < 0.3 and abs(s["bbox"][1] - other[s["text"]][1]) < 0.3
                  for s in api.extract_spans(out) if s["text"] == t) for t in other),
          "a line outside the edited one moved")
    ends = max(s["bbox"][2] for s in api.extract_spans(out) if BAND[0] <= s["bbox"][1] <= BAND[1])
    check(f"{label}: the line still ends inside the page's text margin",
          ends <= pg.rect.width - LINE["bbox"][0] + 0.5, f"ends at {ends:.1f}")

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
