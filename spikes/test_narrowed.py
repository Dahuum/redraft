"""test_narrowed.py — change one word in a line without touching the rest.

Microsoft Word draws some glyphs of a line through a second font object of
the same name (on the demo attestation, the apostrophes of "l’école" and
"l’Université"). The engine edits within one font object, so every edit of
such a LINE was refused and fell to the redraw — including changing
"programmation" to "informatique", three words away from any apostrophe.

When the whole span can't be spliced for a structural reason, only the
changed words are edited, anchored to their own glyph boxes.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
import inplace_spike as S  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


check("changed words are whole words",
      S._changed_words("école de programmation de l’U", "école de informatique de l’U")[1:]
      == ("programmation", "informatique"))
check("a change at the very start still narrows",
      S._changed_words("Direction de l’école", "Présidence de l’école")[1:]
      == ("Direction", "Présidence"))

raw = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
spans = api.extract_spans(raw)
line = next(s for s in spans if "programmation" in s["text"])
before = fitz.open(stream=raw, filetype="pdf")[0].get_text("words")
y = line["bbox"][1]

for key, new in (("programmation", "informatique"),
                 ("programmation", "programmation avancée"),
                 ("Direction", "Présidence")):
    target = line["text"].replace(key, new)
    out, rep = api.apply_replacements(raw, [(line, target)], try_inplace=True)
    ip = rep["in_place"]
    d = fitz.open(stream=out, filetype="pdf")
    text = d[0].get_text().replace("\n", " ")
    nd = sum(1 for sp in d[0].get_texttrace() for g in sp["chars"] if g[1] == 0)
    after = d[0].get_text("words")
    d.close()
    check(f"{key!r} -> {new!r} is edited in place, not redrawn",
          ip["count"] == 1 and not ip["refusals"], str(ip))
    check(f"  ...reads back whole and draws no .notdef",
          target.strip()[:50] in text and nd == 0)
    # The apostrophe words, drawn by the second font object, follow the edit
    # along the line and stay whole; nothing on other lines moves.
    check("  ...the other font's words are still there, in order",
          [w[4] for w in after if abs(w[1] - y) < 3][-1] == "l’Université")
    others_b = [(w[4], round(w[0], 1), round(w[1], 1)) for w in before if abs(w[1] - y) >= 3]
    others_a = [(w[4], round(w[0], 1), round(w[1], 1)) for w in after if abs(w[1] - y) >= 3]
    check("  ...no other line moved", others_b == others_a)

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
