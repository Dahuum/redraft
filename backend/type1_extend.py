"""
type1_extend.py — give a Type 1 font subset the characters it does not have.

WHY THIS EXISTS
---------------
pdfTeX (LaTeX) embeds only the glyphs a document used, as a Type 1 font program. A LaTeX résumé
therefore has no "Y" if nobody in it has a "Y" in their name, and changing the name needed a
letter the font did not contain — the engine refused, and the redraw fallback had no Computer
Modern to draw with.

HOW
---
The subset is the same font as the original, just with most charstrings removed. So the missing
glyphs are taken from the ORIGINAL font (the AMS Computer Modern Type 1 files, SIL OFL) — not a
look-alike — as the very charstrings the producer had: same outlines, same hints, same advance.
Nothing is redrawn or converted.

  1. Split the subset's font program: clear text | eexec-encrypted private part | trailer.
  2. Parse the private part: /Subrs, /CharStrings, and the separators the producer used.
  3. Copy the wanted charstrings from the donor, plus every subroutine they call that the subset
     dropped (hint replacement), plus the two glyphs of an accented composite (seac).
  4. Add the new codes to the built-in /Encoding, re-encrypt, rewrite /Length1 /Length2.
  5. Add /Widths and /ToUnicode for the new codes.

Anything unexpected (a hex-encoded eexec section, a lenIV mismatch, a subroutine the subset kept
under different contents, StandardEncoding as the built-in encoding) returns a reason and the
caller refuses, exactly as for the other font types.
"""
from __future__ import annotations

import os
import re
import urllib.request

import fitz

_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".font_cache", "type1")

# The AMS Computer Modern originals (SIL OFL 1.1), one file per design size.
_DONOR_URLS = ("https://mirrors.ctan.org/fonts/amsfonts/pfb/{lc}.pfb",)
_AMS = re.compile(r"^(cm|lcirc|lcircle|msam|msbm|eufm|eurm|eusm|eufb|eurb|eusb)[a-z0-9]*$")


# ── Type 1 encryption ───────────────────────────────────────────────────────────────────────
def _decrypt(data: bytes, r: int, n: int) -> bytes:
    out = bytearray()
    for c in data:
        out.append(c ^ (r >> 8))
        r = ((c + r) * 52845 + 22719) & 0xFFFF
    return bytes(out[n:])


def _encrypt(plain: bytes, r: int, prefix: bytes) -> bytes:
    out = bytearray()
    for p in prefix + plain:
        c = p ^ (r >> 8)
        out.append(c)
        r = ((c + r) * 52845 + 22719) & 0xFFFF
    return bytes(out)


# ── the font program ───────────────────────────────────────────────────────────────────────
def split_program(data: bytes, length1: int | None = None, length2: int | None = None):
    """(clear, encrypted, trailer) of a Type 1 program — from PDF /Length1 /Length2, or a PFB."""
    if data[:1] == b"\x80":                                   # PFB segments
        segs, i = [], 0
        while i < len(data) and data[i] == 0x80 and data[i + 1] in (1, 2):
            n = int.from_bytes(data[i + 2:i + 6], "little")
            segs.append((data[i + 1], data[i + 6:i + 6 + n]))
            i += 6 + n
        clear = b"".join(b for t, b in segs[:1])
        enc = b"".join(b for t, b in segs if t == 2)
        trailer = b"".join(b for t, b in segs[2:] if t == 1)
        return clear, enc, trailer
    if length1 is None or length2 is None:
        return None
    return data[:length1], data[length1:length1 + length2], data[length1 + length2:]


_ENTRY = re.compile(rb"(?:dup\s+(\d+)|/(\S+))\s+(\d+)\s+(RD|-\|)\s")


