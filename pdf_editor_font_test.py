"""
pdf_editor_font_test.py — proves classic LaTeX/pdfLaTeX font names (URW/Nimbus
PostScript naming: "NimbusRomNo9L-Regu", "-Medi", "-ReguItal", "-MediItal")
resolve to a real substitute font instead of silently falling back to the
embedded subset (which renders any character outside that exact subset as a
missing/boxed glyph).

Found live on a real academic paper this session: `_parse_font_name` only
recognized "Italic"/"Oblique" and full weight words ("Regular", "Medium",
"Bold", ...) — URW's abbreviated "-Regu"/"-Medi"/"-...Ital" suffixes matched
none of that, so the family stayed as the whole raw string, matched no
substitute table entry, and fell straight to SUBSET-FALLBACK. Separately,
even after teaching the parser to recognize these suffixes, substituting to
Tinos (Google Fonts' Times clone) at the exact weight (500, "Medium") still
failed outright, because Tinos only ships Regular(400)/Bold(700) files —
found and fixed with a weight-snap fallback.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from pdf_editor import _parse_font_name, _FONT_SUBSTITUTES, resolve_full_font, font_source  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


print("=== 1) URW/Nimbus suffix parsing ===")
cases = [
    ("NimbusRomNo9L-Regu",     ("NimbusRomNo9L", 400, "normal")),
    ("NimbusRomNo9L-Medi",     ("NimbusRomNo9L", 500, "normal")),
    ("NimbusRomNo9L-ReguItal", ("NimbusRomNo9L", 400, "italic")),
    ("NimbusRomNo9L-MediItal", ("NimbusRomNo9L", 500, "italic")),
]
for name, expected in cases:
    got = _parse_font_name(name)
    check(f"{name} -> {expected}", got == expected, f"got {got}")

print("\n=== 2) existing common names unaffected (no regression) ===")
existing = [
    ("Poppins-Bold", ("Poppins", 700, "normal")),
    ("OpenSans-BoldItalic", ("OpenSans", 700, "italic")),
    ("Inter18pt-Bold", ("Inter", 700, "normal")),
    ("Arial-Bold", ("Arial", 700, "normal")),
]
for name, expected in existing:
    got = _parse_font_name(name)
    check(f"{name} -> {expected}", got == expected, f"got {got}")

print("\n=== 3) Nimbus family resolves via the substitute table ===")
for fam in ("NimbusRomNo9L", "NimbusSanL", "NimbusMonL"):
    check(f"{fam} has a substitute entry", fam in _FONT_SUBSTITUTES)

print("\n=== 4) end-to-end resolution (needs network) ===")
try:
    raw_regular = resolve_full_font("NimbusRomNo9L-Regu")
    raw_medium = resolve_full_font("NimbusRomNo9L-Medi")
    ok = raw_regular is not None and raw_medium is not None
    check("NimbusRomNo9L-Regu resolves to real font bytes", raw_regular is not None)
    check("NimbusRomNo9L-Medi (weight 500, no exact-weight file) still resolves via weight-snap",
         raw_medium is not None)
    if raw_medium is not None:
        src = font_source("NimbusRomNo9L-Medi")
        check("source correctly labeled as a substitute, not a subset-fallback",
             "substitute" in src and "SUBSET-FALLBACK" not in src, src)
except Exception as e:  # noqa: BLE001
    print(f"SKIP network-dependent checks ({e})")

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
