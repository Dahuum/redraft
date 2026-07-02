"""
pdf_editor.py — In-place PDF text editing via PyMuPDF redact + stamp overlay.

Font resolution chain
---------------------
Embedded PDF fonts are subsets — only the glyphs used in the original document
survive.  Inserting new text with characters not in the subset produces blank
boxes.  To fix this, the editor resolves FULL fonts in this order:

  1. System fonts  — searched via ``fc-list`` (fontconfig)
  2. Google Fonts static — direct GitHub raw URL for ``{Family}-{Weight}.ttf``
  3. Google Fonts variable — GitHub Contents API to find ``[wght].ttf``, then
     instanced at the right weight via fontTools
  4. Subset fallback — warns explicitly; only original-document characters render

Stamp approach
--------------
Even with the correct full font, inserting text directly on a page that already
has that font registered as Type0/CID causes garbled output.  All new text is
instead drawn on a fresh "stamp" page where the font registers cleanly as a
simple TrueType reference, then composited back via ``show_pdf_page``.
"""

import io
import json
import os
import re
import struct
import subprocess
import urllib.request
import warnings

import fitz  # PyMuPDF >= 1.18


# ── Font cache directory ───────────────────────────────────────────────────────

_FONT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".font_cache")
os.makedirs(_FONT_CACHE_DIR, exist_ok=True)

# In-process resolved-font cache: fontname → bytes or None
_RESOLVED: dict = {}

# Where the last resolved font actually came from — keyed by fontname.
# Value: human-readable source description, e.g.
#   "system:/usr/share/fonts/.../Poppins-Bold.ttf"
#   "google:.font_cache/Poppins-700-normal.ttf (from ofl/poppins/Poppins-Bold.ttf)"
#   "SUBSET-FALLBACK (embedded subset — missing glyphs render as boxes)"
#   "BUILTIN-FALLBACK:helv (no full font found)"
_FONT_SOURCE: dict = {}


# ── Debug logging ──────────────────────────────────────────────────────────────
# Enable with  PDF_EDITOR_DEBUG=1  in the environment.  All trace output goes to
# stderr so it never pollutes returned PDF bytes / stdout pipelines.

import sys

_DEBUG = os.environ.get("PDF_EDITOR_DEBUG", "").strip() not in ("", "0", "false", "False")


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[pdf_editor:font] {msg}", file=sys.stderr, flush=True)


def _ttfont_name(raw: bytes) -> str:
    """Read the internal name (nameID 6 → 4 → 1) the font reports to TTFont()."""
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(io.BytesIO(raw), lazy=True)
        nm = tt["name"]
        for nid in (6, 4, 1):
            rec = nm.getName(nid, 3, 1) or nm.getName(nid, 1, 0)
            if rec:
                return rec.toUnicode()
    except Exception as exc:
        return f"<unreadable: {exc}>"
    return "<no name table>"

# ── Font name aliases ──────────────────────────────────────────────────────────
# Maps embedded PDF font families (often truncated or variant-specific) to the
# canonical Google Fonts family name and folder used for downloading.

_FAMILY_ALIASES: dict = {
    # Optical-size variants — same font, different name in PDF
    "Inter18pt":         "Inter",
    "Inter24pt":         "Inter",
    "Inter28pt":         "Inter",
    "Inter36pt":         "Inter",
    # NunitoSans with embedded optical-size/width suffix (truncated to 8 chars)
    "NunitoSans10ptExpanded":  "NunitoSans",
    "NunitoSans12ptExpanded":  "NunitoSans",
    "NunitoSans10pt":          "NunitoSans",
    "NunitoSans12pt":          "NunitoSans",
    # Partial/truncated names from some PDF producers (max-8-char internal names)
    "NunitoSans10ptExpanded-R":  "NunitoSans",
    "NunitoSans10ptExpanded-M":  "NunitoSans",
    "NunitoSans10ptExpanded-B":  "NunitoSans",
    "NunitoSans10ptExpanded-S":  "NunitoSans",
    "ariitaft":   "Arial",      # Arial Italic (internal Canva/Acrobat alias)
    "aribdft":    "Arial",      # Arial Bold
    "ArialMT":    "Arial",
    "ArialMTPro": "Arial",
}

# Weight overrides for truncated names that don't follow the standard suffix rules
_WEIGHT_OVERRIDES: dict = {
    "NunitoSans10ptExpanded-R": 400,
    "NunitoSans10ptExpanded-M": 500,
    "NunitoSans10ptExpanded-B": 700,
    "NunitoSans10ptExpanded-S": 600,
    "ariitaft":  400,
    "aribdft":   700,
}

# ── Google Fonts known filenames ───────────────────────────────────────────────
# Avoids relying on the GitHub Contents API (which has a low rate limit).
# Format: family_folder → list of (filename, subdir) to try in order.
# Filenames with '[' are variable fonts that require fontTools instancing.