class Program:
    """Parsed private section: subrs and charstrings as (still-encrypted) bytes, plus the text
    between them, kept verbatim so the rewritten font differs from the original only by what was
    added."""

    def __init__(self, clear: bytes, enc: bytes, trailer: bytes):
        self.clear, self.trailer = clear, trailer
        head = enc[:4]
        self.plain = _decrypt(enc, 55665, 4)
        self.prefix = _decrypt_prefix(enc)
        m = re.search(rb"/lenIV\s+(\d+)", self.plain)
        self.len_iv = int(m.group(1)) if m else 4
        self.subrs: dict = {}            # index -> (enc bytes, tail)
        self.subr_order: list = []
        self.chars: dict = {}            # name -> (enc bytes, tail)
        self.char_order: list = []
        self._parse()

    def _parse(self):
        pt = self.plain
        s_at = pt.find(b"/Subrs")
        c_at = pt.find(b"/CharStrings")
        if c_at < 0:
            raise ValueError("no /CharStrings")
        self.head = b""
        pos, mode = 0, None
        parts = []                       # ("text", bytes) | ("subr", i) | ("char", name)
        i = 0
        last_end = 0
        while True:
            m = _ENTRY.search(pt, i)
            if not m:
                break
            is_subr = m.group(1) is not None
            if not is_subr and m.start() < c_at:
                i = m.end()              # a "/lenIV 4 RD"-like false hit before /CharStrings
                continue
            if is_subr and (s_at < 0 or m.start() < s_at or m.start() > c_at):
                i = m.end()
                continue
            n = int(m.group(3))
            data = pt[m.end():m.end() + n]
            after = m.end() + n
            t = re.compile(rb"\s*(NP|\||ND|\|-|noaccess put|readonly put|noaccess def|readonly def)[^\n]*\n?").match(pt, after)
            tail_end = t.end() if t else after
            tail = pt[after:tail_end]
            text = pt[last_end:m.start()]
            parts.append(("text", text))
            if is_subr:
                idx = int(m.group(1))
                parts.append(("subr", idx, m.group(4), pt[m.start():m.end()].split(b" ", 2)[0]))
                self.subrs[idx] = (data, tail)
                self.subr_order.append(idx)
            else:
                name = m.group(2).decode("latin-1")
                parts.append(("char", name, m.group(4)))
                self.chars[name] = (data, tail)
                self.char_order.append(name)
            last_end = tail_end
            i = tail_end
        self.parts = parts
        self.after_text = pt[last_end:]
        self.rd = next((p[2] for p in parts if p[0] in ("subr", "char")), b"RD")

    # a charstring's decrypted bytes
    def plain_cs(self, enc: bytes) -> bytes:
        return _decrypt(enc, 4330, self.len_iv)

    def emit(self, add_chars: dict, set_subrs: dict, char_count_delta: int) -> bytes:
        """Private section with *add_chars* {name: enc} appended and *set_subrs* {i: enc} replaced."""
        out = bytearray()
        for p in self.parts:
            if p[0] == "text":
                out += p[1]
            elif p[0] == "subr":
                idx = p[1]
                data, tail = self.subrs[idx]
                data = set_subrs.get(idx, data)
                out += b"dup %d %d %s " % (idx, len(data), p[2]) + data + tail
            else:
                name = p[1]
                data, tail = self.chars[name]
                out += b"/%s %d %s " % (name.encode("latin-1"), len(data), p[2]) + data + tail
        # new charstrings go after the last existing one, in the producer's own style
        ref_tail = self.chars[self.char_order[-1]][1] if self.char_order else b" ND\n"
        for name, data in add_chars.items():
            out += b"/%s %d %s " % (name.encode("latin-1"), len(data), self.rd) + data + ref_tail
        out += self.after_text
        res = bytes(out)
        # the "/CharStrings N dict" count
        m = re.search(rb"(/CharStrings\s+)(\d+)(\s+dict)", res)
        if m:
            res = res[:m.start(2)] + str(int(m.group(2)) + char_count_delta).encode() + res[m.end(2):]
        return res

    def build(self, add_chars: dict, set_subrs: dict, new_encoding: dict):
        """(clear', encrypted', trailer, length1, length2)."""
        clear = self.clear
        if new_encoding:
            m = re.search(rb"(/Encoding\s+256\s+array.*?)(?<=\s)((?:readonly\s+)?def)\b", clear, re.S)
            if not m:
                raise ValueError("built-in encoding is not an editable array")
            adds = b"".join(b"dup %d /%s put\n" % (c, n.encode("latin-1")) for c, n in sorted(new_encoding.items()))
            clear = clear[:m.end(1)] + adds + clear[m.end(1):]
        plain = self.emit(add_chars, set_subrs, len(add_chars))
        enc = _encrypt(plain, 55665, self.prefix)
        return clear, enc, self.trailer, len(clear), len(enc)


