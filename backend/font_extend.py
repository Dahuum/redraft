"""
font_extend.py — inject a missing glyph into an embedded, subsetted CID font
by copying it from a donor (open-source) font, so an in-place edit can use a
character that was never drawn in that EXACT embedded font/weight/style
before (the "extend" tier flagged, and left unbuilt, in inplace_spike.py).

EXPERIMENTAL. Companion to inplace_spike.py.

Scope of this version: TrueType-outline (glyf-based) CID fonts only, matching
unitsPerEm between subset and donor (asserted, not silently rescaled), and
only the specific font families this has been tested against (see
_DONOR_FONTS). Anything else falls back to the honest "extend" refusal — this
never guesses.
"""
import io
import os
import sys
import urllib.request

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_editor import _parse_font_name  # reuse the existing, tested name parser

_GF_RAW = "https://raw.githubusercontent.com/google/fonts/main"

# Known-good Google Fonts repo locations, verified directly against the live
# repo. NOT the same table as pdf_editor.py's _GF_KNOWN — that one is stale
# for at least "Source Sans Pro" (Google renamed the family/repo folder to
# "Source Sans 3"; pdf_editor.py doesn't know that yet, a separate pre-existing
# gap in the production font-matching path, out of scope to fix here).
_DONOR_FONTS = {
    "sourcesanspro": {"folder": "sourcesans3", "upright": "SourceSans3[wght].ttf",
                      "italic": "SourceSans3-Italic[wght].ttf"},
    "sourcesans3": {"folder": "sourcesans3", "upright": "SourceSans3[wght].ttf",
                    "italic": "SourceSans3-Italic[wght].ttf"},
    "ibmplexsans": {"folder": "ibmplexsans", "upright": "IBMPlexSans[wdth,wght].ttf",
                    "italic": "IBMPlexSans-Italic[wdth,wght].ttf"},
    "opensans": {"folder": "opensans", "upright": "OpenSans[wdth,wght].ttf",
                "italic": "OpenSans-Italic[wdth,wght].ttf"},
}

_donor_cache: dict = {}  # (family_key, weight, style) -> bytes | None


def _family_key(family: str) -> str:
    return family.replace(" ", "").replace("-", "").lower()


def resolve_donor(fontname: str) -> bytes | None:
    """Given an embedded font's display name (e.g. 'SourceSansPro-Bold'),
    return a STATIC instance of a real, full donor font at the matching
    weight/style, or None if this family isn't known or the fetch fails."""
    family, weight, style = _parse_font_name(fontname)
    key = _family_key(family)
    cache_key = (key, weight, style)
    if cache_key in _donor_cache:
        return _donor_cache[cache_key]

    info = _DONOR_FONTS.get(key)
    if not info:
        _donor_cache[cache_key] = None
        return None

    filename = info["italic"] if style == "italic" else info["upright"]
    url = f"{_GF_RAW}/ofl/{info['folder']}/{filename}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "redraft-font-extend/0.1"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
    except Exception:  # noqa: BLE001
        _donor_cache[cache_key] = None
        return None

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
        data = buf.getvalue()
    except Exception:  # noqa: BLE001
        data = None
    _donor_cache[cache_key] = data
    return data


def extend_font(subset_bytes: bytes, donor_bytes: bytes, chars: list) -> dict:
    """Copy each char in `chars` from donor into a COPY of the subset font
    (never mutates the input bytes). Returns:
      {"font_bytes": bytes, "gid": {char: new_gid}, "width_1000": {char: w}}
    Raises ValueError if unitsPerEm mismatches, the font isn't glyf-based, or
    a requested char isn't in the donor either — callers must treat any
    exception as "extension not possible", not attempt a partial result."""
    subset_tt = TTFont(io.BytesIO(subset_bytes))
    donor_tt = TTFont(io.BytesIO(donor_bytes))
    if "glyf" not in subset_tt or "glyf" not in donor_tt:
        raise ValueError("not a glyf-outline (TrueType) font — not supported yet")
    su_upm = subset_tt["head"].unitsPerEm
    do_upm = donor_tt["head"].unitsPerEm
    if su_upm != do_upm:
        raise ValueError(f"unitsPerEm mismatch (subset={su_upm}, donor={do_upm})")

    donor_cmap = donor_tt.getBestCmap()
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
        g = donor_tt["glyf"][donor_gname]
        if g.isComposite():
            for comp in g.components:
                ensure_copied(comp.glyphName)
                comp.glyphName = name_map[comp.glyphName]  # remap component ref
        order = subset_tt.getGlyphOrder()
        order.append(new_name)
        subset_tt.setGlyphOrder(order)
        subset_tt["glyf"].glyphOrder = order   # glyf caches its own order — must sync explicitly
        subset_tt["glyf"][new_name] = g
        subset_tt["hmtx"][new_name] = donor_tt["hmtx"][donor_gname]
        subset_tt["maxp"].numGlyphs = len(order)
        existing_names.add(new_name)
        return new_name

    for ch in chars:
        if ord(ch) not in donor_cmap:
            raise ValueError(f"donor font has no glyph for {ch!r} either")
        gname = donor_cmap[ord(ch)]
        top_name = ensure_copied(gname)
        gid = subset_tt.getGlyphID(top_name)
        width_units = subset_tt["hmtx"][top_name][0]
        gid_out[ch] = gid
        width_out[ch] = round(width_units * 1000 / su_upm)

    buf = io.BytesIO()
    subset_tt.save(buf)
    return {"font_bytes": buf.getvalue(), "gid": gid_out, "width_1000": width_out}
