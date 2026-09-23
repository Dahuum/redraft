"""reading_order.py — put redrawn text back where the old text was in the
page's content stream.

Text extraction in MuPDF, PDFium (Chrome) and pdf.js (Firefox) follows the
order in which the content stream DRAWS text, not where it lands. The redraw
engine deletes the old glyphs and draws the replacement from a Form XObject
invoked in a stream appended to the page (`q /fzFrm0 Do Q`) — so the edited
words were drawn last, and copy, search and screen readers found them after
the rest of their line, or at the very end of the page: on the Word
attestation, "Est inscrit(e) dans notre éco" extracted at character 660 of
690, below the signature. Nothing on screen shows it; any text layer does.

The fix moves each appended invocation into the original stream, directly
after the text object that drew the text the old field followed, wrapped in
`q <inverse CTM> cm … Q` so it lands exactly where it was drawn before. The
insertion point is found by probing: each candidate is checked by MuPDF's own
text trace, which is the order being repaired.
"""
import re

import fitz

_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_STUB = re.compile(rb"^\s*(?:q\s+(?:[-+.\d]+\s+){6}cm\s+)?q?\s*/([^\s/\[\]<>()]+)\s+Do\s+Q?\s*(?:Q\s*)?$")


def _ops(data: bytes):
    """(op, start, end, operands) for every operator, skipping inline image
    data. *start* is where the operator's FIRST OPERAND begins — the offset
    an insertion must go before, or it splits operands from their keyword."""
    out, stack, i, n = [], [], 0, len(data)
    arg0 = None
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
            continue
        if c != 0x25 and arg0 is None:
            arg0 = i
        if c == 0x25:
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        if c == 0x28:
            depth, i = 1, i + 1
            while i < n and depth:
                if data[i] == 0x5C:
                    i += 2
                    continue
                depth += {0x28: 1, 0x29: -1}.get(data[i], 0)
                i += 1
            stack.append(None)
            continue
        if c == 0x3C and i + 1 < n and data[i + 1] == 0x3C:
            depth, i = 1, i + 2        # a dictionary operand (BDC properties)
            while i < n and depth:
                if data[i:i + 2] == b"<<":
                    depth, i = depth + 1, i + 2
                elif data[i:i + 2] == b">>":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            stack.append(None)
            continue
        if c == 0x3C:
            i = data.find(b">", i) + 1 or n
            stack.append(None)
            continue
        if c in b"[]":
            i += 1
            continue
        if c == 0x2F:
            j, i = i, i + 1
            while i < n and data[i] not in _WS and data[i] not in _DELIM:
                i += 1
            stack.append(data[j:i])
            continue
        j = i
        while i < n and data[i] not in _WS and data[i] not in _DELIM:
            i += 1
        if i == j:
            i += 1
            continue
        tok = data[j:i]
        if tok[:1] in b"+-.0123456789":
            try:
                stack.append(float(tok))
            except ValueError:
                stack.append(None)
            continue
        out.append((tok, arg0 if arg0 is not None else j, i, stack))
        stack = []
        arg0 = None
        if tok == b"ID":                       # inline image: skip to EI
            k = data.find(b"EI", i)
            while k > 0 and not (data[k - 1:k] in _WS and (k + 2 >= n or data[k + 2:k + 3] in _WS)):
                k = data.find(b"EI", k + 2)
            i = n if k < 0 else k + 2
            out.append((b"EI", k, i, []))
    return out


