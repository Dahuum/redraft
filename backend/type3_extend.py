"""
type3_extend.py — give a Type3 font the characters its subset does not have.

WHY THIS EXISTS
---------------
Browsers write a *variable* font as Type3: every glyph is its own little drawing procedure
(a charproc), the text is a run of one-byte codes, /Encoding /Differences names a procedure per
code and /Widths is in the font's own glyph space. There is no font program to inject into, so
none of the TrueType/CFF machinery applies, and an edit needing one new digit — "3,840.00" to
"4,950.00" — was refused, then redrawn in a stand-in font. That is a visible tell.

HOW
---
1. Read the font object (matrix, widths, encoding, charprocs, descriptor).
2. Rebuild the glyphs it HAS into a temporary TrueType font (cu2qu, y flipped, upem = 1/|FontMatrix|).
   That temporary font is exactly what `font_extend.extend_font` already expects of a "subset".
3. Fetch the genuine family at the descriptor's weight — a variable donor is pinned to that weight
   and to the optical size whose advance widths best match the widths measured in the document.
4. Let `extend_font` copy the missing characters in, then read their outlines back out and write
   each as a charproc (`w 0 llx lly urx ury d1`, path, `f`), extend /CharProcs, /Encoding,
   /Widths, /LastChar and /ToUnicode. Codes are allocated above /LastChar by the caller.

Only what is provable is done: any surprise (a glyph made of images or strokes, a skewed matrix, a
Widths array that is a reference we cannot rewrite) returns a reason and the caller falls back to
the honest refusal, exactly as for the other font types.
"""
from __future__ import annotations

import io
import re

import fitz
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.basePen import BasePen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_WEIGHT_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
                 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
_WORDS = {"thin", "extralight", "ultralight", "light", "regular", "medium", "semibold", "demibold",
          "bold", "extrabold", "ultrabold", "black", "heavy", "italic", "oblique", "book", "roman"}


# ── reading the font ────────────────────────────────────────────────────────────────────────
def find_font(doc, display_name: str):
    """xref of the Type3 font whose display name (see inplace_spike._fname) is *display_name*."""
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[2] == "Type3" and (f[3].split("+")[-1] or "%s (%s 0 R)" % (f[2], f[0])) == display_name:
                return f[0]
    return None


def _resolve(doc, xref, key):
    """Text of an object-valued key, following one indirect reference."""
    kind, val = doc.xref_get_key(xref, key)
    if kind == "xref":
        m = re.match(r"(\d+)\s+0\s+R", val)
        return (kind, int(m.group(1)), doc.xref_object(int(m.group(1)), compressed=True)) if m else None
    if kind in ("dict", "array"):
        return (kind, None, val)
    return None