_GF_KNOWN: dict = {
    "poppins":        [("Poppins-{W}.ttf",           "ofl")],
    "opensans":       [("OpenSans[wdth,wght].ttf",   "ofl")],
    "koho":           [("KoHo-{W}.ttf",              "ofl")],
    "poetsenone":     [("PoetsenOne-Regular.ttf",    "ofl")],
    "nunito":         [("Nunito[wght].ttf",           "ofl")],
    "nunitosans":     [("NunitoSans[wdth,wght].ttf", "ofl"),
                       ("NunitoSans-{W}.ttf",         "ofl")],
    "inter":          [("Inter[opsz,wght].ttf",      "ofl")],
    "dmsans":         [("DMSans[opsz,wght].ttf",     "ofl")],
    "dmserifdisplay": [("DMSerifDisplay-Regular.ttf","ofl")],
    "montserrat":     [("Montserrat[wght].ttf",      "ofl")],
    "rosario":        [("Rosario[wght].ttf",          "ofl")],
    "redhatdisplay":  [("RedHatDisplay[wght].ttf",   "ofl")],
    "leaguespartan":  [("LeagueSpartan[wght].ttf",   "ofl")],
    "arimo":          [("Arimo[wght].ttf",            "ofl")],  # Arial-compatible
    "raleway":        [("Raleway[wght].ttf",          "ofl")],
    "dancingscript":  [("DancingScript[wght].ttf",   "ofl")],
    "pacifico":       [("Pacifico-Regular.ttf",       "ofl")],
    "courierprime":   [("CourierPrime-{W}.ttf",       "ofl")],
    "sanchez":        [("Sanchez-Regular.ttf",        "ofl")],
    "batangas":       [("Batangas-Bold.ttf",          "ofl")],  # if exists
    "opensan":        [("OpenSans[wdth,wght].ttf",   "ofl")],
    "sacramento":     [("Sacramento-Regular.ttf",    "ofl")],
    "allura":         [("Allura-Regular.ttf",         "ofl")],
    "greatvibes":     [("GreatVibes-Regular.ttf",     "ofl")],
    "pinyonscript":   [("PinyonScript-Regular.ttf",  "ofl")],
    "alexbrush":      [("AlexBrush-Regular.ttf",     "ofl")],
    "raleway":        [("Raleway[wght].ttf",          "ofl")],
}

# ── Commercial font substitutes ────────────────────────────────────────────────
# Maps commercial/unavailable font families to the closest open-source match
# available on Google Fonts, along with a human-readable reason.

_FONT_SUBSTITUTES: dict = {
    # Geometric sans-serif
    "Garet":          ("Raleway",       "Similar geometric sans-serif proportions"),
    "Touvlo":         ("Montserrat",    "Similar clean geometric sans-serif"),
    "Telegraf":       ("Inter",         "Modern clean sans-serif"),
    "TTNormsPro":     ("Inter",         "Similar proportions and weight range"),
    "CanvaSans":      ("Nunito",        "Round geometric sans-serif"),
    # Serif / display
    "BlostaScript":   ("DancingScript", "Script display font"),
    "BDScript":       ("DancingScript", "Brush/script font"),
    "BrittanySignature": ("DancingScript", "Signature-style script"),
    "Mistrully":      ("Pacifico",      "Playful signature-style script"),
    "MistrullyRegular": ("Pacifico",   "Playful signature-style script"),
    # Arial variants → Arimo (metrically identical, open-source)
    "Arial":          ("Arimo",         "Metrically compatible Arial substitute"),
    "ArialMT":        ("Arimo",         "Metrically compatible Arial substitute"),
    "ArialMTPro":     ("Arimo",         "Metrically compatible Arial substitute"),
    # OpenSauceOne has no accessible TTF download → Inter is closest style
    "OpenSauceOne":   ("Inter",         "Similar modern sans-serif proportions"),
    # NunitoSans when direct download fails
    "NunitoSans":     ("Nunito",        "Same type family, slightly different spacing"),
    # Signature / calligraphy fonts (commercial) → open-source script alternatives
    "AdUScript":      ("GreatVibes",    "Elegant flowing script"),
    "AdUScript-Rg":   ("GreatVibes",    "Elegant flowing script"),
    "Jonathan":       ("Sacramento",    "Thin elegant signature script"),
    "Revive80Signature": ("AlexBrush",  "Brush signature style"),
    "Amsterdam":      ("Allura",        "Formal calligraphy script"),
    "Amsterdam-Three": ("Allura",       "Formal calligraphy script"),
}


# ── PDF Standard-14 (base-14) fonts ────────────────────────────────────────────
# Helvetica/Times/Courier/Symbol/ZapfDingbats and their bold/italic variants are
# the 14 fonts every PDF viewer (and ReportLab) ships built-in.  They are NOT on
# Google Fonts and must NEVER be downloaded — PyMuPDF renders them directly from
# its own built-in codes (helv, tiro, cour, …) with no TTF registration.
# Map: normalised base font name → PyMuPDF built-in code.

