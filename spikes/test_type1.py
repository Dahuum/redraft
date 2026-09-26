"""test_type1.py — Type 1 font subsets (pdfTeX / LaTeX): phrases as fields, letters the subset lacks.

examples/latex-cm.pdf is a two-line page typeset the way pdfTeX does it: Computer Modern (CMR10)
embedded as a Type 1 SUBSET holding only the letters its text used, each line one TJ array whose
word gaps are numbers (there is no space glyph). Two things used to fail on such a file:

  * every WORD was its own field ("Compute", "the", "sample"), so changing a phrase meant
    selecting and editing word after word;
  * a letter the subset never had ("Y", "k", "j") could not be added — the engine refused and
    the redraw had no Computer Modern to draw with.

The missing glyphs are copied from the original AMS Computer Modern font (needs the network the
first time, then cached), so those cases SKIP offline.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
import type1_extend as T1  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


raw = open(os.path.join(HERE, "..", "examples", "latex-cm.pdf"), "rb").read()
spans = api.extract_spans(raw)
texts = [s["text"] for s in spans]
check("a LaTeX line is ONE field, not one per word", "Compute the sample" in texts and
      "Bob eats tea and cabs" in texts and "Compute cabs" in texts, str(texts))


def edit(old, new):
    sd = next(s for s in api.extract_spans(raw) if s["text"] == old)
    return api.apply_replacements(raw, [(sd, new)], try_inplace=True)


def words(pdf):
    return [w[4] for w in fitz.open(stream=pdf, filetype="pdf")[0].get_text("words")]


def fonts(pdf):
    return sorted((f[2], f[3]) for f in fitz.open(stream=pdf, filetype="pdf")[0].get_fonts(full=True))


# 1. a phrase of letters the subset has: pure splice across the word gaps
out, rep = edit("Compute the sample", "Compute the ample")
check("a multi-word phrase edits in place", rep["in_place"]["count"] == 1, str(rep["in_place"]))
check("the phrase reads back", words(out)[:3] == ["Compute", "the", "ample"], str(words(out)))
check("no font added", fonts(out) == fonts(raw))

# 2. letters the subset lacks
out, rep = edit("Bob eats tea and cabs", "Yuki jokes about Zebra kayaks")
if rep["in_place"]["count"] == 0 and any(r.get("reason") in ("missing_glyph", "type1_no_donor")
                                       for r in rep["in_place"].get("refusals", [])) \
        and not os.path.exists(os.path.join(HERE, "..", "backend", ".font_cache", "type1", "cmr10.pfb")):
    print("SKIP - new letters (no network for the Computer Modern original)")
else:
    check("new letters are added in place", rep["in_place"]["count"] == 1, str(rep["in_place"]))
    check("the new text reads back", words(out)[3:8] == ["Yuki", "jokes", "about", "Zebra", "kayaks"], str(words(out)))
    check("still the one embedded font", fonts(out) == fonts(raw), str(fonts(out)))
    pg = fitz.open(stream=out, filetype="pdf")[0]
    pix = pg.get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(30, 60, 400, 110))
    ink = sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 128)
    check("the new glyphs draw ink", ink > 1500, "ink=%d" % ink)
    # rewritten font program still parses, with the new glyphs alongside the old
    d = fitz.open(stream=out, filetype="pdf")
    xr = next(f[0] for f in d[0].get_fonts(full=True))
    import re
    fd = int(re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", d.xref_object(xr)).group(1))
    ff = int(re.search(r"/FontFile\s+(\d+)\s+0\s+R", d.xref_object(fd)).group(1))
    prog = T1.Program(*T1.split_program(d.xref_stream(ff), int(d.xref_get_key(ff, "Length1")[1]),
                                        int(d.xref_get_key(ff, "Length2")[1])))
    check("the font program keeps its old glyphs and gains the new",
          {"a", "b", "Y", "k", "j", "Z"} <= set(prog.chars), str(sorted(prog.chars)))
    # a second edit reuses the letters the first one added
    sd = next(s for s in api.extract_spans(out) if s["text"].startswith("Yuki"))
    out2, rep2 = api.apply_replacements(out, [(sd, "Yuki jokes about Zebra")], try_inplace=True)
    check("the added letters are reusable", rep2["in_place"]["count"] == 1, str(rep2["in_place"]))

# 3. a column drawn after a 2 em+ gap number keeps its place when the text before it changes
col = [v for v in fitz.open(stream=raw, filetype="pdf")[0].get_text("words") if v[1] > 75]
col_x = max(v[0] for v in col)
out, rep = edit("Compute cabs", "Compute be")
words_after = [v for v in fitz.open(stream=out, filetype="pdf")[0].get_text("words") if v[1] > 75]
check("shortening a phrase keeps the column after it in place", rep["in_place"]["count"] == 1 and
      abs(max(v[0] for v in words_after) - col_x) < 0.3,
      "%.2f vs %.2f" % (max(v[0] for v in words_after), col_x))
out, rep = edit("Compute cabs", "Compute cabs and tea")
words_after = [v for v in fitz.open(stream=out, filetype="pdf")[0].get_text("words") if v[1] > 75]
check("lengthening it keeps the column in place too", rep["in_place"]["count"] == 1 and
      abs(max(v[0] for v in words_after) - col_x) < 0.3)

# 4. a centred line stays centred when its text changes length
def centre_of(pdf, y_min, text_first):
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    ws = pg.get_text("words")
    line = [w for w in ws if w[1] > y_min]
    left, right = min(w[0] for w in ws), max(w[2] for w in ws)
    return (min(w[0] for w in line) + max(w[2] for w in line)) / 2 - (left + right) / 2


check("(fixture) the last line is centred on the block", abs(centre_of(raw, 95, None)) < 0.5)
out, rep = edit("Compute the cabs", "Compute the sample and tea")
check("lengthening a centred line keeps it centred", rep["in_place"]["count"] == 1 and
      abs(centre_of(out, 95, None)) < 0.6, "off by %.2f" % centre_of(out, 95, None))
out, rep = edit("Compute the cabs", "Compute be")
check("shortening a centred line keeps it centred", rep["in_place"]["count"] == 1 and
      abs(centre_of(out, 95, None)) < 0.6, "off by %.2f" % centre_of(out, 95, None))

print("RESULT:", "ALL PASS" if not FAIL else "FAILURES: %s" % FAIL)
sys.exit(1 if FAIL else 0)
