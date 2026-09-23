"""conform.py — resources an edit adds, named the way the document names them.

An edit adds resources: a font the redraw sets new text in, a Form XObject
the re-wrap or redraw draws through. MuPDF names them after itself or the
font file — /fzFrm0, /fullpage, /LiberationSans, /HelveticaNeueLTStd_Roman —
in documents whose own names are /F147, /T1_0, /R8 or /F1. Nothing on screen
shows it; any look at the page's resources does ("this page was touched by
MuPDF"). Every name the input never had is renamed to the document's own
pattern for its category: the commonest prefix, the next free number.
"""
import re
from collections import Counter

import fitz

_CATS = ("Font", "XObject", "ExtGState")
_NAME_RE = re.compile(r"/([^\s/<>\[\]()]+)\s+(\d+\s+0\s+R|<<)")
_PAT = re.compile(r"^(.*?)(\d+)$")
_DEFAULT = {"Font": "F", "XObject": "Fm", "ExtGState": "GS"}


def _res_holders(doc):
    """(holder xref, key path to the category dict, category) for every
    resource dictionary in the file — a page's or a form's, inline or
    indirect."""
    out = []
    for x in range(1, doc.xref_length()):
        try:
            keys = doc.xref_get_keys(x)
        except Exception:  # noqa: BLE001
            continue
        if "Resources" in keys:
            kind, val = doc.xref_get_key(x, "Resources")
            if kind == "xref":
                holder, prefix = int(val.split()[0]), ""
            else:
                holder, prefix = x, "Resources/"
        elif any(c in keys for c in _CATS) and "Type" not in keys:
            holder, prefix = x, ""            # an indirect resources dictionary
        else:
            continue
        for cat in _CATS:
            kind, val = doc.xref_get_key(holder, prefix + cat)
            if kind == "xref":
                out.append((int(val.split()[0]), "", cat))
            elif kind == "dict":
                out.append((holder, prefix + cat + "/", cat))
    return list(dict.fromkeys(out))


def _names(doc, holder, path):
    if path:
        kind, val = doc.xref_get_key(holder, path.rstrip("/"))
        text = val if kind == "dict" else ""
    else:
        text = doc.xref_object(holder, compressed=True)
    return [m.group(1) for m in _NAME_RE.finditer(text)]


def conform_names(src: bytes, out: bytes) -> bytes:
    """*out* with every resource name the input never had renamed to the
    input's own pattern, and every font the edit added named the way a
    producer names fonts. Returns *out* unchanged on any doubt."""
    try:
        out = _conform(src, out)
    except Exception:  # noqa: BLE001 — naming is cosmetic; never fail an edit
        pass
    try:
        out = _conform_fonts(src, out)
    except Exception:  # noqa: BLE001
        pass
    return out


_TAG = re.compile(r"^[A-Z]{6}\+")


def _ps_name(doc, fd_xref, fallback: str) -> str:
    """The PostScript name of the font program behind a descriptor (name ID
    6), else *fallback* made into one: no spaces, style after a hyphen."""
    for key in ("FontFile2", "FontFile3", "FontFile"):
        kind, val = doc.xref_get_key(fd_xref, key)
        if kind != "xref":
            continue
        try:
            import io
            from fontTools.ttLib import TTFont
            data = doc.xref_stream(int(val.split()[0]))
            if key == "FontFile3" and doc.xref_get_key(int(val.split()[0]), "Subtype")[1] == "/Type1C":
                from fontTools.cffLib import CFFFontSet
                cff = CFFFontSet()
                cff.decompile(io.BytesIO(data), None)
                return cff.fontNames[0]
            f = TTFont(io.BytesIO(data), lazy=True)
            n = f["name"].getDebugName(6)
            if n:
                return _TAG.sub("", n)
        except Exception:  # noqa: BLE001
            pass
    base = _TAG.sub("", fallback).replace("#20", " ").strip()
    words = base.split()
    if len(words) > 1:
        family = "".join(w for w in words[:-1])
        style = words[-1]
        if "-" in family:                        # "HelveticaNeueLTStd-Roman Regula"
            return family
        return family + "-" + style
    return base