def read_font(doc, xref):
    """The parts of a Type3 font this module needs, or None if the structure is not one it can rewrite."""
    try:
        if doc.xref_get_key(xref, "Subtype")[1] != "/Type3":
            return None
        fm = [float(x) for x in re.findall(_NUM, doc.xref_get_key(xref, "FontMatrix")[1] or "")]
        if len(fm) != 6 or abs(fm[1]) > 1e-9 or abs(fm[2]) > 1e-9 or abs(abs(fm[0]) - abs(fm[3])) > 1e-4 * abs(fm[0]):
            return None                                   # skewed or anisotropic: not handled
        first = int(doc.xref_get_key(xref, "FirstChar")[1])
        last = int(doc.xref_get_key(xref, "LastChar")[1])
        w = _resolve(doc, xref, "Widths")
        if not w or w[0] != "array":
            return None                                   # a Widths reference would need its own rewrite
        widths = [float(x) for x in re.findall(_NUM, w[2])]
        if len(widths) != last - first + 1:
            return None
        enc = _resolve(doc, xref, "Encoding")
        if not enc or enc[0] != "dict":
            return None
        m = re.search(r"/Differences\s*\[(.*?)\]", enc[2], re.S)
        if not m:
            return None
        diffs, code = {}, 0
        for tok in re.findall(r"/[^\s/\[\]<>()]+|\d+", m.group(1)):
            if tok.startswith("/"):
                diffs[code] = tok[1:]
                code += 1
            else:
                code = int(tok)
        cp = _resolve(doc, xref, "CharProcs")
        if not cp or cp[0] != "dict":
            return None
        procs = {n: int(x) for n, x in re.findall(r"/([^\s/\[\]<>()]+)\s+(\d+)\s+0\s+R", cp[2])}
        tu = doc.xref_get_key(xref, "ToUnicode")
        tu_xref = int(re.match(r"(\d+)", tu[1]).group(1)) if tu[0] == "xref" else None
        fd = {}
        kind, val = doc.xref_get_key(xref, "FontDescriptor")
        if kind == "xref":
            fdx = int(re.match(r"(\d+)", val).group(1))
            for k in ("FontFamily", "FontWeight", "FontName", "ItalicAngle", "StemV"):
                fd[k] = doc.xref_get_key(fdx, k)[1]
        return {"xref": xref, "fm": fm, "first": first, "last": last, "widths": widths,
                "enc_kind": enc[0], "enc_xref": enc[1], "enc_text": enc[2], "diffs": diffs,
                "cp_xref": cp[1], "procs": procs, "tu_xref": tu_xref, "fd": fd,
                "upem": round(1.0 / abs(fm[0])), "flip": fm[3] < 0}
    except Exception:  # noqa: BLE001 — anything odd means "not provable"
        return None


# ── charproc <-> outline ───────────────────────────────────────────────────────────────────
_OPS = re.compile(rb"(" + _NUM.encode() + rb")|([A-Za-z][A-Za-z0-9*]*)")


def parse_charproc(data: bytes):
    """(advance, [(op, points...)]) for a path-only charproc, or None when it draws anything else."""
    stack, segs, adv = [], [], None
    cur = None
    for m in _OPS.finditer(data):
        if m.group(1) is not None:
            stack.append(float(m.group(1)))
            continue
        op = m.group(2).decode("latin-1")
        if op in ("d0", "d1"):
            adv = stack[0] if stack else None
        elif op == "m":
            cur = (stack[-2], stack[-1]); segs.append(("m", cur))
        elif op == "l":
            cur = (stack[-2], stack[-1]); segs.append(("l", cur))
        elif op == "c":
            p = stack[-6:]; segs.append(("c", (p[0], p[1]), (p[2], p[3]), (p[4], p[5]))); cur = (p[4], p[5])
        elif op == "v" and cur:
            p = stack[-4:]; segs.append(("c", cur, (p[0], p[1]), (p[2], p[3]))); cur = (p[2], p[3])
        elif op == "y":
            p = stack[-4:]; segs.append(("c", (p[0], p[1]), (p[2], p[3]), (p[2], p[3]))); cur = (p[2], p[3])
        elif op == "h":
            segs.append(("h",))
        elif op in ("f", "F", "f*", "n"):
            pass
        else:
            return None                                   # strokes, images, clips, colours: not a plain outline
        stack = []
    return adv, segs


def _draw(segs, pen, sy):
    """Replay parsed path segments into a fontTools pen, y scaled by *sy* (-1 flips down to up)."""
    open_ = False
    for s in segs:
        if s[0] == "m":
            if open_:
                pen.closePath()
            pen.moveTo((s[1][0], s[1][1] * sy)); open_ = True
        elif s[0] == "l":
            pen.lineTo((s[1][0], s[1][1] * sy))
        elif s[0] == "c":
            pen.curveTo(*[(p[0], p[1] * sy) for p in s[1:]])
        elif s[0] == "h":
            if open_:
                pen.closePath(); open_ = False
    if open_:
        pen.closePath()


def _tounicode_chars(doc, tu_xref):
    """{code: single character} from a font's /ToUnicode."""
    import inplace_spike as S
    if not tu_xref:
        return {}
    data = doc.xref_stream(tu_xref)
    return {c: s for c, s in S._parse_tounicode_cmap(data).items() if s and len(s) == 1}


