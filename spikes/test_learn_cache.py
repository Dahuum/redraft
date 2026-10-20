"""test_learn_cache.py — the glyph transform is learned once per font, not once per preview.

Adding letters a document's subset lacks from a measured substitute costs ~1.7s to learn the
substitute->document transform. It is cached by the fonts' content, but a subset extended by an
earlier edit is re-saved on every request and fontTools stamps head.modified on save: the bytes
differed by that timestamp alone, so every preview of the same edits learned it again.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import io  # noqa: E402
import api  # noqa: E402
import glyph_synth as gs  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


learned = [0]
_orig = gs._learn_weight_transform


def spy(src, dst, raster_size=96):
    learned[0] += 1
    return _orig(src, dst, raster_size)


gs._learn_weight_transform = spy
pdf = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
by = {s["text"].strip(): s for s in api.extract_spans(pdf)}
reps = [(by["Sara Idrissi"], "Yasmine Bouazzaoui"), (by["Essaouira"], "Marrakech"), (by["SI125678"], "QZ998877")]
api._ingest_embedded_fonts(pdf, "")
outs = []
for run in range(3):
    before = learned[0]
    out, _rep = api.apply_replacements(pdf, reps, try_inplace=True)
    outs.append(out)
    if run:
        check(f"request {run + 1}: the transform is not learned again", learned[0] == before, learned[0] - before)
check("the first request did learn it (the test exercises the path)", learned[0] > 0, learned[0])

# the digest itself: identical content saved at different times -> same key; different glyphs -> different key
a = io.BytesIO(); b = io.BytesIO()
src = TTFont(io.BytesIO(api.resolve_full_font("TwCenMT-Bold")))
src.save(a)
src2 = TTFont(io.BytesIO(a.getvalue()))
src2.recalcTimestamp = False          # keep the stamp set below (save would write "now")
src2["head"].modified += 7
src2.save(b)
assert a.getvalue() != b.getvalue()
check("same font saved at another time -> same cache key",
      gs._font_digest(TTFont(io.BytesIO(a.getvalue()))) == gs._font_digest(TTFont(io.BytesIO(b.getvalue()))))
src3 = TTFont(io.BytesIO(a.getvalue()))
src3["hmtx"].metrics[src3.getGlyphOrder()[1]] = (999, 0)
c = io.BytesIO(); src3.save(c)
check("a font with different glyph data -> different cache key",
      gs._font_digest(TTFont(io.BytesIO(a.getvalue()))) != gs._font_digest(TTFont(io.BytesIO(c.getvalue()))))

# Learned from the document's OWN glyphs. Within one request a later edit's subset may carry
# glyphs an earlier edit synthesized; on the attestation the second learn included the 'Q' the
# first had made, so the transform was fitted (and its held-out accuracy measured) partly
# against its own output.
injected, polluted = {}, []
_inj = gs.inject_into_font


def inj_spy(subset_bytes, glyphs):
    r = _inj(subset_bytes, glyphs)
    parent = injected.get(gs._bytes_digest(subset_bytes) if hasattr(gs, "_bytes_digest") else None, set())
    key = gs._font_digest(TTFont(io.BytesIO(r["font_bytes"])))
    injected[key] = set(parent) | set(glyphs)
    if hasattr(gs, "_bytes_digest"):
        injected[gs._bytes_digest(r["font_bytes"])] = injected[key]
    return r


def learn_spy(src, dst, raster_size=96):
    made = injected.get(gs._font_digest(dst), set()) | injected.get(gs._font_digest(src), set())
    bad = sorted(set(gs.shared_chars(src, dst)) & made)
    if bad:
        polluted.append(bad)
    return _orig(src, dst, raster_size)


gs.inject_into_font = inj_spy
gs._learn_weight_transform = learn_spy
gs._XF_CACHE.clear()
api.apply_replacements(pdf, reps, try_inplace=True)
check("no transform is learned from glyphs this request synthesized", not polluted, polluted)
gs.inject_into_font = _inj

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