_BASE14: dict = {
    "helvetica":             "helv",
    "helvetica-bold":        "hebo",
    "helvetica-oblique":     "heit",
    "helvetica-boldoblique": "hebi",
    "helvetica-obliquebold": "hebi",
    "times-roman":           "tiro",
    "times":                 "tiro",
    "times-bold":            "tibo",
    "times-italic":          "tiit",
    "times-bolditalic":      "tibi",
    "times-italicbold":      "tibi",
    "courier":               "cour",
    "courier-bold":          "cobo",
    "courier-oblique":       "coit",
    "courier-boldoblique":   "cobi",
    "symbol":                "symb",
    "zapfdingbats":          "zadb",
}


def _base14_builtin(fontname: str) -> str | None:
    """Return the PyMuPDF built-in code for a PDF base-14 font, else None.

    Strips any subset prefix ('ABCDEF+Helvetica' → 'Helvetica') and normalises
    case/spaces so 'Helvetica-Bold', 'helvetica bold', etc. all resolve.
    """
    bare = fontname.split("+")[-1].strip().lower().replace(" ", "")
    return _BASE14.get(bare)


# ── Weight / style helpers ─────────────────────────────────────────────────────

_WEIGHT_MAP = {
    "Thin": 100, "ExtraLight": 200, "Light": 300, "Regular": 400,
    "Medium": 500, "SemiBold": 600, "Bold": 700, "ExtraBold": 800,
    "Black": 900,
}
_WEIGHT_NAME = {v: k for k, v in _WEIGHT_MAP.items()}
_WEIGHT_NAME[400] = "Regular"


def _weight_name(w: int) -> str:
    """700 → 'Bold', 400 → 'Regular', etc."""
    # Snap to nearest standard weight
    closest = min(_WEIGHT_NAME, key=lambda x: abs(x - w))
    return _WEIGHT_NAME[closest]


def _parse_font_name(fontname: str) -> tuple:
    """
    'Poppins-Bold'        → ('Poppins', 700, 'normal')
    'OpenSans-BoldItalic' → ('OpenSans', 700, 'italic')
    'Inter18pt-Bold'      → ('Inter', 700, 'normal')   ← alias applied
    'ariitaft'            → ('Arial', 400, 'normal')   ← truncated alias
    """
    bare = fontname.split("+")[-1]

    # Apply weight override before any parsing
    if bare in _WEIGHT_OVERRIDES:
        weight   = _WEIGHT_OVERRIDES[bare]
        family   = _FAMILY_ALIASES.get(bare, bare)
        is_italic = False
        return (family, weight, "normal")

    is_italic = bare.endswith(("Italic", "Oblique"))
    name = re.sub(r"(Italic|Oblique)$", "", bare).rstrip("-")

    weight = 400
    family = name
    for wname, wval in sorted(_WEIGHT_MAP.items(), key=lambda x: -len(x[0])):
        if name.endswith("-" + wname) or name.endswith(wname):
            weight = wval
            family = re.sub(r"-?" + wname + "$", "", name)
            break

    # Apply family alias (e.g. 'Inter18pt' → 'Inter')
    family = _FAMILY_ALIASES.get(family, family)

    return (family, weight, "italic" if is_italic else "normal")


def _family_folder(family: str) -> str:
    """'Open Sans' / 'OpenSans' → 'opensans'  (Google Fonts folder name)."""
    return re.sub(r"[\s\-_]", "", family).lower()


def _font_alias(fontname: str) -> str:
    """Sanitised resource alias safe for PDF resource dicts."""
    bare = fontname.split("+")[-1]
    return re.sub(r"[^a-zA-Z0-9]", "_", bare)[:32]


# ── cmap parser (no fontTools needed for availability check) ──────────────────

def _parse_cmap_chars(raw: bytes) -> set:
    """Return the set of printable Unicode chars in the font's cmap table."""
    try:
        if len(raw) < 12:
            return set()
        _, numTables = struct.unpack_from(">IH", raw, 0)[:2]
        cmap_off = None
        for i in range(numTables):
            base = 12 + i * 16
            if raw[base:base + 4] == b"cmap":
                cmap_off = struct.unpack_from(">I", raw, base + 8)[0]
                break
        if cmap_off is None:
            return set()
        num_sub = struct.unpack_from(">H", raw, cmap_off + 2)[0]
        chars = set()
        for i in range(num_sub):
            base = cmap_off + 4 + i * 8
            plat, enc, offset = struct.unpack_from(">HHI", raw, base)
            if struct.unpack_from(">H", raw, cmap_off + offset)[0] != 4:
                continue
            if (plat, enc) not in [(0, 3), (3, 1)]:
                continue
            tbl = cmap_off + offset
            seg = struct.unpack_from(">H", raw, tbl + 6)[0] // 2
            ends   = struct.unpack_from(f">{seg}H", raw, tbl + 14)
            starts = struct.unpack_from(f">{seg}H", raw, tbl + 16 + seg * 2)
            for s, e in zip(starts, ends):
                if s == 0xFFFF:
                    break
                for cp in range(s, e + 1):
                    if 0x20 <= cp < 0x10000:
                        chars.add(chr(cp))
        return chars
    except Exception:
        return set()


