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

# The same idea, keyed by a NORMALIZED family instead of an exact spelling.
# Real documents name one typeface a dozen ways — the IRS 1040 embeds
# "HelveticaNeueLTStd-Roman" — and enumerating every foundry tag and style
# word per family does not scale.
_DONOR_SUBSTITUTE_KEYS = {
    "helvetica": "Arimo",      # Arimo is metrically compatible with
    "helveticaneue": "Arimo",  # Arial/Helvetica and already proven here.
}

# Tags stripped, one at a time from the end, while looking for a key above.
# "roman"/"book" are weight words, the rest are foundry and optical-size tags.
_DONOR_TAGS = ("roman", "regular", "normal", "book", "std", "pro", "lt", "mt", "ps")


def _donor_alias_key(family: str) -> str:
    """Normalized family key for the substitute table, or the bare key.

    Strips a trailing tag at a time and stops the moment the remainder is a
    curated key. Because nothing is returned unless it LANDS on a curated
    entry, over-stripping cannot invent a match: "Cobalt" would have to
    shrink to "coba" to lose its "lt", which the length floor forbids, and
    "cobalt" is not in the table anyway.
    """
    k = _family_key(family)
    for _ in range(len(_DONOR_TAGS) + 1):
        if k in _DONOR_SUBSTITUTE_KEYS:
            return k
        for tag in _DONOR_TAGS:
            if k.endswith(tag) and len(k) - len(tag) >= 5:
                k = k[: -len(tag)]
                break
        else:
            break
    return k

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
                # Only outlines and advances are ever taken from a donor. Its layout tables
                # are not, and instancing + compiling a big family's GPOS (Merriweather)
                # took four minutes on the first edit.
                for tag in ("GPOS", "GSUB", "GDEF", "kern", "BASE", "JSTF", "DSIG", "STAT"):
                    if tag in tt:
                        del tt[tag]
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
    """Donor bytes for *fontname*, or None. See resolve_donor_detailed."""
    return resolve_donor_detailed(fontname)[0]


def resolve_donor_detailed(fontname: str):
    """(donor bytes, kind) for an embedded font's display name, or (None, None).

    *kind* is "family" when the donor IS this typeface — an installed copy, or
    the same family from the open-source catalogue — and "substitute" when it
    is a different typeface standing in for one that has no open-source
    relative. The caller has to know which: the same designer's letterforms at
    the same weight need no correction and inject as they are, while another
    typeface's need measuring before they are allowed anywhere near the page.
    Injecting a substitute unmeasured is how a Tw Cen MT field came to be
    drawn with Poppins letterforms 33% too tall — see
    font_donors.correct_external_donor.
    """
    family, weight, style = _parse_font_name(fontname)
    key = _family_key(family)
    cache_key = (key, weight, style)
    if cache_key in _donor_cache:
        return _donor_cache[cache_key]

    # An installed copy of the REAL family beats anything downloadable: it is
    # the actual typeface, not a lookalike. Measured, this was the whole
    # reason a Chrome-printed document could not gain 'q', 'z' or 'k' — its
    # font is Liberation Sans, which sits in /usr/share/fonts on the machine
    # doing the editing, while this resolver only ever looked at the Google
    # Fonts repository and reported "no donor".
    try:
        from pdf_editor import _find_system_font
        sys_raw = _find_system_font(family, weight, style)
        if sys_raw:
            _donor_cache[cache_key] = (sys_raw, "family")
            return _donor_cache[cache_key]
    except Exception:  # noqa: BLE001 — no system font search is not fatal
        pass

    # "family" spellings first — the same typeface under its own name, or a
    # catalogue rename of it. Only after those is a different typeface tried.
    tried = [(family, "family")]
    if key in _KNOWN_RENAMES:
        tried.append((_KNOWN_RENAMES[key], "family"))
    if family in _FONT_SUBSTITUTES:
        tried.append((_FONT_SUBSTITUTES[family][0], "substitute"))
    if family in _DONOR_ONLY_SUBSTITUTES:
        tried.append((_DONOR_ONLY_SUBSTITUTES[family], "substitute"))
    _alias = _donor_alias_key(family)
    if _alias in _DONOR_SUBSTITUTE_KEYS:
        tried.append((_DONOR_SUBSTITUTE_KEYS[_alias], "substitute"))

    data, kind = None, None
    for candidate, cand_kind in tried:
        data = _resolve_from_repo(candidate, weight, style)
        if data:
            kind = cand_kind
            break
    _donor_cache[cache_key] = (data, kind)
    return _donor_cache[cache_key]


