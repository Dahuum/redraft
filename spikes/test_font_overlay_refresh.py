"""test_font_overlay_refresh.py — a redraw must see glyphs an earlier edit
just injected into the SAME embedded font, not the pre-edit snapshot.

Found live: editing the Word attestation's name to one needing 'm'/'h' (not
in the original 297-glyph Bold subset) succeeds in place — the in-place
engine injects them straight into the document's own font. A birthplace
field on the same page that can't be edited in place (an encoding quirk
this test doesn't parse) falls to the redraw, and *also* needs 'm'. The
redraw draws through PyMuPDF's own insert_font/insert_text, which resolves
fonts via resolve_full_font() — and that checks a per-REQUEST overlay
(_DOC_FONTS) populated ONCE, at upload, from the ORIGINAL pre-edit bytes,
before ever reading the document's current font. The overlay still held the
pre-edit font, missing 'm' entirely, and PyMuPDF's stand-in for a glyph
missing from the buffer it was given was not a blank box: "Hamburg" came out
with an 'm' roughly 45% too wide — silently, no warning, no refusal.

Made worse by a second, independent thing this test also covers: a Word
export commonly embeds the SAME face TWICE, once under a subset-tagged PDF
name ("BCDHEE+TwCenMT-Bold") and once under the font's own bare name ("Tw
Cen MT Bold") — present in the ORIGINAL, unedited upload, both resolving to
the SAME overlay cache key. The in-place engine extends only the xref it
locates by name; refreshing the overlay by iteration order could pick
either twin and silently UNDO the very glyphs just injected. The fix keeps
whichever twin covers more glyphs, never fewer.

Uses examples/attestation-demo.pdf with fabricated values (this file's own
convention — never the corpus's real third-party document).
"""
import io
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

import api  # noqa: E402
import pdf_editor as pe  # noqa: E402

FAIL = []
DEMO = os.path.join(HERE, "..", "examples", "attestation-demo.pdf")


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


if not os.path.exists(DEMO):
    print("SKIP - examples/attestation-demo.pdf not found")
    sys.exit(0)