def _mul(a, b):
    """PDF matrix product a × b (row-vector convention: a then b)."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _inv(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_))


_LINE_OPS = (b"Td", b"TD", b"Tm", b"T*", b"'", b'"')
_SHOW_OPS = (b"Tj", b"TJ")


def _array_splits(data: bytes, a: int, b: int):
    """Offsets inside the TJ array data[a:b] ("[" ... "]") where it may be
    cut in two: before every element but the first."""
    out, i, first = [], a + 1, True
    while i < b:
        c = data[i]
        if c in _WS:
            i += 1
            continue
        if c == 0x5D:                       # ]
            break
        if not first:
            out.append(i)
        first = False
        if c == 0x28:                       # (literal)
            depth, i = 1, i + 1
            while i < b and depth:
                if data[i] == 0x5C:
                    i += 2
                    continue
                depth += {0x28: 1, 0x29: -1}.get(data[i], 0)
                i += 1
        elif c == 0x3C:                     # <hex>
            i = data.find(b">", i) + 1
        else:                               # a number
            while i < b and data[i] not in _WS and data[i] not in b"()<>[]":
                i += 1
    return out


def _slots(data: bytes):
    """Every place the stub may go, in stream order, as dicts:

      after_et  after a text object: plain `q … Do Q`;
      line      before a line-positioning operator inside a text object
                (pdfTeX draws a page as ONE): split `ET … BT <Tlm> Tm` —
                those operators work from the line matrix, restored exactly;
      show/arr  mid-line, before a show operator or between two elements of
                a TJ array: the split must also put the running text position
                back where it was, and no operator sets it apart from the
                line matrix — except a TJ holding only a number, which moves
                the text position and leaves the line matrix alone. Its value
                is measured after the slot is chosen (_finish).

    Never inside a marked-content sequence opened within the same text
    object: a split would leave it straddling BT/ET."""
    ctm, stack, out = (1, 0, 0, 1, 0, 0), [], []
    tlm, tl, in_bt = None, 0.0, False
    fs, th, md, md_bt = None, 1.0, 0, 0
    for op, s, e, args in _ops(data):
        nums = all(isinstance(x, float) for x in args)
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            if stack:
                ctm = stack.pop()
        elif op == b"cm" and len(args) == 6 and nums:
            ctm = _mul(tuple(args), ctm)
        elif op in (b"BDC", b"BMC"):
            md += 1
        elif op == b"EMC":
            md = max(0, md - 1)
        elif op == b"Tf" and len(args) == 2 and isinstance(args[1], float):
            fs = args[1]
        elif op == b"Tz" and len(args) == 1 and nums:
            th = args[0] / 100.0
        elif op == b"BT":
            in_bt, tlm, md_bt = True, (1, 0, 0, 1, 0, 0), md
        elif op == b"ET":
            in_bt = False
            out.append({"kind": "after_et", "off": e, "ctm": ctm})
        elif op == b"TL" and len(args) == 1 and nums:
            tl = args[0]
        elif in_bt and tlm is not None and md == md_bt and op in _SHOW_OPS and fs:
            out.append({"kind": "show", "off": s, "ctm": ctm, "tlm": tlm, "fs": fs, "th": th})
            if op == b"TJ" and data[s:s + 1] == b"[":
                for k in _array_splits(data, s, e):
                    out.append({"kind": "arr", "off": k, "ctm": ctm, "tlm": tlm,
                                "fs": fs, "th": th})
        elif in_bt and op in _LINE_OPS and tlm is not None:
            if md == md_bt:
                out.append({"kind": "line", "off": s, "ctm": ctm, "tlm": tlm})
            if op in (b"Td", b"TD") and len(args) == 2 and nums:
                tlm = _mul((1, 0, 0, 1, args[0], args[1]), tlm)
                if op == b"TD":
                    tl = -args[1]
            elif op == b"Tm" and len(args) == 6 and nums:
                tlm = tuple(args)
            elif op in (b"T*", b"'", b'"'):
                tlm = _mul((1, 0, 0, 1, 0, -tl), tlm)
            else:
                tlm = None                 # can't follow it: no further slots here
    return out


def _fmt(m):
    return " ".join("%.6f" % v for v in m).encode()


def _insert(body: bytes, slot: dict, name: bytes, n: float = 0.0):
    """*body* with the stub `/name Do` placed at *slot*; *n* is the TJ
    number that restores the text position after a mid-line split."""
    inv = _inv(slot["ctm"])
    if inv is None:
        return None
    do = b"q %s cm /%s Do Q" % (_fmt(inv), name)
    off, kind = slot["off"], slot["kind"]
    if kind == "after_et":
        return body[:off] + b"\n" + do + b"\n" + body[off:]
    reopen = b"BT %s Tm" % _fmt(slot["tlm"])
    if kind == "line":
        return body[:off] + b"\nET\n" + do + b"\n" + reopen + b"\n" + body[off:]
    move = b" [%.4f] TJ" % n if abs(n) > 1e-9 else b""
    if kind == "show":
        return body[:off] + b"\nET\n" + do + b"\n" + reopen + move + b"\n" + body[off:]
    # arr: close the first half of the array, reopen it after the stub
    return (body[:off] + b"] TJ\nET\n" + do + b"\n" + reopen + move + b"\n[" + body[off:])


def _trace(doc, pno, exact=False):
    """Every glyph MuPDF extracts, in drawing order: (char, x, y). A trace
    span is a font run, not a line — pdfTeX's reach from one line into the
    field on the next — so the order is compared glyph by glyph."""
    out = []
    for t in doc[pno].get_texttrace():
        for ch in t["chars"]:
            if exact:
                out.append((ch[0], ch[2][0], ch[2][1]))
            else:
                out.append((ch[0], round(ch[2][0], 1), round(ch[2][1], 1)))
    return out


def _position_fix(doc, pno, slot, without_exact, first, n_drawn, tr):
    """The TJ number that puts the glyph after a mid-line split back where it
    was. With no fix, text after the split starts at the line matrix; the
    first body glyph after the stub shows how far it fell short."""
    k = tr.index(first) + n_drawn            # the first body glyph after the stub
    exact = _trace(doc, pno, exact=True)
    if k >= len(exact):
        return 0.0
    j = tr.index(first)                      # body glyphs before the stub
    if j >= len(without_exact) or exact[k][0] != without_exact[j][0]:
        return None
    dx = without_exact[j][1] - exact[k][1]
    dy = -(without_exact[j][2] - exact[k][2])          # page y runs down, PDF up
    a, b, c, d, _e, _f = _mul(slot["tlm"], slot["ctm"])  # text space -> user space
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    tx = (dx * d - dy * c) / det
    ty = (-dx * b + dy * a) / det
    if abs(ty) > 0.05:
        return None                          # not a move along the line
    return -tx * 1000.0 / (slot["fs"] * slot["th"])


def restore_order(before: bytes, after: bytes, pno: int) -> bytes:
    """Move every appended one-XObject text stub of *after* back to where
    the text it replaces was drawn in *before*. Returns *after* unchanged if
    anything can't be proven."""
    try:
        return _restore(before, after, pno)
    except Exception:  # noqa: BLE001 — order is cosmetic to the eye; never fail an edit
        return after