def _cff_stub():
    """cffLib wants an otFont for compile/decompile; only recalcBBoxes is read.

    Passing None raises AttributeError deep inside the compiler, which is why
    this exists rather than being inlined at each call.
    """
    import types
    return types.SimpleNamespace(recalcBBoxes=False, isTTF=False)


def _glyph_name_for(ch: str):
    """Adobe glyph name for a character, or None if it has no standard one."""
    global _AGL_REV
    if _AGL_REV is None:
        from fontTools import agl
        _AGL_REV = {v: k for k, v in agl.AGL2UV.items()}
    return _AGL_REV.get(ord(ch))


_AGL_REV = None

# How far a donor's letters may sit from this subset's own, per landmark,
# before injecting them would be visible. 6% is under half the 13% at which a
# wrong-sized letter was first noticed by eye in this codebase, and well above
# the 1-2% that separates metrically compatible cuts of the same design.
_CFF_MAX_LANDMARK_ERR = 0.06


_CAP_REFS = ("H", "E", "T", "A", "I", "N")
_XH_REFS = ("x", "o", "e", "n", "u", "c")


def _cff_bounds(charstrings, name):
    from fontTools.pens.boundsPen import BoundsPen
    try:
        bp = BoundsPen(None)
        charstrings[name].draw(bp)
        return bp.bounds
    except Exception:  # noqa: BLE001
        return None


def _cff_landmarks(charstrings):
    """(cap height, x-height) read off a CFF's OWN reference glyphs, or Nones.

    Flat-topped letters only: 'O' and 'e' overshoot the line they sit on, so a
    mix of the two measures a different thing depending on which glyphs a
    subset happens to contain — and a subset contains whatever the document
    used, which is arbitrary.
    """
    cap = xh = None
    for n in _CAP_REFS:
        if n in charstrings:
            b = _cff_bounds(charstrings, n)
            if b:
                cap = b[3]
                break
    for n in _XH_REFS:
        if n in charstrings:
            b = _cff_bounds(charstrings, n)
            if b:
                xh = b[3]
                break
    return cap, xh


def _tt_landmarks(donor, scale):
    """The same two landmarks from a TrueType donor, in the subset's units."""
    from fontTools.pens.boundsPen import BoundsPen
    cmap, gset = donor.getBestCmap(), donor.getGlyphSet()
    cap = xh = None
    for group, ref in ((_CAP_REFS, "cap"), (_XH_REFS, "xh")):
        for n in group:
            g = cmap.get(ord(n))
            if not g:
                continue
            bp = BoundsPen(gset)
            try:
                gset[g].draw(bp)
            except Exception:  # noqa: BLE001
                continue
            if bp.bounds:
                if ref == "cap":
                    cap = bp.bounds[3] * scale
                else:
                    xh = bp.bounds[3] * scale
                break
    return cap, xh


