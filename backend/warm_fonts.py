"""Build-time font warm-up (best-effort).

Pre-downloads the common substitute fonts the editor/annex path uses into
.font_cache so the first live request is instant. Runtime download handles
anything not warmed here. Failures are non-fatal (the Dockerfile runs this
with `|| true`).
"""
import warnings

warnings.simplefilter("ignore")

try:
    from pdf_editor import resolve_full_font
except Exception as exc:  # noqa: BLE001
    print(f"warm: could not import engine ({exc}); skipping")
    raise SystemExit(0)

NAMES = [
    "Arial", "Arial-Bold", "Arial-Italic", "Arial-BoldItalic",
    "ArialMT", "Arial-BoldMT",
    "Calibri", "Calibri-Bold",
    "Inter", "Inter-Bold",
    # Script-coverage fallback (pdf_editor._get_fallback_font /
    # _FALLBACK_FAMILIES): the edit path's system-font lookup finds nothing
    # on the production container (python:3.12-slim ships zero fonts), so
    # without this the Google Fonts download fallback would hit cold on
    # every live request that needs it instead of an instant disk-cache hit.
    "Noto Sans",
]

cached = 0
for name in NAMES:
    try:
        if resolve_full_font(name):
            cached += 1
    except Exception as exc:  # noqa: BLE001
        print(f"warm: {name} -> {exc}")

print(f"warm: resolved {cached}/{len(NAMES)} font names")
