"""test_word_multifont.py — one visual font, two PDF font objects (Word).

Word draws "TwCenMT-Regular" through TWO font objects under that one display name: its own
TrueType object for everything the base encoding can hold, and a second, Type0/CID object it
switches to for the rare character the base object's own /ToUnicode names but whose glyph the
subsetter actually dropped — here, the curly apostrophe in "l'ecole". The base object keeps a
/Widths entry for that code, zeroed rather than removed (the same stub-width pattern this
fixture's Bold face already has for 'h'/'m'/'4' — see _set_simple_widths).

Before the fix, reflow._Metrics treated a name as CID-only whenever ANY Type0 object existed
for it, measuring the paragraph's other 95%+ (drawn by the base object) against the CID
object's own codes and widths — wrong, and correctly refused by the self-check. After: each
character is measured through whichever variant actually has a REAL (non-stub) width for it,
simple preferred. This is a unit test of that choice, calibrated against the exact stub this
fixture has, not an end-to-end reflow of it — this paragraph's lines are long enough to also
hit an unrelated, pre-existing small per-character rounding drift once they must be re-broken,
which is a separate, later problem (see the project memory note on it), not this one.
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
import reflow  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


raw = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
doc = fitz.open(stream=raw, filetype="pdf")
sd = next(s for s in api.extract_spans(raw) if s["text"].startswith("La Direction de l"))
page = doc[sd["page"]]
m = reflow._Metrics(doc, page)

f = m.font("TwCenMT-Regular")
check("both a simple and a CID object are found for this name",
      f["order"] == ["simple", "cid"], str(f["order"]))

apostrophe = "’"
simple_cfg = f["variants"]["simple"]
simple_code = m._code_in(simple_cfg, "simple", apostrophe)
check("(fixture) the simple object DOES name the apostrophe", simple_code is not None)
check("(fixture) and its own /Widths entry for that code is the documented zero stub",
      not m._has_width(simple_cfg, "simple", simple_code))

variant, code = m.variant_code("TwCenMT-Regular", apostrophe)
check("the stub is skipped: the apostrophe resolves through the CID object instead",
      variant == "cid", "got %r" % (variant,))
adv = m.advance("TwCenMT-Regular", 11.04, apostrophe, code, variant)
check("...with a real, non-zero advance", adv is not None and adv > 1.0, "got %r" % (adv,))

# An ordinary letter, drawn by the base object, is unaffected — still the base object,
# still that object's own real width.
variant2, code2 = m.variant_code("TwCenMT-Regular", "e")
adv2 = m.advance("TwCenMT-Regular", 11.04, "e", code2, variant2)
check("an ordinary letter still resolves through the simple object",
      variant2 == "simple", "got %r" % (variant2,))
check("...with a plausible (non-stub) width", adv2 is not None and adv2 > 2.0, "got %r" % (adv2,))

# A name with only ONE object (the overwhelming common case) is unaffected: this
# recalibrates that the fix is additive, not a change of behaviour for every other font.
f2 = m.font("Calibri")
check("a single-object name still has exactly one order entry", f2["order"] in (["simple"], ["cid"]),
      str(f2["order"]))

# The reason a caller sees after a narrowed retry that still can't fit: this drives whether
# api._try_inplace_batch even TRIES reflow (_REFLOW_REASONS) at all.
new = sd["text"].replace("programmation",
                         "developpement informatique et intelligence artificielle appliquee")
r = S.edit(raw, sd["text"], new, page=sd["page"], bbox=sd["bbox"], verify=False)
check("a narrowed retry that only fails on WIDTH reports would_overflow, not the outer "
      "location refusal (so the caller knows to try reflow)",
      r.get("reason") == "would_overflow", str(r.get("reason")))

print("RESULT:", "ALL PASS" if not FAIL else "FAILURES: %s" % FAIL)
sys.exit(1 if FAIL else 0)