def _decrypt_prefix(enc: bytes) -> bytes:
    """The four random plaintext bytes the producer put in front of the eexec section."""
    r = 55665
    out = bytearray()
    for c in enc[:4]:
        out.append(c ^ (r >> 8))
        r = ((c + r) * 52845 + 22719) & 0xFFFF
    return bytes(out)


# ── reading a charstring: which subroutines and glyphs does it call? ───────────────────────
def _walk(cs: bytes):
    """(callsubr indices, seac (asb, adx, ady, bchar, achar) or None, hsbw advance or None)."""
    stack, calls, seac, adv, ps = [], [], None, None, []
    i = 0
    while i < len(cs):
        v = cs[i]
        i += 1
        if v >= 32:
            if v <= 246:
                stack.append(v - 139)
            elif v <= 250:
                stack.append((v - 247) * 256 + cs[i] + 108); i += 1
            elif v <= 254:
                stack.append(-(v - 251) * 256 - cs[i] - 108); i += 1
            else:
                stack.append(int.from_bytes(cs[i:i + 4], "big", signed=True)); i += 4
            continue
        if v == 12:
            e = cs[i]; i += 1
            if e == 6 and len(stack) >= 5:                    # seac
                seac = tuple(stack[-5:]); stack = []
            elif e == 16 and len(stack) >= 2:                 # callothersubr
                num, n = stack.pop(), stack.pop()
                args = [stack.pop() for _ in range(min(n, len(stack)))]
                if num == 3 and args:
                    ps.append(args[0])                        # hint replacement: the subr number
                else:
                    ps.extend(args)
            elif e == 17:                                     # pop
                stack.append(ps.pop() if ps else 3)
            elif e == 12 and len(stack) >= 2:                 # div
                b, a = stack.pop(), stack.pop(); stack.append(a / b if b else 0)
            else:
                stack = []
            continue
        if v == 10 and stack:                                 # callsubr
            calls.append(int(stack.pop()))
        elif v == 13 and len(stack) >= 2:                     # hsbw
            adv = stack[-1]; stack = []
        elif v in (11, 14):
            stack = []
        else:
            stack = []
    return calls, seac, adv


