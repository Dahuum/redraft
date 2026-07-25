"""
test_font_extend_broad.py — proves the GENERAL font resolver (font_extend.py)
against a diverse battery of real Google Fonts families, not just the ones
that happened to be hardcoded before. Each represents a structurally
different case the resolver must handle without any per-family entry:

  - Roboto           variable, MULTI-axis (wdth,wght)
  - Lato              STATIC family (one file per weight+style)
  - PT Sans            STATIC, unusual filename prefix ("PT_Sans-Web-Bold.ttf")
  - Oswald             variable, single-axis, NO italic file exists at all

For each, builds a fixture PDF (same technique as test_font_extend.py) and
runs the FULL edit() pipeline — not just resolve_donor() — end to end,
including the ToUnicode/width-array correctness checks.

Requires network access; skips cleanly if unavailable.
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
            "https://api.github.com/repos/google/fonts/contents/ofl/roboto",
            method="GET", headers={"User-Agent": "test"})
        urllib.request.urlopen(req, timeout=8).close()
        return True
    except Exception:
        return False


def build_fixture(donor_fontname, text, size=14):
    """A subset-font PDF embedding `donor_fontname` (resolved fresh, exactly
    like a real document would embed a subset of it), drawing `text` once —
    so the subset only contains that text's own glyphs, same as any real PDF."""
    donor = font_extend.resolve_donor(donor_fontname)
    if donor is None:
        return None, None
    doc = fitz.open()
    page = doc.new_page(width=400, height=120)
    page.insert_font(fontname="F0", fontbuffer=donor)
    page.insert_text((40, 40), text, fontname="F0", fontsize=size)
    doc.subset_fonts()
    font_name = page.get_fonts(full=True)[0][3].split("+")[-1]
    return doc.tobytes(garbage=4, deflate=True), font_name


if not network_ok():
    print("SKIP — no network access.")
    sys.exit(0)

CASES = [
    ("Roboto-Bold (variable, multi-axis wdth+wght)", "Roboto-Bold",
     "Invoice Total: 2040", "Invoice Total: 2069"),
    ("Lato-Bold (STATIC family)", "Lato-Bold",
     "Property Valuation Report", "Property Waluation Repnrt"),
    ("PTSans-Regular (STATIC, weird PT_Sans-Web- prefix)", "PTSans-Regular",
     "Account Holder: Jane Smith", "Account Holder: Jake Smith"),
    ("Oswald-Medium (variable, NO italic file exists at all)", "Oswald-Medium",
     "SECTION HEADER TITLE", "SECTION HEADER FIXED"),
]

for label, fontname, old, new in CASES:
    print(f"\n=== {label} ===")
    pdf, fixture_font = build_fixture(fontname, old)
    if pdf is None:
        check(f"{label}: donor resolved", False, "resolve_donor returned None")
        continue
    print(f"  fixture font (as embedded): {fixture_font!r}")
    r = sp.edit(pdf, old, new)
    shown = {k: v for k, v in r.items() if k != "pdf_b64"}
    print("  ", shown)
    check(f"{label}: ok", r.get("ok") is True, str(shown))
    if r.get("ok"):
        check(f"{label}: guarantee (0px outside)", r.get("guarantee") is True, str(shown))
        import base64
        edited = base64.b64decode(r["pdf_b64"])
        d = fitz.open(stream=edited, filetype="pdf")
        txt = sp._norm(d[0].get_text())
        d.close()
        check(f"{label}: ToUnicode correct (extracts as real text)", new in txt, repr(txt))

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