def m_glyph_ratios(pdf_bytes: bytes, field_size: float, tol: float = 1.5):
    """The rendered ink WIDTH/SIZE ratio of every roughly-field-sized 'm'
    glyph on page 0, via texttrace's per-CHARACTER bbox (the true
    rasterised extent) — not get_text("words"), whose bbox is
    metrics/advance-derived and barely moves even when the glyph actually
    drawn is a completely different, mis-sized shape.

    Width alone isn't comparable: a redrawn field can legitimately be a
    little SMALLER than its source (the engine's own bounded resize to
    fit) — "Hamburg" here at 13.6pt against the name field's 14.0pt, both
    correct on their own. Dividing by the run's own size cancels that out;
    what's left is shape, which must be the same 'm' regardless of scale.
    A wide-net *tol* only excludes the page's clearly differently-sized
    text (the ~11pt footer) while still comparing any field a resize has
    nudged a point or two, which the live bug's 45%-too-wide 'm' clears by
    a wide margin either way."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        out = []
        for tr in doc[0].get_texttrace():
            size = tr.get("size", 0)
            if not size or abs(size - field_size) > tol:
                continue
            for ch in tr["chars"]:
                if chr(ch[0]) == "m":
                    bbox = ch[3]
                    out.append((bbox[2] - bbox[0]) / size)
        return out
    finally:
        doc.close()


data = open(DEMO, "rb").read()

# ── fixture check: the demo really does embed the Bold face twice ──────────
d0 = fitz.open(stream=data, filetype="pdf")
bold_xrefs = [fo[0] for fo in d0[0].get_fonts(full=True) if "Bold" in fo[3]]
check("fixture: the Bold face is embedded under two different xrefs",
      len(bold_xrefs) >= 2, str(bold_xrefs))
d0.close()

# ── single call: name (needs 'm'/'h', succeeds in place) + birthplace
#    (needs 'm' too, falls to redraw) in ONE apply_replacements batch ──────
api._ingest_embedded_fonts(data, None)
spans = api.extract_spans(data)
name = next((s for s in spans if s["text"].strip() == "Sara Idrissi"), None)
pob = next((s for s in spans if s["text"].strip() == "Essaouira"), None)
check("fixture: both fields are found", name is not None and pob is not None)

if name and pob:
    out, rep = api.apply_replacements(
        data, [(name, "Mahmoud Chahine"), (pob, "Hamburg")],
        preserve_size=True, try_inplace=True)
    ip = rep["in_place"]
    check("single call: the name lands in place (injects 'm'/'h')", ip["count"] >= 1,
          str(ip))
    doc = fitz.open(stream=out, filetype="pdf")
    ham = [w for w in doc[0].get_text("words") if w[4] == "Hamburg"]
    check("single call: 'Hamburg' is drawn as one word", len(ham) == 1, str(ham))
    doc.close()
    ratios = m_glyph_ratios(out, name["size"])
    check("single call: at least two 'm' glyphs to compare", len(ratios) >= 2,
          str([round(r, 3) for r in ratios]))
    if len(ratios) >= 2:
        check("single call: every 'm' at field size renders the same shape "
              "(width/size ratio)",
              max(ratios) - min(ratios) < 0.2 * min(ratios),
              f"ratios={[round(r, 3) for r in ratios]}")

# ── two SEPARATE calls against the same document (the /bulk, /annex shape):
#    call 1 injects 'm'/'h' via an in-place edit that needs no redraw at
#    all; call 2, later, redraws a DIFFERENT field that also needs 'm' ─────
data2 = open(DEMO, "rb").read()
api._ingest_embedded_fonts(data2, None)
name2 = next((s for s in api.extract_spans(data2) if s["text"].strip() == "Sara Idrissi"), None)
if name2:
    mid, rep1 = api.apply_replacements(data2, [(name2, "Mahmoud Chahine")],
                                       preserve_size=True, try_inplace=True)
    check("two calls: call 1 (name only) lands fully in place, no redraw needed",
          rep1["in_place"]["count"] == 1 and rep1["in_place"]["total"] == 1)
    overlay = pe._DOC_FONTS.get()
    if overlay and "TwCenMT-700-normal.ttf" in overlay:
        tt = TTFont(io.BytesIO(overlay["TwCenMT-700-normal.ttf"]), lazy=True)
        has_m = ord("m") in (tt.getBestCmap() or {})
        check("two calls: the overlay reflects call 1's injected glyphs even "
              "though call 1 needed no redraw", has_m,
              f"numGlyphs={tt['maxp'].numGlyphs}")

    pob2 = next((s for s in api.extract_spans(mid) if s["text"].strip() == "Essaouira"), None)
    if pob2:
        out2, rep2 = api.apply_replacements(mid, [(pob2, "Hamburg")],
                                            preserve_size=True, try_inplace=True)
        doc2 = fitz.open(stream=out2, filetype="pdf")
        ham2 = [w for w in doc2[0].get_text("words") if w[4] == "Hamburg"]
        check("two calls: 'Hamburg' (call 2's redraw) is drawn as one word",
              len(ham2) == 1, str(ham2))
        doc2.close()
        ratios2 = m_glyph_ratios(out2, name2["size"])
        check("two calls: at least two 'm' glyphs to compare", len(ratios2) >= 2,
              str([round(r, 3) for r in ratios2]))
        if len(ratios2) >= 2:
            check("two calls: every 'm' at field size renders the same shape "
                  "(width/size ratio)",
                  max(ratios2) - min(ratios2) < 0.2 * min(ratios2),
                  f"ratios={[round(r, 3) for r in ratios2]}")

# ── the never-regress rule, as a direct unit check: refreshing must not
#    let an UNEXTENDED same-keyed twin overwrite an extended one already in
#    the overlay, even when the unextended twin is read LATER in the scan ──
data3 = open(DEMO, "rb").read()
api._ingest_embedded_fonts(data3, None)
name3 = next((s for s in api.extract_spans(data3) if s["text"].strip() == "Sara Idrissi"), None)
if name3:
    mid3, rep3 = api.apply_replacements(data3, [(name3, "Mahmoud Chahine")],
                                        preserve_size=True, try_inplace=True)
    d3 = fitz.open(stream=mid3, filetype="pdf")
    extended = unextended = None
    for fo in d3[0].get_fonts(full=True):
        if "Bold" not in fo[3]:
            continue
        raw = d3.extract_font(fo[0])[3]
        n = TTFont(io.BytesIO(raw), lazy=True)["maxp"].numGlyphs
        if extended is None or n > extended[1]:
            unextended = extended
            extended = (fo[0], n)
        else:
            unextended = (fo[0], n)
    d3.close()
    check("fixture: after the edit, one Bold twin is extended and the other isn't",
          extended is not None and unextended is not None and extended[1] > unextended[1],
          f"extended={extended} unextended={unextended}")
    if extended and unextended:
        # Refresh again from the SAME (already-refreshed) document state —
        # exercising the same key a second time is exactly where "keep
        # whichever xref is iterated last" would have silently regressed.
        overlay = pe._DOC_FONTS.get()
        key = "TwCenMT-700-normal.ttf"
        before_n = TTFont(io.BytesIO(overlay[key]), lazy=True)["maxp"].numGlyphs
        check("unit: the overlay already holds the larger, extended font",
              before_n == extended[1], f"{before_n} vs {extended[1]}")
        api._refresh_doc_font_overlay(mid3)
        after_n = TTFont(io.BytesIO(pe._DOC_FONTS.get()[key]), lazy=True)["maxp"].numGlyphs
        check("unit: refreshing again does not regress to the smaller twin",
              after_n == extended[1], f"{after_n} vs {extended[1]} (unextended={unextended[1]})")

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