def extend_cff_font(subset_bytes: bytes, donor_bytes: bytes, chars: list) -> dict:
    """Copy each char in `chars` from a TrueType donor into a COPY of a bare
    CFF (Type1C) subset. Returns:
      {"font_bytes": bytes, "names": {char: glyphname}, "width_1000": {char: w}}

    The TrueType path (extend_font) cannot do this: a PDF's /FontFile3 holds a
    BARE CFF program with no SFNT wrapper, so TTFont() rejects it outright with
    "bad sfntVersion". Every Adobe-produced document embeds its fonts this way
    — the whole IRS form set, and most of what Distiller and InDesign emit —
    so without this, any edit on those files that needs a character outside
    the subset can only be refused.

    Raises ValueError if the subset is not CFF, if a char has no standard
    Adobe glyph name, or if the donor cannot draw it. Callers must treat any
    exception as "extension not possible" and refuse, never ship a partial
    result.
    """
    from fontTools.cffLib import CFFFontSet
    from fontTools.ttLib import TTFont
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.transformPen import TransformPen

    stub = _cff_stub()
    cff = CFFFontSet()
    try:
        cff.decompile(io.BytesIO(subset_bytes), stub)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"not a CFF program: {type(exc).__name__}") from exc
    if not cff.fontNames:
        raise ValueError("CFF has no font")
    td = cff[cff.fontNames[0]]
    cs = td.CharStrings

    donor = TTFont(io.BytesIO(donor_bytes))
    if "glyf" not in donor:
        raise ValueError("donor is not a glyf-outline (TrueType) font")
    # CFF charstrings are expressed in the font's own units; a PDF Type1C
    # subset is 1000/em by convention and its /Widths are already in those
    # units, so the donor is scaled into 1000 rather than the other way round.
    scale = 1000.0 / donor["head"].unitsPerEm
    cmap = donor.getBestCmap()
    gset = donor.getGlyphSet()

    # A donor is only allowed near the page if its letters are the RIGHT SIZE
    # in this font's own terms. correct_external_donor cannot be used here —
    # it needs glyf outlines on both sides and returns None for a CFF target —
    # so the check is made directly, against landmarks measured from the
    # subset's own glyphs. Injecting unmeasured is how a Tw Cen MT field came
    # to be drawn with Poppins letterforms 33% too tall.
    sub_cap, sub_xh = _cff_landmarks(cs)
    don_cap, don_xh = _tt_landmarks(donor, scale)
    checked = []
    for a, b, label in ((sub_cap, don_cap, "cap height"), (sub_xh, don_xh, "x-height")):
        if a and b and a > 1:
            checked.append((label, abs(b - a) / a))
    if not checked:
        raise ValueError("cannot measure this subset against the donor")
    worst_label, worst = max(checked, key=lambda t: t[1])
    if worst > _CFF_MAX_LANDMARK_ERR:
        raise ValueError(
            f"donor {worst_label} is off by {worst * 100:.0f}% "
            f"(limit {_CFF_MAX_LANDMARK_ERR * 100:.0f}%)")

    names, widths = {}, {}
    for ch in chars:
        name = _glyph_name_for(ch)
        if not name:
            raise ValueError(f"no standard glyph name for {ch!r}")
        adv_units = None
        if name in cs:
            # Already drawable — nothing to inject, but the caller still needs
            # its width and name to place it.
            names[ch] = name
            widths[ch] = _cff_advance(cs, name)
            continue
        gname = cmap.get(ord(ch))
        if not gname:
            raise ValueError(f"no glyph for {ch!r} in donor")
        adv_units = donor["hmtx"][gname][0] * scale
        # The glyph set is handed to the pen as well as to the transform: a
        # composite like 'e' + acute reaches addComponent, and without a glyph
        # set to resolve the parts it raises instead of drawing.
        pen = T2CharStringPen(adv_units, gset)
        gset[gname].draw(TransformPen(pen, (scale, 0, 0, scale, 0, 0)))
        charstring = pen.getCharString(private=td.Private,
                                       globalSubrs=td.GlobalSubrs)
        charstring.private = td.Private
        charstring.globalSubrs = td.GlobalSubrs
        # CharStrings.__setitem__ only REPLACES a name already in the
        # name->index map, so a new glyph has to go in through the index.
        cs.charStringsIndex.append(charstring)
        cs.charStrings[name] = len(cs.charStringsIndex) - 1
        td.charset.append(name)
        names[ch] = name
        widths[ch] = adv_units

    out = io.BytesIO()
    cff.compile(out, stub)
    return {"font_bytes": out.getvalue(), "names": names, "width_1000": widths,
            "landmark_error": worst, "landmark_checked": worst_label}


def _cff_advance(charstrings, name: str) -> float:
    """Advance width of a glyph already in the CFF, in 1000ths of an em."""
    from fontTools.pens.basePen import NullPen
    csobj = charstrings[name]
    csobj.draw(NullPen())
    w = getattr(csobj, "width", None)
    if w is None:
        w = charstrings.private.defaultWidthX if hasattr(charstrings, "private") else 0
    return float(w)


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
