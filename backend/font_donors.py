"""
font_donors.py — find the best available source for a glyph the edited font
does not have, preferring sources already inside the document.

WHY LOOK INSIDE THE PDF FIRST
-----------------------------
A PDF that uses a font family almost always embeds several cuts of it, each
subset to the characters that cut actually drew. So a Bold subset can be
missing a letter the Regular subset — sitting a few objects away in the same
file — already contains, drawn by the same designer at the same unitsPerEm.

The engine used to skip straight past that and download an open-source
"lookalike" of the family. Measured against the real Bold on the attestation
document (see font_metrics.py), the lookalike it picked was the WORST of
twenty candidates: stem 36.8% too heavy, x-height 33.2% too tall. The
document's own Regular cut is within 3.8%. Going to the network for a
stranger's letterforms while the genuine typeface is already in the file is
both slower and worse.

THE ORDER, BEST FIRST
---------------------
  0. The character is already in the edited font          — no donor needed.
  1. Another subset of the SAME family, weight and style  — the identical
     glyph. Common: a document can embed one cut twice (a simple font for one
     text run and a CID font for another).
  2. Another cut of the same family at a different weight or style, corrected
     by a measured, held-out-validated transform (glyph_synth).
  3. The genuine family fetched from an open-source catalogue, if the family
     really is open-source (Montserrat, Lato, …) rather than a lookalike.
  4. A measured-closest lookalike — a real change of typeface, and the last
     resort rather than the first move.

Only 0-2 are implemented here; 3 and 4 remain font_extend.resolve_donor's
job. Every candidate returned carries the numbers it was chosen on, so the
caller can refuse rather than ship a glyph nobody measured.
"""
from __future__ import annotations

import io

from fontTools.ttLib import TTFont

import font_metrics as fmet
import glyph_synth as gs
from pdf_editor import _parse_font_name


