"""test_font_cache.py — page font lists are reused only while the document is unchanged.

One 3-edit request on a 15-page arXiv paper listed a page's fonts 865 times, 1.1 s of 5.2 s,
almost all on documents not yet changed. textcache now reuses those lists, keyed like the text
cache on the bytes the document was opened from. What must never happen is a STALE list: the
engine renames fonts (conform), adds them (the redraw, font extension) and edits their
dictionaries, and every lookup after that has to see it.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))
warnings.filterwarnings("ignore")

import fitz  # noqa: E402
import api  # noqa: E402  (installs the caches)
import textcache  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


d = fitz.open()
for _ in range(3):
    d.new_page().insert_text((50, 50), "Invoice 4821", fontname="helv")
PDF = d.tobytes()
d.close()

a = fitz.open(stream=PDF, filetype="pdf")
first = a[1].get_fonts(full=True)
again = a[1].get_fonts(full=True)
check("a repeated lookup gives the same list", first == again, (first, again))
again.append("junk")
check("a caller changing its list does not change the next one", a[1].get_fonts(full=True) == first)
b = fitz.open(stream=PDF, filetype="pdf")
check("the same bytes opened again give the same list", b[1].get_fonts(full=True) == first)

# renamed after a cached read: the next read sees the new name
xref = first[0][0]
b.xref_set_key(xref, "BaseFont", "/RenamedFont")
check("a font renamed after it was listed is listed under its new name",
      any(f[3] == "RenamedFont" for f in b[1].get_fonts(full=True)), b[1].get_fonts(full=True))
check("...while another copy of the unchanged bytes still lists the original",
      a[1].get_fonts(full=True) == first)

# a font added to a page after it was listed
c = fitz.open(stream=PDF, filetype="pdf")
c[2].get_fonts(full=True)
c[2].insert_text((50, 90), "added", fontname="tiro")
check("a font added after the page was listed is in the next list",
      any("Times" in f[3] for f in c[2].get_fonts(full=True)), c[2].get_fonts(full=True))

# what it is for: one request on the arXiv paper reaches MuPDF's resource walk far less often
CORPUS = os.path.expanduser("~/.cache/redraft-audit/corpus")
path = os.path.join(CORPUS, "arxiv-latex.pdf")
if os.path.exists(path):
    data = open(path, "rb").read()
    spans = api.extract_spans(data)
    cand = [s for s in spans if len(s["text"].split()) >= 3 and s["size"] >= 6][:3]
    reps = [(s, s["text"].replace(s["text"].split()[1], "market", 1)) for s in cand]
    api._ingest_embedded_fonts(data, "")
    api.apply_replacements(data, reps, try_inplace=True)            # warm
    before = dict(textcache._STATS)
    api.apply_replacements(data, reps, try_inplace=True)
    got = {k: textcache._STATS.get(k, 0) - before.get(k, 0) for k in ("font_hit", "font_miss", "font_skip")}
    check("arXiv request: at least 4 in 5 page-font lookups are served from the cache",
          got["font_hit"] >= 4 * (got["font_miss"] + got["font_skip"]) and got["font_hit"] > 0, got)
else:
    print("SKIP - the audit corpus is not built")

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