def _tag_value(tag):
    """A subset tag read as Skia writes them: a little-endian base-26
    counter, AAAAAA, BAAAAA, … ZAAAAA, ABAAAA."""
    return sum((ord(c) - 65) * 26 ** i for i, c in enumerate(tag))


def _next_tag(known, seed: str) -> str:
    """The subset tag the producer would give the next font: the next value
    of its counter where its tags ARE a counter (Chrome/Skia), else six
    letters drawn from the font's own bytes, as most producers do."""
    tags = [n[:6] for n in known if _TAG.match(n)]
    vals = sorted(_tag_value(t) for t in tags)
    if len(vals) >= 2 and vals[-1] < 26 * 26:
        v = vals[-1] + 1
        return "".join(chr(65 + (v // 26 ** i) % 26) for i in range(6))
    import hashlib
    h = hashlib.sha1(seed.encode()).digest()
    return "".join(chr(65 + b % 26) for b in h[:6])


def _conform_fonts(src, out):
    """Fonts the edit added, named like the document's own: MuPDF wraps even
    the document's OWN font program in a new Type0 named
    "HelveticaNeueLTStd-Roman Regula" (spaces, truncated), or names a
    substitute "Liberation Serif Regular". A producer writes the PostScript
    name — with a six-letter subset tag when that is the document's habit."""
    sdoc = fitz.open(stream=src, filetype="pdf")
    known = {f[3] for p in sdoc for f in p.get_fonts(full=True)}
    known_x = {f[0] for p in sdoc for f in p.get_fonts(full=True)}
    sdoc.close()
    tagged = sum(1 for n in known if _TAG.match(n))
    use_tag = known and tagged * 2 >= len(known)
    doc = fitz.open(stream=out, filetype="pdf")
    changed = False
    seen = set()
    pages = set()
    for p in doc:
        for f in p.get_fonts(full=True):
            x, base = f[0], f[3]
            if x in seen or base in known:
                continue
            seen.add(x)
            if x in known_x and " " not in base:
                continue
            obj = doc.xref_object(x, compressed=True)
            desc = None
            m = re.search(r"/DescendantFonts\s*\[\s*(\d+)\s+0\s+R", obj)
            if m:
                desc = int(m.group(1))
            holder = desc or x
            fk, fv = doc.xref_get_key(holder, "FontDescriptor")
            fd = int(fv.split()[0]) if fk == "xref" else None
            # A font with no embedded program is drawn from whatever the
            # viewer finds under its NAME: renaming it changes the glyphs
            # (measured: an 11pt overdraw on the arXiv paper).
            if fd is None or not any(doc.xref_get_key(fd, k)[0] == "xref"
                                     for k in ("FontFile", "FontFile2", "FontFile3")):
                continue
            cur = doc.xref_get_key(holder, "BaseFont")[1].lstrip("/")
            name = cur.replace("#20", " ")
            if " " in name or not name:
                name = _ps_name(doc, fd, name) if fd else name.replace(" ", "")
            if use_tag and not _TAG.match(name):
                name = _next_tag(known, obj + name) + "+" + name
                known.add(name)
            if not use_tag:
                name = _TAG.sub("", name) if not _TAG.match(cur) else name
            name = name.replace(" ", "")
            for target in {x, holder}:
                if doc.xref_get_key(target, "BaseFont")[1].lstrip("/") != name:
                    doc.xref_set_key(target, "BaseFont", "/" + name)
                    changed = True
            if fd and doc.xref_get_key(fd, "FontName")[1].lstrip("/") != name:
                doc.xref_set_key(fd, "FontName", "/" + name)
                changed = True
            if changed:
                pages.add(p.number)
    if not changed:
        doc.close()
        return out
    res = doc.tobytes(garbage=1, deflate=True)
    doc.close()
    # Proven: same pixels on every page that uses a renamed font.
    a, b = fitz.open(stream=out, filetype="pdf"), fitz.open(stream=res, filetype="pdf")
    try:
        for pn in pages:
            if a[pn].get_pixmap(dpi=72).samples != b[pn].get_pixmap(dpi=72).samples:
                return out
    finally:
        a.close()
        b.close()
    return res


def _conform(src, out):
    sdoc = fitz.open(stream=src, filetype="pdf")
    known = {c: set() for c in _CATS}
    for holder, path, cat in _res_holders(sdoc):
        known[cat].update(_names(sdoc, holder, path))
    sdoc.close()

    doc = fitz.open(stream=out, filetype="pdf")
    holders = _res_holders(doc)
    fresh = {c: set() for c in _CATS}
    for holder, path, cat in holders:
        fresh[cat].update(n for n in _names(doc, holder, path) if n not in known[cat])
    if not any(fresh.values()):
        doc.close()
        return out

    rename = {}
    taken = set().union(*known.values(), *fresh.values())
    for cat in _CATS:
        pats = Counter()
        nums = {}
        for n in known[cat]:
            m = _PAT.match(n)
            if m:
                pats[m.group(1)] += 1
                nums[m.group(1)] = max(nums.get(m.group(1), -1), int(m.group(2)))
        prefix = pats.most_common(1)[0][0] if pats else _DEFAULT[cat]
        k = nums.get(prefix, -1) + 1
        for n in sorted(fresh[cat]):
            while prefix + str(k) in taken:
                k += 1
            rename[(cat, n)] = prefix + str(k)
            taken.add(prefix + str(k))
            k += 1
    if not rename:
        doc.close()
        return out

    # The pages that use a renamed resource: the only ones whose pixels the
    # rename could change, and so the ones the proof below renders.
    olds = {n for _c, n in rename}
    touched = [p.number for p in doc
               if olds & ({f[4] for f in p.get_fonts(full=True)} | {x[1] for x in p.get_xobjects()})]
    # 1. the dictionaries, rewritten whole: a key set to null stays in an
    #    inline dictionary as "/fzFrm0 null" — the very name being removed.
    for holder, path, cat in holders:
        names = [n for n in _names(doc, holder, path) if (cat, n) in rename]
        if not names:
            continue
        text = (doc.xref_get_key(holder, path.rstrip("/"))[1] if path
                else doc.xref_object(holder, compressed=True))
        for n in names:
            text = re.sub(r"/" + re.escape(n) + r"(?=[\s/<\[(\d])", "/" + rename[(cat, n)], text)
        if path:
            doc.xref_set_key(holder, path.rstrip("/"), text)
        else:
            doc.update_object(holder, text)
    # 2. the content that uses them: every stream (pages' and forms'); a
    #    name the input never had can only appear in what the edit added.
    ops = {"Font": rb"Tf", "XObject": rb"Do", "ExtGState": rb"gs"}
    for x in range(1, doc.xref_length()):
        try:
            if not doc.xref_is_stream(x):
                continue
            keys = doc.xref_get_keys(x)
            if "Subtype" in keys and doc.xref_get_key(x, "Subtype")[1] not in ("/Form",):
                continue
            if "Length1" in keys or "Filter" in keys and doc.xref_get_key(x, "Filter")[1] \
                    in ("/DCTDecode", "/JPXDecode", "/CCITTFaxDecode", "/JBIG2Decode"):
                continue
            data = doc.xref_stream(x)
        except Exception:  # noqa: BLE001
            continue
        if not data:
            continue
        new_data = data
        for (cat, n), new in rename.items():
            pat = re.compile(rb"/" + re.escape(n.encode()) + rb"(?=[\s/\[<(])(?=[^\n]*?\b" +
                             ops[cat] + rb"\b)")
            new_data = pat.sub(b"/" + new.encode(), new_data)
        if new_data != data:
            doc.update_stream(x, new_data)
    res = doc.tobytes(garbage=1, deflate=True)
    doc.close()
    # Proven: same pixels on every page that uses a renamed resource.
    a, b = fitz.open(stream=out, filetype="pdf"), fitz.open(stream=res, filetype="pdf")
    try:
        for p in touched:
            if a[p].get_pixmap(dpi=60).samples != b[p].get_pixmap(dpi=60).samples:
                return out
    finally:
        a.close()
        b.close()
    return res