def _family_key(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def harvest_cuts(doc) -> list:
    """Every embedded TrueType-outline font in *doc*, parsed and measured.

    One entry per font program (deduplicated by the stream that holds it, not
    by the font dictionary): a document can reference the same embedded file
    from several font objects, and they are not separate donors.
    """
    cuts, seen = [], set()
    for pno in range(doc.page_count):
        try:
            fonts = doc[pno].get_fonts(full=True)
        except Exception:  # noqa: BLE001
            continue
        for f in fonts:
            xref, basefont = f[0], f[3]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                raw = doc.extract_font(xref)[3]
            except Exception:  # noqa: BLE001
                continue
            if not raw or len(raw) < 256:
                continue
            try:
                tt = TTFont(io.BytesIO(raw))
                if "glyf" not in tt:
                    continue          # CFF outlines: a different injection problem
                measured = fmet.measure(tt)
            except Exception:  # noqa: BLE001
                continue
            display = basefont.split("+")[-1]
            family, weight, style = _parse_font_name(display)
            cuts.append({
                "xref": xref, "basefont": basefont, "display": display,
                "family": family, "family_key": _family_key(family),
                "weight": weight, "style": style,
                "tt": tt, "raw": raw, "coverage": measured["coverage"],
                "measured": measured,
            })
    return cuts


def correct_external_donor(subset_bytes: bytes, donor_bytes: bytes,
                           needed_chars) -> dict:
    """Glyphs for *needed_chars* from a donor that is NOT in the document,
    corrected by the same measured, held-out-validated transform step 2 uses
    — or None when the donor cannot be made to match.

    Step 4 of the order above is "a measured-closest lookalike", and it was
    the only step that did no measuring. The lookalike was injected scaled by
    the unitsPerEm ratio alone, which normalises the design grid and nothing
    else, so every typographic proportion came across unchanged. Measured on
    the attestation document, Poppins Bold standing in for Tw Cen MT Bold:

        x-height    418.9 -> 558.0 per mille   (33% too tall)
        cap height  641.1 -> 702.0             (9.5%)
        stem        125.0 -> 171.0             (37% too heavy)

    The ratios differ per landmark, so no single scale can fix it — which is
    exactly what glyph_synth's anchored vertical mapping is for. Rendered, an
    injected lowercase 'z' stood at 1143 units against the font's own
    x-height of 879 and read as a capital Z in the middle of a word.

    Through the transform the same donor validates at 0.94% height error and
    4.9% stem error on held-out glyphs, against 33% and 37% raw. The SIZES
    come right, which is what made the difference between a lowercase 'z'
    and something that read as a capital Z in the middle of a word.

    The shapes are a weaker match than an in-document cut's and the report
    says so — mean held-out agreement 0.586 against 0.715 for this document's
    own Regular cut, worst glyph 0.306 against 0.602 — so the caller gets the
    numbers and can disclose the substitution. It is deliberately not a
    refusal: the transformed glyphs were measured and rendered, and they are
    the right letters at the right size, which is a different thing from the
    unmeasured injection this replaces.

    None means the transform could not be learned or a glyph's structure did
    not survive it; the caller then falls back or refuses on its own terms.
    """
    needed = [c for c in needed_chars if not c.isspace()]
    if not needed:
        return None
    try:
        target = TTFont(io.BytesIO(subset_bytes))
        donor = TTFont(io.BytesIO(donor_bytes))
    except Exception:  # noqa: BLE001
        return None
    if "glyf" not in target or "glyf" not in donor:
        return None               # CFF outlines: a different injection problem
    try:
        cov = fmet.measure(donor)["coverage"]
        if not all(ord(ch) in cov for ch in needed):
            return None
        xf = gs.learn_weight_transform(donor, target)
    except Exception:  # noqa: BLE001
        return None
    if not xf.usable:
        return None
    glyphs = {}
    for ch in needed:
        try:
            syn = gs.synthesize_char(donor, ch, xf)
        except Exception:  # noqa: BLE001
            return None
        if not syn:
            return None           # structure did not survive; see topology_ok
        glyphs[ch] = syn
    name = ""
    try:
        name = donor["name"].getDebugName(4) or ""
    except Exception:  # noqa: BLE001
        pass
    return {"kind": "external_measured",
            "provenance": f"{name or 'open-source donor'}, corrected by a "
                          f"measured transform",
            "report": xf.report, "glyphs": glyphs}


def find_in_document_donor(doc, target_display: str, needed_chars,
                           target_tt=None) -> dict:
    """Best in-document source for *needed_chars* in the cut named
    *target_display*, or None.

    Returns {"kind", "provenance", "cut", "transform", "report", "glyphs"}
    where `glyphs` maps each needed character to a synthesized outline ready
    for glyph_synth.inject_into_font, and `report` is the held-out accuracy of
    the transform that produced them. A candidate whose transform fails its
    own validation is not returned at all — an unmeasured glyph is exactly
    what this is meant to prevent.
    """
    needed = [c for c in needed_chars if not c.isspace()]
    if not needed:
        return None

    display = target_display.split("+")[-1]
    family, weight, style = _parse_font_name(display)
    fam_key = _family_key(family)

    cuts = harvest_cuts(doc)
    if target_tt is None:
        for c in cuts:
            if c["display"] == display:
                target_tt = c["tt"]
                break
    if target_tt is None:
        return None

    candidates = []
    for c in cuts:
        if c["family_key"] != fam_key:
            continue
        if c["tt"] is target_tt:
            continue
        if not all(ord(ch) in c["coverage"] for ch in needed):
            continue
        # Same weight AND style first: that is the identical glyph rather than
        # a corrected one, so it needs no transform to be faithful.
        same_cut = (c["weight"] == weight and c["style"] == style)
        candidates.append((0 if same_cut else 1, c))

    if not candidates:
        return None

    best = None
    for rank, c in sorted(candidates, key=lambda t: t[0]):
        # An italic donor for upright text (or the reverse) is a visible
        # defect, not an approximation — refuse rather than "correct" it.
        if c["style"] != style:
            continue
        try:
            xf = gs.learn_weight_transform(c["tt"], target_tt)
        except Exception:  # noqa: BLE001
            continue
        if not xf.usable:
            continue
        glyphs = {}
        ok = True
        for ch in needed:
            syn = gs.synthesize_char(c["tt"], ch, xf)
            if not syn:
                ok = False       # structure did not survive; see topology_ok
                break
            glyphs[ch] = syn
        if not ok:
            continue
        cand = {
            "kind": "same_cut" if rank == 0 else "same_family_other_weight",
            "provenance": (f"{c['display']} embedded in this document "
                           f"(weight {c['weight']}, {c['style']})"),
            "cut": c, "transform": xf, "report": xf.report, "glyphs": glyphs,
        }
        # Prefer the higher-ranked kind; among equals prefer better measured
        # shape agreement.
        if best is None or (xf.report.get("iou_mean") or 0) > (
                best["report"].get("iou_mean") or 0):
            best = cand
        if rank == 0:
            break     # an identical cut cannot be beaten
    return best
