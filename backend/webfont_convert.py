"""Font programs a browser cannot load, rebuilt as OpenType it can.

The editor draws text being typed in the document's own fonts, loaded into the page from
/webfonts. A browser takes TrueType and OpenType; a PDF also holds BARE CFF programs (/FontFile3:
everything Acrobat Distiller and InDesign write — the whole IRS form set) and Type 1 programs
(/FontFile: LaTeX's Computer Modern), and leaves the standard 14 fonts out entirely, for the
viewer's own (MuPDF's are bare CFF too). Those fonts were reported missing and every field drawn
in them was typed in the app's own font: all 898 fields of the W-9, all 1153 of the 1040.

Both rebuild the same way: every glyph redrawn as a Type 2 charstring with its own advance (no
subroutines to carry over), named glyphs mapped to Unicode by the Adobe Glyph List, wrapped in the
tables an OpenType-CFF font needs. Measured: rendered pixel for pixel like the program it came
from (MuPDF, same text, same size), and accepted by Chrome's font sanitizer.
"""
import io
import os
import tempfile

# TeX draws no space character, so its fonts have no space glyph and a browser takes the fallback
# face's: "Computer  modern" with gaps twice too wide. A third of an em is TeX's interword space.
_SPACE_EM = 1 / 3


def _stub():
    import types
    return types.SimpleNamespace(recalcBBoxes=False, isTTF=False)


def _build(ps_name: str, upm: int, order: list, draw, width_of) -> bytes:
    """OpenType-CFF from glyph *order*, `draw(name, pen)` and `width_of(name)`."""
    from fontTools.agl import toUnicode
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    charstrings, metrics, cmap = {}, {}, {}
    y_min, y_max = 0.0, 0.0
    for g in order:
        w = int(round(width_of(g) or 0))
        pen = T2CharStringPen(w, None)
        draw(g, pen)
        charstrings[g] = pen.getCharString()
        bounds = BoundsPen(None)
        draw(g, bounds)
        b = bounds.bounds
        if b:
            y_min, y_max = min(y_min, b[1]), max(y_max, b[3])
        metrics[g] = (w, int(round(b[0])) if b else 0)
        u = toUnicode(g) if g != ".notdef" else ""
        if len(u) == 1 and ord(u) not in cmap:
            cmap[ord(u)] = g
    if 0x20 not in cmap:
        g = "space" if "space" not in metrics else "space.rd"
        order = order + [g]
        charstrings[g] = T2CharStringPen(int(round(upm * _SPACE_EM)), None).getCharString()
        metrics[g] = (int(round(upm * _SPACE_EM)), 0)
        cmap[0x20] = g
    safe = "".join(c for c in ps_name if c.isalnum() or c in "-_")[:60] or "Embedded"
    asc = int(round(max(y_max, upm * 0.75)))
    desc = int(round(min(y_min, -upm * 0.2)))
    fb = FontBuilder(upm, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(safe, {"FullName": safe, "FamilyName": safe}, charstrings, {})
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=asc, descent=desc)
    fb.setupNameTable({"familyName": safe, "styleName": "Regular", "psName": safe})
    fb.setupOS2(sTypoAscender=asc, sTypoDescender=desc, usWinAscent=asc, usWinDescent=-desc)
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _upm(font_matrix) -> int:
    try:
        return int(round(1 / font_matrix[0])) if font_matrix and font_matrix[0] else 1000
    except Exception:  # noqa: BLE001
        return 1000


def otf_from_cff(cff_bytes: bytes, ps_name: str = None):
    """OpenType from a bare CFF program; None for one that cannot be mapped (CID-keyed: its
    glyphs have no names to map to Unicode)."""
    from fontTools.cffLib import CFFFontSet
    from fontTools.pens.basePen import NullPen

    cff = CFFFontSet()
    cff.decompile(io.BytesIO(cff_bytes), _stub())
    name = cff.fontNames[0]
    top = cff[name]
    if hasattr(top, "ROS"):
        return None
    cs = top.CharStrings
    order = list(top.charset)
    if ".notdef" not in order:
        order.insert(0, ".notdef")

    def width_of(g):
        c = cs[g]
        c.draw(NullPen())
        w = getattr(c, "width", None)
        return w if w is not None else top.Private.defaultWidthX

    return _build(ps_name or name.split("+")[-1], _upm(getattr(top, "FontMatrix", None)), order,
                  lambda g, pen: cs[g].draw(pen), width_of)


def otf_from_type1(t1_bytes: bytes, ps_name: str = None):
    """OpenType from a Type 1 program (PFA or PFB, as a PDF's /FontFile holds it)."""
    from fontTools.pens.basePen import NullPen
    from fontTools.t1Lib import T1Font

    if b"cleartomark" not in t1_bytes[-600:]:
        # A /FontFile usually drops the trailer (Length3 0): 512 zeros and cleartomark.
        t1_bytes = t1_bytes.rstrip(b"\x00") + b"\n" + (b"0" * 64 + b"\n") * 8 + b"cleartomark\n"
    fd, path = tempfile.mkstemp(suffix=".pfa")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(t1_bytes)
        t1 = T1Font(path, kind="OTHER")
        t1.parse()
    finally:
        os.unlink(path)
    font = t1.font
    cs = font["CharStrings"]
    order = sorted(cs.keys(), key=lambda g: (g != ".notdef", g))

    def width_of(g):
        c = cs[g]
        c.draw(NullPen())
        return getattr(c, "width", 0) or 0

    return _build(ps_name or str(font.get("FontName", "Embedded")), _upm(font.get("FontMatrix")), order,
                  lambda g, pen: cs[g].draw(pen), width_of)


# Rebuilt fonts by program content. MuPDF's Helvetica alone is 654 glyphs, 0.34 s to rebuild, and
# every document that leaves Helvetica out asks for it again.
_MEMO: dict = {}
_MEMO_MAX = 128


def web_ready(program: bytes, ext: str, ps_name: str = None):
    """*program* as a font a browser loads (TrueType/OpenType bytes), or None."""
    if not program:
        return None
    if program[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return program
    if ext not in ("cff", "pfa", "pfb"):
        return None
    import hashlib
    key = (hashlib.sha1(program).hexdigest(), ext, ps_name)
    if key in _MEMO:
        return _MEMO[key]
    try:
        out = otf_from_cff(program, ps_name) if ext == "cff" else otf_from_type1(program, ps_name)
    except Exception:  # noqa: BLE001 — a font this cannot rebuild is drawn in a generic face
        out = None
    if len(_MEMO) >= _MEMO_MAX:
        _MEMO.pop(next(iter(_MEMO)))
    _MEMO[key] = out
    return out