def build_subset_font(doc, info):
    """A temporary TrueType font holding the glyphs the Type3 font already draws, or None."""
    upem = info["upem"]
    sy = -1.0 if info["flip"] else 1.0
    chars = _tounicode_chars(doc, info["tu_xref"])
    order, glyphs, adv, cmap = [".notdef"], {}, {".notdef": upem // 2}, {}
    pen = TTGlyphPen(None)
    glyphs[".notdef"] = pen.glyph()
    for code, ch in sorted(chars.items()):
        name = info["diffs"].get(code)
        x = info["procs"].get(name) if name else None
        if x is None or name == "g0":
            continue
        parsed = parse_charproc(doc.xref_stream(x))
        if not parsed or not parsed[1]:
            continue
        gname = "uni%04X" % ord(ch)
        if gname in glyphs:
            continue
        p = TTGlyphPen(None)
        _draw(parsed[1], Cu2QuPen(p, 1.0, reverse_direction=False), sy)
        glyphs[gname] = p.glyph()
        w = info["widths"][code - info["first"]] if 0 <= code - info["first"] < len(info["widths"]) else parsed[0] or upem // 2
        adv[gname] = int(round(w))
        cmap[ord(ch)] = gname
        order.append(gname)
    if len(order) < 4:
        return None
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (adv.get(n, 0), 0) for n in order})
    fb.setupHorizontalHeader(ascent=int(upem * 0.8), descent=-int(upem * 0.2))
    fb.setupNameTable({"familyName": "T3", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue(), {ch: info["widths"][c - info["first"]] for c, ch in chars.items()
                            if 0 <= c - info["first"] < len(info["widths"]) and info["widths"][c - info["first"]]}


# ── the donor ──────────────────────────────────────────────────────────────────────────────
def _family_and_style(fd):
    fam = (fd.get("FontFamily") or "").strip("() ")
    words = [w for w in re.split(r"\s+", fam) if w and w.lower() not in _WORDS and not re.match(r"^\d+pt$", w.lower())]
    family = "".join(words) if words else re.sub(r"[^A-Za-z0-9]", "", (fd.get("FontName") or "").split("+")[-1].split("-")[0])
    try:
        wt = int(float(fd.get("FontWeight") or 400))
    except ValueError:
        wt = 400
    italic = False
    try:
        italic = abs(float(fd.get("ItalicAngle") or 0)) > 1.0
    except ValueError:
        pass
    style = _WEIGHT_NAMES.get(min(900, max(100, int(round(wt / 100.0)) * 100)), "Regular")
    return family, wt, style, italic


_DROP = ("GPOS", "GSUB", "GDEF", "kern", "DSIG", "BASE", "JSTF", "MATH", "STAT", "meta", "SVG ", "COLR", "CPAL")


def _lean(raw: bytes) -> TTFont:
    """The donor with the layout tables removed. Only outlines and advances are used from it,
    and compiling a variable font's GPOS once per optical-size candidate took a minute."""
    t = TTFont(io.BytesIO(raw))
    for tag in _DROP:
        if tag in t:
            del t[tag]
    return t


_PIN_CACHE: dict = {}


def _pin_variable(raw: bytes, weight: int, ref_widths: dict, upem_out: int):
    """A static instance of a variable donor: weight from the descriptor, optical size by width match."""
    import hashlib
    from fontTools.varLib import instancer
    key = (hashlib.sha1(raw).hexdigest(), weight, tuple(sorted(ref_widths.items())), upem_out)
    if key in _PIN_CACHE:
        return _PIN_CACHE[key]
    probe = _lean(raw)
    axes = {a.axisTag: a for a in probe["fvar"].axes}
    loc = {t: a.defaultValue for t, a in axes.items()}
    if "wght" in axes:
        loc["wght"] = min(max(weight, axes["wght"].minValue), axes["wght"].maxValue)
    cands = [None]
    if "opsz" in axes:
        lo, hi = axes["opsz"].minValue, axes["opsz"].maxValue
        cands = [lo + (hi - lo) * k / 4.0 for k in range(5)]
    best, best_err = None, 1e18
    for o in cands:
        l2 = dict(loc)
        if o is not None:
            l2["opsz"] = o
        try:
            inst = instancer.instantiateVariableFont(_lean(raw), l2, inplace=True)
        except Exception:  # noqa: BLE001
            continue
        cm, hm, up = inst.getBestCmap(), inst["hmtx"], inst["head"].unitsPerEm
        err, n = 0.0, 0
        for ch, w in ref_widths.items():
            g = cm.get(ord(ch))
            if g and g in hm.metrics:
                err += abs(hm[g][0] * upem_out / up - w) / max(w, 1); n += 1
        if n and err / n < best_err:
            best, best_err = inst, err / n
    _PIN_CACHE[key] = (best, best_err if best is not None else None)
    return _PIN_CACHE[key]


def _save(tt):
    b = io.BytesIO(); tt.save(b); return b.getvalue()


def get_donor(fd, ref_widths, upem):
    """(donor TTFont, report) — the genuine family, pinned to this document's weight — or (None, reason)."""
    import font_extend
    family, weight, style, italic = _family_and_style(fd)
    if not family:
        return None, "no_family"
    name = "%s-%s%s" % (family, style if style != "Regular" else ("Italic" if italic else "Regular"),
                        "Italic" if italic and style != "Regular" else "")
    try:
        raw, kind = font_extend.resolve_donor_detailed(name)
    except Exception:  # noqa: BLE001
        return None, "no_donor"
    if not raw or kind != "family":
        return None, "no_donor"          # a substitute typeface is never dropped into a page unmeasured
    tt = TTFont(io.BytesIO(raw))
    if "fvar" in tt:
        tt, err = _pin_variable(raw, weight, ref_widths, upem)
        if tt is None:
            return None, "no_donor"
        if err is not None and err > 0.06:
            return None, "donor_mismatch"   # its advance widths disagree with the document's by >6% on average
    return tt, "family"


# ── writing back ───────────────────────────────────────────────────────────────────────────
class _PathOut(BasePen):
    def __init__(self, sy, glyphset):
        super().__init__(glyphset)             # composites (Å, é, ö…) need it to find their parts
        self.sy, self.ops, self.pts = sy, [], []

    def _pt(self, p):
        self.pts.append((p[0], p[1] * self.sy)); return "%s %s" % (_f(p[0]), _f(p[1] * self.sy))

    def _moveTo(self, p): self.ops.append("%s m" % self._pt(p))
    def _lineTo(self, p): self.ops.append("%s l" % self._pt(p))
    def _curveToOne(self, a, b, c): self.ops.append("%s %s %s c" % (self._pt(a), self._pt(b), self._pt(c)))

    def _qCurveToOne(self, q, p):
        p0 = self._getCurrentPoint()
        a = (p0[0] + 2.0 / 3 * (q[0] - p0[0]), p0[1] + 2.0 / 3 * (q[1] - p0[1]))
        b = (p[0] + 2.0 / 3 * (q[0] - p[0]), p[1] + 2.0 / 3 * (q[1] - p[1]))
        self._curveToOne(a, b, p)

    def _closePath(self): self.ops.append("h")
    def _endPath(self): pass


def _f(v):
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _charproc(font_tt, gname, sy):
    gs = font_tt.getGlyphSet()
    pen = _PathOut(sy, gs)
    gs[gname].draw(pen)
    if not pen.pts:
        return None
    xs, ys = [p[0] for p in pen.pts], [p[1] for p in pen.pts]
    adv = font_tt["hmtx"][gname][0]
    head = "%s 0 %s %s %s %s d1\n" % (_f(adv), _f(min(xs)), _f(min(ys)), _f(max(xs)), _f(max(ys)))
    return (head + "\n".join(pen.ops) + "\nf\n").encode("latin-1"), adv, (min(xs), min(ys), max(xs), max(ys))


def extend(doc, display_name: str, missing_chars, code_for: dict):
    """Add *missing_chars* to the Type3 font, each under the code in *code_for*.

    Returns (True, None) or (None, reason). The document is only modified when everything
    needed has been produced.
    """
    import font_extend
    xref = find_font(doc, display_name)
    info = read_font(doc, xref) if xref else None
    if not info:
        return None, "unsupported_structure"
    sub = build_subset_font(doc, info)
    if not sub:
        return None, "unsupported_structure"
    subset_bytes, ref_widths = sub
    donor, why = get_donor(info["fd"], ref_widths, info["upem"])
    if donor is None:
        return None, why
    try:
        res = font_extend.extend_font(subset_bytes, _save(donor), list(missing_chars))
    except Exception:  # noqa: BLE001
        return None, "no_donor"
    ext = TTFont(io.BytesIO(res["font_bytes"]))
    order = ext.getGlyphOrder()
    sy = -1.0 if info["flip"] else 1.0
    new = {}
    for ch in missing_chars:
        code = code_for.get(ch)
        if code is None or code > 255 or code in info["diffs"] or code <= info["last"]:
            return None, "no_code"
        try:
            made = _charproc(ext, order[res["gid"][ch]], sy)
        except Exception:  # noqa: BLE001
            made = None
        if not made:
            return None, "empty_glyph"
        new[ch] = (code,) + made
    # ── all produced: now write ──
    names = set(info["procs"])
    n = 0
    diffs_add, procs_add, tu = [], {}, {}
    for ch, (code, stream, adv, bb) in new.items():
        while "gx%X" % n in names:
            n += 1
        name = "gx%X" % n
        names.add(name)
        x = doc.get_new_xref()
        doc.update_object(x, "<<>>")
        doc.update_stream(x, stream)
        procs_add[name] = x
        diffs_add.append((code, name))
        tu[code] = ord(ch)
    last = max(info["last"], max(c for c, _ in diffs_add))
    widths = list(info["widths"]) + [0.0] * (last - info["last"])
    for ch, (code, stream, adv, bb) in new.items():
        widths[code - info["first"]] = float(adv)
    # /CharProcs, /Encoding, /Widths, /LastChar, /FontBBox
    _, _, cp_text = _resolve(doc, xref, "CharProcs")
    cp_new = cp_text.rstrip().rstrip(">").rstrip() + " " + " ".join("/%s %d 0 R" % (k, v) for k, v in procs_add.items()) + " >>"
    m = re.search(r"/Differences\s*\[(.*?)\]", info["enc_text"], re.S)
    add = " ".join("%d /%s" % (c, nm) for c, nm in sorted(diffs_add))
    enc_new = info["enc_text"][:m.end(1)] + " " + add + info["enc_text"][m.end(1):]
    wtxt = "[" + " ".join(_f(w) for w in widths) + "]"
    doc.xref_set_key(xref, "CharProcs", cp_new)
    if info["enc_xref"]:
        doc.update_object(info["enc_xref"], enc_new)
    else:
        doc.xref_set_key(xref, "Encoding", enc_new)
    doc.xref_set_key(xref, "Widths", wtxt)
    doc.xref_set_key(xref, "LastChar", str(last))
    # /FontBBox is deliberately left alone: each charproc's own d1 box is what clips, and
    # MuPDF (and so anything that reads text boxes) derives every span's ascent and descent
    # from the font box — widening it moved the boxes of text nobody edited by 3 pt.
    if info["tu_xref"]:
        import inplace_spike as S
        S._add_tounicode_entries(doc, info["tu_xref"], tu, hex_digits=2)
    return True, None


def donor_bytes(pdf_bytes: bytes, xref: int):
    """The genuine family of a Type3 font at its own weight, as font-file bytes, or None.

    For the REDRAW engine: a Type3 font has no font program to reuse, so a value redrawn in it
    fell back to Helvetica. The family it was made from (Merriweather, Manrope...) is the
    honest stand-in — same letterforms, width-matched to the document's own advances.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            info = read_font(doc, xref)
            sub = build_subset_font(doc, info) if info else None
        finally:
            doc.close()
        if not sub:
            return None
        donor, why = get_donor(info["fd"], sub[1], info["upem"])
        return _save(donor) if donor is not None else None
    except Exception:  # noqa: BLE001
        return None
