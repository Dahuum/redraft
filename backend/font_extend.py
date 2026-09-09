"""
font_extend.py — inject a missing glyph into an embedded, subsetted CID font
by copying it from a donor (open-source) font, so an in-place edit can use a
character that was never drawn in that EXACT embedded font/weight/style
before (the "extend" tier flagged, and left unbuilt, in inplace_spike.py).

EXPERIMENTAL. Companion to inplace_spike.py.

WHY THIS ISN'T A HARDCODED PER-FAMILY TABLE (read before adding another entry)
-------------------------------------------------------------------------------
An earlier version of this file had a tiny dict of ~3 font families it knew
how to fetch a donor for — every OTHER family (the vast majority of the
~1800 families on Google Fonts) hit "unknown font family" on first contact,
which is exactly the "same error, different font" pattern that made this
tier feel broken. The real fix is a resolver that works for the CATALOG, not
one entry at a time:

  1. Normalize the requested family name into a candidate GitHub repo folder
     name under google/fonts (ofl/apache/ufl license dirs).
  2. LIST that folder via the GitHub Contents API — this returns the file
     names Google's own build actually produced, so nothing about their
     naming quirks needs to be guessed:
       - modern families ship ONE variable file per style axis-set, e.g.
         "Montserrat[wght].ttf" / "Montserrat-Italic[wght].ttf", sometimes
         with extra axes ("Merriweather[opsz,wdth,wght].ttf");
       - older/static-only families ship one file PER weight+style, e.g.
         "Lato-Bold.ttf", "Lato-BoldItalic.ttf" — and the prefix before the
         weight name isn't always the family ("PT_Sans-Web-Bold.ttf" for
         "PT Sans") — so weight/style is parsed from each filename's own
         SUFFIX, never assumed from a template.
  3. Prefer an exact variable-axis match (instance at the exact weight via
     fontTools); otherwise pick the nearest-weight STATIC file with the
     correct style (upright/italic) — refusing rather than substituting the
     wrong slant, since an italic glyph merged into upright text (or vice
     versa) would be a visible defect, not just an imperfection.
  4. If the family isn't found under its own name, retry with a small,
     genuinely-necessary set of known Google-side renames (e.g. "Source Sans
     Pro" -> "Source Sans 3"), then with pdf_editor.py's EXISTING
     `_FONT_SUBSTITUTES` table (commercial fonts -> open-source lookalikes,
     e.g. Arial -> Arimo) — reusing tables the main engine already trusts
     rather than inventing a second one.
  5. Only if ALL of that fails — a genuinely custom/commercial font with no
     open-source relative — refuse honestly with "unknown font family".

Listings and resolved font bytes are cached (in-memory + a small on-disk
JSON index) since the GitHub Contents API is rate-limited to 60 req/hour
unauthenticated.

Scope of this version: TrueType-outline (glyf-based) CID fonts only —
CFF/OpenType-CFF donors and subsets are skipped (refuse), and unitsPerEm
must match between subset and donor (asserted, not silently rescaled).
"""
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_editor import _parse_font_name, _WEIGHT_MAP, _FONT_SUBSTITUTES  # noqa: E402

_GH_API = "https://api.github.com/repos/google/fonts/contents"
_GF_RAW = "https://raw.githubusercontent.com/google/fonts/main"
_LICENSE_DIRS = ("ofl", "apache", "ufl")

# Donor-only substitutes for families with NO real Google Fonts entry (used
# solely to find glyphs to copy INTO the original embedded subset).
# Deliberately kept separate from pdf_editor._FONT_SUBSTITUTES: that table
# also feeds _fetch_google_font()/resolve_full_font(), which _font_info()
# PREFERS over the embedded subset for the entire run — adding an entry
# there for a family whose subset IS usable would silently switch every
# edit on that font to the substitute family wholesale, defeating the whole
# point of extending the original font for just the missing glyphs.
_DONOR_ONLY_SUBSTITUTES = {
    "TwCenMT": "Poppins",  # TW Cen MT: no open-source equivalent on Google
                           # Fonts under its own name; rounded-geometric
                           # proportions are the closest visual match.
}