# ── donors ──────────────────────────────────────────────────────────────────────────────
def donor_program(ps_name: str):
    """The complete original of *ps_name* (e.g. "CMR10") as a Program, or None."""
    lc = ps_name.split("+")[-1].lower()
    if not _AMS.match(lc):
        return None
    os.makedirs(_CACHE, exist_ok=True)
    path = os.path.join(_CACHE, lc + ".pfb")
    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        raw = None
        for url in _DONOR_URLS:
            try:
                req = urllib.request.Request(url.format(lc=lc), headers={"User-Agent": "redraft-type1/0.1"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = r.read()
                break
            except Exception:  # noqa: BLE001
                continue
        if not raw or raw[:1] != b"\x80":
            return None
        with open(path, "wb") as f:
            f.write(raw)
    try:
        parts = split_program(open(path, "rb").read())
        return Program(*parts) if parts else None
    except Exception:  # noqa: BLE001
        return None


def glyph_name(ch: str, available) -> str | None:
    from fontTools import agl
    cands = []
    n = agl.UV2AGL.get(ord(ch))
    if n:
        cands.append(n)
    cands += ["uni%04X" % ord(ch), ch]
    return next((c for c in cands if c in available), None)


# ── PDF glue ────────────────────────────────────────────────────────────────────────────
def find_font(doc, display_name: str):
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[2] == "Type1" and (f[3].split("+")[-1] or "") == display_name:
                return f[0], f[3]
    return None


def extend(doc, display_name: str, missing_chars, code_for: dict, refs=None):
    """Add *missing_chars* to the Type 1 subset named *display_name*, each under its code in
    *code_for*. Returns (True, None) or (None, reason). The document changes only on success."""
    import inplace_spike as S
    hit = find_font(doc, display_name)
    if not hit:
        return None, "no_font"
    xref, basefont = hit
    obj = doc.xref_object(xref, compressed=True)
    m = re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", obj)
    if not m:
        return None, "no_descriptor"
    fd = int(m.group(1))
    fdo = doc.xref_object(fd, compressed=True)
    m = re.search(r"/FontFile\s+(\d+)\s+0\s+R", fdo)
    if not m:
        return None, "no_fontfile"
    ff = int(m.group(1))
    if "/Encoding" in obj:
        return None, "pdf_encoding"          # codes come from the PDF's own /Encoding: not handled
    try:
        l1 = int(doc.xref_get_key(ff, "Length1")[1]); l2 = int(doc.xref_get_key(ff, "Length2")[1])
        sub = Program(*split_program(doc.xref_stream(ff), l1, l2))
    except Exception:  # noqa: BLE001
        return None, "unsupported_program"
    donor = donor_program(basefont)
    if donor is None:
        return None, "no_donor"
    if donor.len_iv != sub.len_iv:
        return None, "leniv_mismatch"

    # which glyphs
    want, names = {}, {}
    for ch in missing_chars:
        n = glyph_name(ch, donor.chars)
        if not n:
            return None, "glyph_not_in_donor"
        names[ch] = n
        want[n] = donor.chars[n][0]
    add = {n: d for n, d in want.items() if n not in sub.chars}
    # composites: seac names its two parts by StandardEncoding code
    from fontTools.encodings.StandardEncoding import StandardEncoding
    todo, subrs_needed = list(add), set()
    seen = set()
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        calls, seac, _ = _walk(donor.plain_cs(donor.chars[n][0]))
        stack = list(calls)
        while stack:
            k = stack.pop()
            if k in subrs_needed:
                continue
            subrs_needed.add(k)
            if k not in donor.subrs:
                return None, "subr_missing"
            stack += _walk(donor.plain_cs(donor.subrs[k][0]))[0]
        if seac:
            for code in (int(seac[3]), int(seac[4])):
                part = StandardEncoding[code]
                if part not in sub.chars and part not in add:
                    if part not in donor.chars:
                        return None, "seac_part_missing"
                    add[part] = donor.chars[part][0]
                    todo.append(part)
    set_subrs = {}
    for k in sorted(subrs_needed):
        d_enc = donor.subrs[k][0]
        if k in sub.subrs:
            s_enc = sub.subrs[k][0]
            if sub.plain_cs(s_enc) == donor.plain_cs(d_enc):
                continue
            if len(sub.plain_cs(s_enc)) > 1:              # not a stub: the subset really uses it
                return None, "subr_conflict"
            set_subrs[k] = d_enc
        else:
            return None, "subr_index_absent"
    codes = {}
    for ch in missing_chars:
        c = code_for.get(ch)
        if c is None or not (0 <= c <= 255) or c == 32:
            return None, "no_code"
        codes[c] = names[ch]
    try:
        clear, enc, trailer, l1n, l2n = sub.build(add, set_subrs, codes)
    except ValueError as e:
        return None, "encoding_not_editable" if "encoding" in str(e) else "build_failed"
    # widths from the donor's own hsbw
    widths = {}
    for c, n in codes.items():
        adv = _walk(donor.plain_cs(donor.chars[n][0]))[2]
        if adv is None:
            return None, "no_advance"
        widths[c] = float(adv)
    # ── all produced: now write ──
    if refs is None:
        refs = S._simple_font_refs(doc, display_name, require_truetype=False)
    if not refs or not S._set_simple_widths(doc, refs, widths, extend=True):
        return None, "bad_widths"
    stream = clear + enc + trailer
    doc.update_stream(ff, stream)
    doc.xref_set_key(ff, "Length1", str(l1n))
    doc.xref_set_key(ff, "Length2", str(l2n))
    doc.xref_set_key(ff, "Length3", str(len(trailer)))
    last = max(refs["last_char"], max(codes))
    doc.xref_set_key(xref, "LastChar", str(last))
    m = re.search(r"/CharSet\s*\((.*?)\)", fdo, re.S)
    if m:
        extra = "".join("/" + n for n in names.values() if "/" + n not in m.group(1))
        doc.xref_set_key(fd, "CharSet", "(" + m.group(1) + extra + ")")
    if refs.get("tu_xref"):
        S._add_tounicode_entries(doc, refs["tu_xref"], {c: ord(ch) for ch in missing_chars
                                                        for c in [code_for[ch]]}, hex_digits=2)
    return True, None
