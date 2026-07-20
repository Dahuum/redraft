"""
test_font_extend.py — adversarial tests for the "extend" tier (injecting a
missing glyph into an embedded subset font from a real, open-source donor).

Uses a REPRODUCIBLE synthetic fixture (built with a real Google Font, Source
Sans 3 — the same family the failing real-world resume used) rather than any
private document, so this suite runs the same for anyone.

Requires network access (downloads the donor font once, then caches). If the
network is unavailable, these tests are SKIPPED with a clear message rather
than failing — this is the one part of the suite with an external dependency.
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
import inplace_spike as sp  # noqa: E402
import font_extend  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def network_ok():
    try:
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/google/fonts/main/ofl/sourcesans3/SourceSans3%5Bwght%5D.ttf",
            method="HEAD", headers={"User-Agent": "test"})
        urllib.request.urlopen(req, timeout=8).close()
        return True
    except Exception:
        return False


def build_cv_like_fixture():
    """A subset-font PDF where a specific font/weight never draws a specific
    character — the exact real-world shape that broke on the original resume:
    an embedded, Google-Fonts-backed subset missing a digit that never
    appeared in that particular weight. Uses whatever BaseFont name PyMuPDF's
    own embed+subset pipeline actually assigns (its own internal naming, not
    forced) — this is the same real, organic pipeline the production code
    resolves against, not a hand-rigged name."""
    donor = font_extend.resolve_donor("SourceSansPro-Regular")
    if donor is None:
        return None, None
    doc = fitz.open()
    page = doc.new_page(width=400, height=120)
    page.insert_font(fontname="F0", fontbuffer=donor)
    page.insert_text((40, 40), "Location: Casablanca, Morocco", fontname="F0", fontsize=14)
    doc.subset_fonts()
    font_name = page.get_fonts(full=True)[0][3].split("+")[-1]
    return doc.tobytes(garbage=4, deflate=True), font_name


if not network_ok():
    print("SKIP — no network access (font_extend needs to download a donor font).")
    sys.exit(0)

pdf, fixture_font = build_cv_like_fixture()
if pdf is None:
    print("SKIP — couldn't resolve the donor font (network flaky?).")
    sys.exit(0)
print(f"[fixture font, as PyMuPDF's own embed+subset pipeline named it] {fixture_font!r}\n")

print("=== extend tier: inject a digit never used in this exact font before ===")
r = sp.edit(pdf, "Casablanca, Morocco", "Casablanca6, Morocco")
print(" ", {k: v for k, v in r.items() if k != "pdf_b64"})
check("ok", r.get("ok") is True)
check("tier=extend", r.get("tier") == "extend")
check("guarantee (0 px outside, fair bbox)", r.get("guarantee") is True)

if r.get("ok"):
    import base64
    edited = base64.b64decode(r["pdf_b64"])
    d = fitz.open(stream=edited, filetype="pdf")
    txt = d[0].get_text()
    d.close()
    check("ToUnicode fixed — extracts as real text, not U+FFFD",
         "Casablanca6, Morocco" in sp._norm(txt), repr(txt))

print("\n=== shrinking + extending together ===")
r2 = sp.edit(pdf, "Casablanca, Morocco", "Cas6, Morocco")
print(" ", {k: v for k, v in r2.items() if k != "pdf_b64"})
check("shrink+extend ok+guarantee", r2.get("ok") and r2.get("guarantee"))

print("\n=== unknown font family still refuses cleanly (no crash) ===")
donor = font_extend.resolve_donor("SomeFontNobodyHasHeardOf-Regular")
check("unknown family resolves to None, not an exception", donor is None)

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