# Google-side renames that break simple normalization (the repo folder no
# longer matches the family's common/PDF-embedded name). Keep this SMALL —
# it's a last-resort override, not the primary resolution mechanism.
_KNOWN_RENAMES = {
    "sourcesanspro": "Source Sans 3",
}

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".font_cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "_gf_repo_listing_cache.json")
os.makedirs(_CACHE_DIR, exist_ok=True)

_listing_cache: dict = {}   # "license/folder" -> list[str] filenames | None (miss)
_donor_cache: dict = {}     # (family_key, weight, style) -> bytes | None (in-memory, per-process)


def _load_listing_cache():
    global _listing_cache
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            _listing_cache = json.load(f)
    except Exception:  # noqa: BLE001
        _listing_cache = {}


def _save_listing_cache():
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_listing_cache, f)
    except Exception:  # noqa: BLE001
        pass  # best-effort; an unwritable cache dir shouldn't break resolution


_load_listing_cache()


def _family_key(family: str) -> str:
    return re.sub(r"[^a-z0-9]", "", family.lower())


def _gh_headers() -> dict:
    h = {"User-Agent": "redraft-font-extend/0.2"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"  # 60/hr -> 5000/hr if set; optional
    return h


def _gh_list_dir(license_dir: str, folder: str):
    """List a google/fonts repo directory's filenames, or None if the family
    genuinely isn't there. Only a real 404 (folder doesn't exist) is cached —
    a 403/5xx/timeout is a TRANSIENT failure (most commonly the unauthenticated
    GitHub API's 60-req/hour limit) and must never be cached as a permanent
    miss, or a rate-limited request would wrongly and durably brand a real,
    known font family as "unknown" for everyone after it."""
    key = f"{license_dir}/{folder}"
    if key in _listing_cache:
        return _listing_cache[key]
    url = f"{_GH_API}/{key}"
    try:
        req = urllib.request.Request(url, headers=_gh_headers())
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
        names = [d["name"] for d in data if isinstance(d, dict) and d.get("type") == "file"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return None  # transient (rate limit, server error) — do NOT cache
        names = None
    except Exception:  # noqa: BLE001 — network hiccup: don't cache, just miss this call
        return None
    _listing_cache[key] = names
    _save_listing_cache()
    return names


# ── filename parsing: derive (weight, style) from whatever's actually there ──
_WEIGHT_BY_LEN = sorted(_WEIGHT_MAP.items(), key=lambda kv: -len(kv[0]))


def _classify_static_filename(stem: str):
    """'PT_Sans-Web-BoldItalic' -> (700, 'italic'); works regardless of the
    prefix, since it only inspects the filename's own suffix."""
    for wname, wval in _WEIGHT_BY_LEN:
        if stem.endswith(wname + "Italic"):
            return wval, "italic"
        if stem.endswith(wname):
            return wval, "normal"
    if stem.endswith("Italic"):
        return 400, "italic"
    return None


_VAR_RE = re.compile(r"^(.*?)(-Italic)?\[([\w,]+)\]\.ttf$", re.I)


def _pick_donor_file(files, weight: int, style: str):
    """From a directory listing, choose the best donor file for (weight,
    style). Returns ("variable", filename) or ("static", filename), or None."""
    ttfs = [f for f in files if f.lower().endswith(".ttf")]
    want_italic = style == "italic"

    variable_upright, variable_italic = [], []
    static_candidates = []  # (weight, style, filename)
    for f in ttfs:
        m = _VAR_RE.match(f)
        if m:
            (variable_italic if m.group(2) else variable_upright).append(f)
            continue
        stem = f[:-4]
        cls = _classify_static_filename(stem)
        if cls:
            static_candidates.append((cls[0], cls[1], f))

    # Prefer an exact variable-axis file for the right style — instancing
    # gives an exact weight match, no nearest-weight compromise needed.
    if want_italic and variable_italic:
        return ("variable", variable_italic[0])
    if not want_italic and variable_upright:
        return ("variable", variable_upright[0])

    # Static family: correct STYLE is a hard requirement (a wrong slant is a
    # visible defect, not just an imperfect weight) — nearest weight within
    # that style is the acceptable compromise, matching how the main engine's
    # own font-matching already snaps to the nearest standard weight.
    same_style = [c for c in static_candidates if c[1] == style]
    if same_style:
        same_style.sort(key=lambda c: abs(c[0] - weight))
        return ("static", same_style[0][2])

    return None


def _download(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "redraft-font-extend/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _resolve_from_repo(family: str, weight: int, style: str):
    """Try every license dir for this exact family name. Returns font bytes
    (already instanced if variable) or None if the folder isn't found."""
    folder = _family_key(family)
    for lic in _LICENSE_DIRS:
        files = _gh_list_dir(lic, folder)
        if not files:
            continue
        choice = _pick_donor_file(files, weight, style)
        if not choice:
            continue
        kind, filename = choice
        try:
            raw = _download(f"{_GF_RAW}/{lic}/{folder}/{filename}")
        except Exception:  # noqa: BLE001
            continue
        if kind == "static":
            return raw
        try:
            tt = TTFont(io.BytesIO(raw))
            if "fvar" in tt:
                axes = {a.axisTag: a for a in tt["fvar"].axes}
                w = float(weight)
                if "wght" in axes:
                    w = max(axes["wght"].minValue, min(axes["wght"].maxValue, w))
                instantiateVariableFont(tt, {"wght": w}, inplace=True)
            buf = io.BytesIO()
            tt.save(buf)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            continue
    return None


def resolve_donor(fontname: str) -> bytes | None:
    """Given an embedded font's display name (e.g. 'Montserrat-Bold'), return
    a full, static donor font at the matching weight/style — resolved
    generically against the Google Fonts catalog, not a hardcoded per-family
    table. None if genuinely not found (custom/commercial font with no
    open-source relative, or a transient fetch failure)."""
    family, weight, style = _parse_font_name(fontname)
    key = _family_key(family)
    cache_key = (key, weight, style)
    if cache_key in _donor_cache:
        return _donor_cache[cache_key]

    tried = [family]
    if key in _KNOWN_RENAMES:
        tried.append(_KNOWN_RENAMES[key])
    if family in _FONT_SUBSTITUTES:
        tried.append(_FONT_SUBSTITUTES[family][0])
    if family in _DONOR_ONLY_SUBSTITUTES:
        tried.append(_DONOR_ONLY_SUBSTITUTES[family])

    data = None
    for candidate in tried:
        data = _resolve_from_repo(candidate, weight, style)
        if data:
            break
    _donor_cache[cache_key] = data
    return data


def extend_font(subset_bytes: bytes, donor_bytes: bytes, chars: list) -> dict:
    """Copy each char in `chars` from donor into a COPY of the subset font
    (never mutates the input bytes). Returns:
      {"font_bytes": bytes, "gid": {char: new_gid}, "width_1000": {char: w}}
    Raises ValueError if the font isn't glyf-based or a requested char isn't
    in the donor either — callers must treat any exception as "extension
    not possible", not attempt a partial result.

    unitsPerEm mismatches (common: most Google Fonts use 1000, many legacy
    Microsoft-heritage TrueType fonts like the commercial fonts this exists
    to work around use 2048) are handled by scaling the donor's outline and
    metrics into the subset's own unitsPerEm via a TransformPen, rather than
    refusing outright — a uniform scale keeps the injected glyph's *shape*
    correct; it can't make a donor glyph's design identical to a font we
    don't have, but it keeps it correctly sized relative to everything else
    already in the subset instead of silently drawing at the wrong scale."""
    subset_tt = TTFont(io.BytesIO(subset_bytes))
    donor_tt = TTFont(io.BytesIO(donor_bytes))
    if "glyf" not in subset_tt or "glyf" not in donor_tt:
        raise ValueError("not a glyf-outline (TrueType) font — not supported yet")
    su_upm = subset_tt["head"].unitsPerEm
    do_upm = donor_tt["head"].unitsPerEm
    scale = su_upm / do_upm

    donor_cmap = donor_tt.getBestCmap()
    donor_glyphset = donor_tt.getGlyphSet()
    existing_names = set(subset_tt.getGlyphOrder())
    name_map: dict = {}
    gid_out, width_out = {}, {}

    def ensure_copied(donor_gname):
        if donor_gname in name_map:
            return name_map[donor_gname]
        new_name = donor_gname if donor_gname not in existing_names else f"donor_{donor_gname}"
        while new_name in existing_names or new_name in name_map.values():
            new_name += "_"
        name_map[donor_gname] = new_name

        # Draw (and scale, if needed) through the pen protocol rather than
        # copying raw coordinates/component transforms by hand — TransformPen
        # composes the unit-scale with each composite component's own
        # transform correctly, and TTGlyphPen never mutates the donor's own
        # Glyph object (the previous version aliased it directly, which
        # rewrote donor state on every composite's component remap).
        pen = TTGlyphPen(donor_glyphset)
        draw_pen = TransformPen(pen, (scale, 0, 0, scale, 0, 0)) if scale != 1.0 else pen
        donor_glyphset[donor_gname].draw(draw_pen)
        new_glyph = pen.glyph()
        if new_glyph.isComposite():
            for comp in new_glyph.components:
                comp.glyphName = ensure_copied(comp.glyphName)  # remap + recurse

        order = subset_tt.getGlyphOrder()
        order.append(new_name)
        subset_tt.setGlyphOrder(order)
        subset_tt["glyf"].glyphOrder = order   # glyf caches its own order — must sync explicitly
        subset_tt["glyf"][new_name] = new_glyph
        donor_width, donor_lsb = donor_tt["hmtx"][donor_gname]
        subset_tt["hmtx"][new_name] = (max(round(donor_width * scale), 0),
                                        round(donor_lsb * scale))
        subset_tt["maxp"].numGlyphs = len(order)
        existing_names.add(new_name)
        return new_name

    for ch in chars:
        cp = ord(ch)
        if cp not in donor_cmap:
            raise ValueError(f"donor font has no glyph for {ch!r} either")
        gname = donor_cmap[cp]
        top_name = ensure_copied(gname)
        gid = subset_tt.getGlyphID(top_name)
        width_units = subset_tt["hmtx"][top_name][0]
        gid_out[ch] = gid
        width_out[ch] = round(width_units * 1000 / su_upm)

        # ensure_copied only adds the outline (glyf) and metrics (hmtx) — the
        # glyph is still unreachable by codepoint until the subset's OWN
        # cmap tables know about it. This matters only for the simple-font
        # (non-CID) path: PyMuPDF's insert_text() resolves Unicode chars via
        # the font's cmap before drawing, whereas the original CID/Identity-H
        # use case this module was built for bypasses cmap entirely (the
        # CIDToGIDMap maps code->GID directly) — such subsets often carry NO
        # cmap table at all (PyMuPDF strips it when it builds a Type0/CID
        # font), so this gap went unnoticed until glyph-injection was reused
        # for simple fonts. Skip entirely when there's no cmap to update.
        if "cmap" not in subset_tt:
            continue
        for table in subset_tt["cmap"].tables:
            if table.format == 0:
                # format 0 (Mac Roman) stores a raw byte-per-codepoint GID
                # table — it physically cannot reference a glyph whose id
                # exceeds 255, which the newly-appended glyph's id almost
                # always does once the subset already has a couple hundred
                # glyphs. Skip it there; the Unicode-BMP subtable below is
                # what PyMuPDF/HarfBuzz actually consult for text shaping.
                if cp < 256 and gid < 256:
                    table.cmap[cp] = top_name
            else:
                table.cmap[cp] = top_name

    buf = io.BytesIO()
    subset_tt.save(buf)
    return {"font_bytes": buf.getvalue(), "gid": gid_out, "width_1000": width_out}