def _restore(before, after, pno):
    doc = fitz.open(stream=after, filetype="pdf")
    page = doc[pno]
    conts = page.get_contents()
    stubs = [(k, x) for k, x in enumerate(conts) if _STUB.match(doc.xref_stream(x) or b"")]
    if not stubs:
        return after
    main = [x for x in conts if (doc.xref_stream(x) or b"").strip() not in (b"q", b"Q")
            and not _STUB.match(doc.xref_stream(x) or b"")]
    if len(main) != 1:
        return after                    # only the common case: one body stream
    body_x = main[0]
    bdoc = fitz.open(stream=before, filetype="pdf")
    border = _trace(bdoc, pno)          # the ORIGINAL drawing order
    bdoc.close()

    for _, sx in stubs:
        stub = doc.xref_stream(sx)
        # What this stub draws: the glyphs that vanish without it.
        full = _trace(doc, pno)
        doc.update_stream(sx, b"")
        without = _trace(doc, pno)
        without_exact = _trace(doc, pno, exact=True)
        doc.update_stream(sx, stub)
        kept = set(without)
        drawn = [g for g in full if g not in kept]
        if not drawn:
            continue
        first = drawn[0]
        # Where the text it replaces began in the ORIGINAL order: the deleted
        # glyph nearest its first glyph, on its baseline.
        cands = [i for i, g in enumerate(border) if abs(g[2] - first[2]) <= 1.0 and g not in kept]
        if not cands:
            continue
        orig_i = min(cands, key=lambda i: (abs(border[i][1] - first[1]), i))
        # Back to the first glyph of that deleted run, then its predecessor
        # that the body still draws.
        while orig_i > 0 and border[orig_i - 1] not in kept and \
                abs(border[orig_i - 1][2] - first[2]) <= 1.0:
            orig_i -= 1
        pred = next((border[i] for i in range(orig_i - 1, -1, -1) if border[i] in kept), None)
        body = doc.xref_stream(body_x)
        slots = _slots(body)
        if pred is None:
            # It was the first text drawn on the page: it goes first again.
            name = _STUB.match(stub).group(1)
            nb = _insert(body, {"kind": "after_et", "off": 0, "ctm": (1, 0, 0, 1, 0, 0)}, name)
            doc.update_stream(body_x, nb)
            doc.update_stream(sx, b"")
            tr = _trace(doc, pno)
            if sorted(tr) != sorted(full) or tr.index(first) != 0:
                doc.update_stream(body_x, body)
                doc.update_stream(sx, stub)
            continue
        if not slots:
            continue

        name = _STUB.match(stub).group(1)

        def order_after(k, n=0.0):
            nb = _insert(body, slots[k], name, n)
            if nb is None:
                return None
            doc.update_stream(body_x, nb)
            doc.update_stream(sx, b"")
            return _trace(doc, pno)

        # Binary search: the first slot after which `pred` precedes the stub.
        # Drawing ORDER does not depend on geometry, so mid-line slots are
        # probed with no position fix; that is measured once a slot is chosen.
        lo, hi, best = 0, len(slots) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            tr = order_after(mid)
            if tr is None:
                break
            if pred in tr and first in tr and tr.index(first) > tr.index(pred):
                best = mid
                hi = mid - 1
            else:
                lo = mid + 1
        tr = None
        if best is not None:
            tr = order_after(best)
            if slots[best]["kind"] in ("show", "arr") and tr is not None:
                n = _position_fix(doc, pno, slots[best], without_exact, first, len(drawn),
                                  tr)
                tr = order_after(best, n) if n is not None else None
        # Proven: the page draws the same glyphs, and the stub's text now
        # follows its predecessor directly.
        if tr is None or sorted(tr) != sorted(full) or tr.index(first) != tr.index(pred) + 1:
            doc.update_stream(body_x, body)
            doc.update_stream(sx, stub)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    # Moving where text is drawn must not change a pixel of the page.
    a_, b_ = fitz.open(stream=after, filetype="pdf"), fitz.open(stream=out, filetype="pdf")
    try:
        same = a_[pno].get_pixmap(dpi=96).samples == b_[pno].get_pixmap(dpi=96).samples
    finally:
        a_.close()
        b_.close()
    return out if same else after