# ── System font lookup ─────────────────────────────────────────────────────────

# Path of the file the most recent helper read from — picked up by
# resolve_full_font() to record _FONT_SOURCE.  Reset at the start of each resolve.
_LAST_PATH: dict = {"value": None}


def _find_system_font(family: str, weight: int, style: str) -> bytes | None:
    """
    Search system fonts via ``fc-list`` (Linux fontconfig).

    Returns the raw font bytes if a matching font is found, else None.
    """
    # Build fc-list style string
    style_str = _weight_name(weight)
    if style == "italic":
        style_str += " Italic" if style_str != "Regular" else "Italic"

    try:
        result = subprocess.run(
            ["fc-list", f":family={family}:style={style_str}", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=5,
        )
        for path in result.stdout.strip().splitlines():
            path = path.strip()
            if path and os.path.exists(path):
                _dbg(f"system fc-list match: family={family!r} style={style_str!r} → {path}")
                _LAST_PATH["value"] = f"system:{path}"
                with open(path, "rb") as f:
                    return f.read()
    except Exception:
        pass

    # Broader fc-match fallback
    try:
        query = f"{family}:weight={weight}"
        result = subprocess.run(
            ["fc-match", query, "--format=%{file}"],
            capture_output=True, text=True, timeout=5,
        )
        path = result.stdout.strip()
        if path and os.path.exists(path):
            fam_clean = family.lower().replace(" ", "")
            base_clean = os.path.basename(path).lower().replace("-", "").replace("_", "")
            if fam_clean in base_clean:
                _dbg(f"system fc-match accepted: query={query!r} → {path}")
                _LAST_PATH["value"] = f"system:{path}"
                with open(path, "rb") as f:
                    return f.read()
            _dbg(f"system fc-match REJECTED (wrong family): query={query!r} → "
                 f"{path} (basename has no {fam_clean!r})")
    except Exception:
        pass

    _dbg(f"system: no match for family={family!r} weight={weight} style={style!r}")
    return None


# ── Variable font instancing ───────────────────────────────────────────────────

def _instance_variable_font(raw: bytes, weight: int) -> bytes:
    """
    Use fontTools to produce a static TTF instance of a variable font at
    the specified *weight*.  Returns the original bytes unchanged on error.
    """
    try:
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont, OverlapMode

        tt = TTFont(io.BytesIO(raw))
        _dbg(f"TTFont() opened variable font: name={_ttfont_name(raw)!r}")
        if "fvar" not in tt:
            return raw  # Not a variable font

        # Clamp weight to axis bounds
        axes = {a.axisTag: a for a in tt["fvar"].axes}
        if "wght" not in axes:
            return raw

        ax = axes["wght"]
        w = max(ax.minValue, min(ax.maxValue, float(weight)))
        _dbg(f"instancing variable font at wght={w} (axis {ax.minValue}..{ax.maxValue})")

        instantiateVariableFont(
            tt, {"wght": w},
            inplace=True,
            overlap=OverlapMode.KEEP_AND_DONT_SET_FLAGS,
        )

        buf = io.BytesIO()
        tt.save(buf)
        return buf.getvalue()

    except Exception as exc:
        warnings.warn(f"[pdf_editor] variable font instancing failed: {exc}")
        return raw


# ── Google Fonts download ──────────────────────────────────────────────────────

_GF_RAW = "https://raw.githubusercontent.com/google/fonts/main"


def _gh_get(url: str, timeout: int = 10) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pdf-editor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _download_and_cache(url: str, cache_path: str, weight: int) -> bytes | None:
    """Download *url*, optionally instance variable font at *weight*, cache."""
    raw = _gh_get(url)
    if not raw or len(raw) < 1000:
        _dbg(f"download MISS: {url}")
        return None
    is_var = "[" in url
    data = _instance_variable_font(raw, weight) if is_var else raw
    with open(cache_path, "wb") as f:
        f.write(data)
    rel = os.path.relpath(cache_path, os.path.dirname(_FONT_CACHE_DIR))
    src_url = url.replace(_GF_RAW + "/", "")
    _dbg(f"download HIT: {src_url} {'(variable→instanced)' if is_var else '(static)'} "
         f"→ cached {rel} [{len(data)} bytes] name={_ttfont_name(data)!r}")
    _LAST_PATH["value"] = f"google:{cache_path} (from {src_url})"
    return data


def _fetch_google_font(family: str, weight: int, style: str) -> bytes | None:
    """
    Download the full font for *family* from Google Fonts GitHub.

    Resolution order:
      1. Disk cache (.font_cache/)
      2. Known filenames from _GF_KNOWN (no API call required)
      3. Substitute: if family is in _FONT_SUBSTITUTES, recurse with the
         substitute family and log a warning
      4. Generic static + variable filename patterns as last resort

    Variable fonts are instanced at *weight* via fontTools before caching.
    """
    fam_nospace = family.replace(" ", "").replace("-", "")
    folder      = _family_folder(family)
    wname       = _weight_name(weight)
    suffix      = wname + ("Italic" if style == "italic" else "")

    cache_key  = f"{fam_nospace}-{weight}-{style}.ttf"
    cache_path = os.path.join(_FONT_CACHE_DIR, cache_key)

    # 1. Disk cache
    if os.path.exists(cache_path):
        _dbg(f"disk-cache HIT: {cache_path}")
        _LAST_PATH["value"] = f"google-cache:{cache_path}"
        with open(cache_path, "rb") as f:
            return f.read()

    # 2. Known filenames (avoids GitHub API rate-limit)
    if folder in _GF_KNOWN:
        for tmpl, sub in _GF_KNOWN[folder]:
            fname = tmpl.replace("{W}", suffix).replace("{w}", wname.lower())
            url   = f"{_GF_RAW}/{sub}/{folder}/{fname}"
            data  = _download_and_cache(url, cache_path, weight)
            if data:
                return data

    # 3. Substitute family
    if family in _FONT_SUBSTITUTES:
        sub_family, reason = _FONT_SUBSTITUTES[family]
        _dbg(f"SUBSTITUTE: {family!r} → {sub_family!r} ({reason})")
        warnings.warn(
            f"[pdf_editor] '{family}' not available — substituting '{sub_family}' "
            f"({reason})"
        )
        data = _fetch_google_font(sub_family, weight, style)
        if data is not None:
            # Annotate that this came via a substitute, not the original family.
            prev = _LAST_PATH["value"] or ""
            _LAST_PATH["value"] = f"substitute({family}→{sub_family}) {prev}"
        return data

    # 4. Generic fallback patterns
    for sub in ("ofl", "apache", "ufl"):
        # Static
        for fname in [f"{fam_nospace}-{suffix}.ttf",
                      f"{family}-{suffix}.ttf"]:
            data = _download_and_cache(
                f"{_GF_RAW}/{sub}/{folder}/{fname}", cache_path, weight
            )
            if data:
                return data
        # Variable
        for var in [f"{fam_nospace}[wght].ttf",
                    f"{fam_nospace}[opsz,wght].ttf",
                    f"{fam_nospace}[wdth,wght].ttf"]:
            data = _download_and_cache(
                f"{_GF_RAW}/{sub}/{folder}/{var}", cache_path, weight
            )
            if data:
                return data

    return None


# ── Full font resolution ───────────────────────────────────────────────────────

def resolve_full_font(fontname: str) -> bytes | None:
    """
    Resolve a FULL (non-subset) font for *fontname*.

    Returns raw font bytes if found, None if all sources fail.
    Logs a clear warning on failure.
    """
    if fontname in _RESOLVED:
        return _RESOLVED.get(fontname + ":bytes")

    # 0. PDF base-14 — built into every viewer; never download a TTF for these.
    builtin = _base14_builtin(fontname)
    if builtin:
        _dbg(f"resolve {fontname!r}: PDF base-14 → built-in {builtin!r}, skip download")
        _RESOLVED[fontname + ":bytes"] = None
        _RESOLVED[fontname] = True
        _FONT_SOURCE[fontname] = f"builtin:{builtin} (PDF base-14, no download)"
        return None

    family, weight, style = _parse_font_name(fontname)
    _dbg(f"resolve {fontname!r} → parsed family={family!r} weight={weight} style={style!r}")
    _LAST_PATH["value"] = None

    # 1. System fonts
    raw = _find_system_font(family, weight, style)
    if raw:
        _dbg(f"RESOLVED {fontname!r} via {_LAST_PATH['value']} "
             f"→ TTFont name={_ttfont_name(raw)!r}")
        _RESOLVED[fontname + ":bytes"] = raw
        _FONT_SOURCE[fontname] = _LAST_PATH["value"]
        _RESOLVED[fontname] = True
        return raw

    # 2. Google Fonts
    raw = _fetch_google_font(family, weight, style)
    if raw:
        _dbg(f"RESOLVED {fontname!r} via {_LAST_PATH['value']} "
             f"→ TTFont name={_ttfont_name(raw)!r}")
        _RESOLVED[fontname + ":bytes"] = raw
        _FONT_SOURCE[fontname] = _LAST_PATH["value"]
        _RESOLVED[fontname] = True
        return raw

    _dbg(f"SUBSET FALLBACK {fontname!r}: no full font found")
    warnings.warn(
        f"[pdf_editor] SUBSET FALLBACK '{fontname}' "
        f"(family='{family}', weight={weight}) — "
        f"no full font found; missing chars will render as boxes"
    )
    _RESOLVED[fontname + ":bytes"] = None
    _FONT_SOURCE[fontname] = "SUBSET-FALLBACK (embedded subset — missing glyphs render as boxes)"
    _RESOLVED[fontname] = True
    return None


def font_source(fontname: str) -> str:
    """Human-readable description of where *fontname* was last resolved from.

    Returns '' if the font has not been resolved yet.  Useful for surfacing the
    actual font origin in a UI ("Poppins-Bold → google:.font_cache/...").
    """
    return _FONT_SOURCE.get(fontname, "")


# ── Color helpers ──────────────────────────────────────────────────────────────

def _int_to_rgb(c: int) -> tuple:
    return (((c >> 16) & 0xFF) / 255.0,
            ((c >>  8) & 0xFF) / 255.0,
            ( c        & 0xFF) / 255.0)


# ── Background sampling ────────────────────────────────────────────────────────

def _sample_bg(pix: fitz.Pixmap, rect: fitz.Rect) -> tuple:
    """
    Return (r,g,b) in [0,1] for the dominant background color behind *rect*.

    Samples multiple regions so coloured backgrounds are identified correctly
    even when the strip directly above the span falls on the white page area:
      - 1-px strip above (y0-1) and at y1  — avoids glyph interiors
      - 4 corners of the bbox              — glyph-free for most text
      - left/right edges at mid-height     — glyph-free for most text

    The median of all samples is returned, which is robust to the few glyph
    pixels that occasionally land in corner/edge positions.
    """
    x0, y0 = max(0, int(rect.x0)), max(0, int(rect.y0))
    x1, y1 = min(pix.width, int(rect.x1)), min(pix.height, int(rect.y1))
    n, s, samples = pix.n, pix.samples, []

    def _grab(px, py):
        if 0 <= px < pix.width and 0 <= py < pix.height:
            b = (py * pix.width + px) * n
            samples.append((s[b], s[b+1], s[b+2]))

    step = max(1, (x1 - x0) // 12)

    # Strip above and at the bottom of the rect
    for px in range(x0, x1, step):
        _grab(px, y0 - 1)
        _grab(px, y1)

    # 4 corners of the bbox itself (background-dominated)
    for px, py in [(x0, y0), (x1-1, y0), (x0, y1-1), (x1-1, y1-1)]:
        _grab(px, py)

    # Left and right edges at the vertical midpoint
    y_mid = (y0 + y1) // 2
    _grab(x0,     y_mid)
    _grab(x1 - 1, y_mid)

    if not samples:
        return (1.0, 1.0, 1.0)
    samples.sort(key=lambda t: t[0] + t[1] + t[2])
    m = samples[len(samples) // 2]
    return (m[0] / 255.0, m[1] / 255.0, m[2] / 255.0)


# ── Span extraction ────────────────────────────────────────────────────────────

def get_spans(doc: fitz.Document, page_num: int = 0) -> list:
    """Return all non-empty text spans on *page_num* as a list of dicts."""
    page   = doc[page_num]
    spans  = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for block in blocks:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span.get("text", "")
                if not text.strip():
                    continue
                spans.append({
                    "text":   text,
                    "bbox":   fitz.Rect(span["bbox"]),
                    "origin": span["origin"],
                    "font":   span["font"],
                    "size":   span["size"],
                    "color":  _int_to_rgb(span["color"]),
                    "flags":  span["flags"],
                })
    return spans


# ── Alignment detection ───────────────────────────────────────────────────────

def _detect_alignments(spans: list) -> dict:
    """
    Return a dict mapping each span's origin key ``(round(ox,1), round(oy,1))``
    to ``'left'``, ``'right'``, or ``'center'``.

    Algorithm
    ---------
    For each span we look for *column mates* — other spans that share the same
    horizontal anchor within a ±3 pt tolerance:

    *Right-aligned*: another span shares the same **right edge** (x1) within
    3 pt, AND has a **different left edge** (x0 differs by > 3 pt).  This means
    different-width texts all ending at the same column boundary.

    *Centered*: another span shares the same **bbox midpoint** ((x0+x1)/2)
    within 3 pt, AND has a different left edge (x0 differs by > 3 pt) AND a
    different right edge (x1 differs by > 3 pt).  Equal-width texts that happen
    to share the same midpoint are excluded (they'd fall into left or right).

    *Left-aligned*: everything else (default).

    Priority: right > center > left.
    """
    X1_TOL  = 3.0   # pt — right-edge tolerance for right-alignment
    CX_TOL  = 3.0   # pt — midpoint tolerance for centering
    X0_MIN  = 3.0   # pt — minimum x0 difference to distinguish from same-text

    n = len(spans)
    x0s = [s["bbox"].x0 for s in spans]
    x1s = [s["bbox"].x1 for s in spans]
    cxs = [(s["bbox"].x0 + s["bbox"].x1) / 2.0 for s in spans]

    result: dict = {}

    for i, s in enumerate(spans):
        key    = (round(s["origin"][0], 1), round(s["origin"][1], 1))
        x0_i   = x0s[i]
        x1_i   = x1s[i]
        cx_i   = cxs[i]

        # ── Right: shares x1, different x0 ────────────────────────────────
        is_right = any(
            j != i
            and abs(x1s[j] - x1_i) <= X1_TOL
            and abs(x0s[j] - x0_i) > X0_MIN
            for j in range(n)
        )
        if is_right:
            result[key] = "right"
            continue

        # ── Center: shares midpoint, different x0 AND different x1 ────────
        is_center = any(
            j != i
            and abs(cxs[j] - cx_i) <= CX_TOL
            and abs(x0s[j] - x0_i) > X0_MIN
            and abs(x1s[j] - x1_i) > X0_MIN
            for j in range(n)
        )
        if is_center:
            result[key] = "center"
            continue

        result[key] = "left"

    return result


# ── PDFEditor ──────────────────────────────────────────────────────────────────

class PDFEditor:
    """
    Edit text in a PDF via redact + stamp overlay.

    Full fonts are resolved from system / Google Fonts so that any replacement
    text renders correctly regardless of what characters were in the original.

    Usage::

        ed = PDFEditor("invoice.pdf")
        for span in ed.spans():
            ed.replace(span=span, new_text=fake_for(span["text"]))
        ed.save("invoice_edited.pdf")
    """

    def __init__(self, path: str):
        self.path = path
        self.doc  = fitz.open(path)
        n = len(self.doc)
        self._pixmaps = {
            i: self.doc[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
            for i in range(n)
        }
        # Queued replacements: page_num → [(span, new_text)]
        self._queue: dict = {i: [] for i in range(n)}
        # Font resolution cache for this document instance:
        # (page_num, fontname) → (alias, raw_bytes, avail_chars)
        self._font_cache: dict = {}

    # ------------------------------------------------------------------
    def spans(self, page_num: int = 0) -> list:
        return get_spans(self.doc, page_num)

    # ------------------------------------------------------------------
    def _font_info(self, page_num: int, fontname: str) -> tuple:
        """
        Return (alias, raw_bytes, avail_chars) for *fontname*.

        Prefers a FULL font from system/Google Fonts over the embedded subset.
        Falls back to the subset (with a warning) only if no full font is found.
        """
        key = (page_num, fontname)
        if key in self._font_cache:
            return self._font_cache[key]

        # 0. PDF base-14 (Helvetica/Times/Courier/…): render with PyMuPDF's
        #    built-in code directly — no download, no insert_font registration.
        builtin = _base14_builtin(fontname)
        if builtin:
            _dbg(f"_font_info {fontname!r}: PDF base-14 → built-in {builtin!r} "
                 f"(no download, no insert_font)")
            _FONT_SOURCE[fontname] = f"builtin:{builtin} (PDF base-14, no download)"
            result = (builtin, None, set())   # alias=builtin code, raw=None
            self._font_cache[key] = result
            return result

        alias = _font_alias(fontname)

        # 1. Try to get the full font
        full_raw = resolve_full_font(fontname)
        if full_raw:
            avail = _parse_cmap_chars(full_raw)
            result = (alias, full_raw, avail)
            self._font_cache[key] = result
            return result

        # 2. Fall back to the embedded subset
        target = fontname.split("+")[-1].lower()
        page   = self.doc[page_num]
        for entry in page.get_fonts(full=True):
            xref, ext, ftype, basefont, *_ = entry
            if basefont.split("+")[-1].lower() != target:
                continue
            try:
                raw = self.doc.extract_font(xref)[3]
                if raw and len(raw) > 256:
                    avail  = _parse_cmap_chars(raw)
                    result = (alias, raw, avail)
                    self._font_cache[key] = result
                    return result
            except Exception as exc:
                warnings.warn(f"[pdf_editor] subset extraction failed for '{fontname}': {exc}")
            break

        self._font_cache[key] = (None, None, set())
        return (None, None, set())

    # ------------------------------------------------------------------
    def replace(self, span: dict, new_text: str, page_num: int = 0):
        """Queue *span* → *new_text* replacement on *page_num*."""
        _, _, avail = self._font_info(page_num, span["font"])
        if avail:  # non-empty means we have cmap info
            missing = [c for c in new_text if c not in avail and not c.isspace()]
            if missing:
                warnings.warn(
                    f"[pdf_editor] '{span['font'].split('+')[-1]}': "
                    f"chars not in font: {sorted(set(missing))}"
                )
        self._queue[page_num].append((span, new_text))

    # ------------------------------------------------------------------
    def replace_all(self, replacements: list, page_num: int = 0):
        for span, new_text in replacements:
            self.replace(span=span, new_text=new_text, page_num=page_num)

    # ------------------------------------------------------------------
    def apply(self):
        """
        Flush all queued replacements:
          1. Erase original text spans with background-colored rects.
          2. Draw replacement text on a fresh stamp page (avoids CID conflict),
             honouring the original alignment and fitting text within the bbox.
          3. Composite stamp onto the original.
        """
        for page_num, items in self._queue.items():
            if not items:
                continue

            page   = self.doc[page_num]
            pix    = self._pixmaps[page_num]
            pw, ph = page.rect.width, page.rect.height

            # Build alignment map from ALL spans on this page (column context)
            all_spans  = get_spans(self.doc, page_num)
            align_map  = _detect_alignments(all_spans)

            # Erase originals
            for span, _ in items:
                page.draw_rect(span["bbox"], color=None,
                               fill=_sample_bg(pix, span["bbox"]), overlay=True)

            # Build stamp
            stamp_doc  = fitz.open()
            stamp_doc.new_page(width=pw, height=ph)
            stamp_page = stamp_doc[0]
            registered: set = set()

            for span, new_text in items:
                alias, raw, _ = self._font_info(page_num, span["font"])
                is_base14 = _base14_builtin(span["font"]) is not None

                # base-14 fonts have alias=builtin code, raw=None → skip insert_font.
                if alias and raw and alias not in registered:
                    try:
                        stamp_page.insert_font(fontname=alias, fontbuffer=raw)
                        registered.add(alias)
                        _dbg(f"insert_font(alias={alias!r}) for original {span['font']!r} "
                             f"[{len(raw)} bytes, src={_FONT_SOURCE.get(span['font'], '?')}]")
                    except Exception as exc:
                        warnings.warn(f"[pdf_editor] insert_font '{span['font']}': {exc}")
                        _dbg(f"insert_font FAILED for {span['font']!r}: {exc} → using helv")
                        alias = None

                fontname_to_use = alias if alias else "helv"

                if alias is None:
                    # Genuine fallback: no full font, no subset, not base-14.
                    _dbg(f"⚠ FALLBACK to builtin 'helv' for original {span['font']!r} "
                         f"— text {new_text[:30]!r} will NOT match")
                    _FONT_SOURCE[span["font"]] = "BUILTIN-FALLBACK:helv (no full font found)"
                elif is_base14:
                    _dbg(f"insert_text base-14 built-in {fontname_to_use!r} for "
                         f"{span['font']!r} text={new_text[:30]!r}")
                else:
                    _dbg(f"insert_text(font={fontname_to_use!r}, size≈{span['size']}) "
                         f"text={new_text[:30]!r} (original font {span['font']!r})")

                # Font object for text-width measurement
                try:
                    fobj = fitz.Font(fontbuffer=raw) if raw else fitz.Font(fontname_to_use)
                except Exception:
                    fobj = fitz.Font("helv")

                bbox      = span["bbox"]              # fitz.Rect
                bbox_w    = max(bbox.x1 - bbox.x0, 1.0)
                fontsize  = span["size"]
                text_w    = fobj.text_length(new_text, fontsize)

                # Shrink font proportionally if new text overflows, min 6 pt
                if text_w > bbox_w:
                    fontsize = max(6.0, fontsize * bbox_w / text_w)
                    text_w   = fobj.text_length(new_text, fontsize)

                # Look up alignment detected from column context
                align_key = (round(span["origin"][0], 1),
                             round(span["origin"][1], 1))
                alignment = align_map.get(align_key, "left")

                oy = span["origin"][1]
                if alignment == "right":
                    ox = bbox.x1 - text_w
                elif alignment == "center":
                    ox = (bbox.x0 + bbox.x1) / 2.0 - text_w / 2.0
                else:
                    ox = span["origin"][0]

                try:
                    stamp_page.insert_text(
                        fitz.Point(ox, oy),
                        new_text,
                        fontname=fontname_to_use,
                        fontsize=fontsize,
                        color=span["color"],
                        overlay=True,
                        render_mode=0,
                    )
                except Exception as exc:
                    warnings.warn(f"[pdf_editor] insert_text '{span['font']}': {exc}")

            page.show_pdf_page(page.rect, stamp_doc, 0, overlay=True)

        self._queue = {i: [] for i in range(len(self.doc))}

    # ------------------------------------------------------------------
    def save(self, output_path: str):
        """Apply all queued replacements and save to *output_path*."""
        self.apply()
        self.doc.save(output_path, garbage=4, deflate=True)
        print(f"✅  saved → {output_path}")
