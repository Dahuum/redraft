"""
test_inplace.py — adversarial correctness tests for inplace_spike.py.

Builds PDFs that split text across MULTIPLE separate drawing instructions
(one Tj per word, one per character) — the real-world pattern that broke the
naive version — plus the deliberately-unsupported cases (kerning-split within
one instruction, ragged multi-run boundaries), and checks each gets the RIGHT
outcome: a correct edit with 0 pixels changed outside it, or an honest refusal
with the right reason.
"""
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
import inplace_spike as sp  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def find_ttf():
    for p in ["/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "/usr/share/fonts/TTF/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
        if os.path.exists(p):
            return p
    import subprocess
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans-serif"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if out and os.path.exists(out):
            return out
    except Exception:
        pass
    return None


TTF = find_ttf()
if not TTF:
    print("No system TTF found — cannot build CID test fixtures. Aborting.")
    sys.exit(1)
print(f"[using font] {TTF}\n")

_FONT = fitz.Font(fontfile=TTF)


def glyph_ids_for(text):
    return [_FONT.has_glyph(ord(ch)) for ch in text]


def hexstr(codes):
    return "".join("%04X" % g for g in codes)


def build_contiguous(phrase, size=13):
    """One clean TJ run — the easy baseline case."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 100), phrase, fontfile=TTF, fontname="F0", fontsize=size, color=(0.1, 0.1, 0.15))
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True)


def build_split_by_words(phrase, size=13):
    """The real-world hard case: one SEPARATE Tj per word (+ its own leading
    space), each its own precisely-positioned Tm — REAL font-metric advances,
    so PyMuPDF's own text extraction reads it as one continuous line (as it
    would for genuine design-tool output), while the content stream still
    represents it as many small instructions."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontfile=TTF, fontname="F0", fontsize=size)  # embeds the font
    words = phrase.split(" ")
    x = 72.0
    parts = [b"q BT /F0 %g Tf 0.1 0.1 0.15 rg" % size]
    for i, w in enumerate(words):
        piece = (" " if i > 0 else "") + w
        gids = glyph_ids_for(piece)
        parts.append(b"1 0 0 1 %.3f %g Tm <%s> Tj" % (x, 100, hexstr(gids).encode()))
        x += _FONT.text_length(piece, size)
    parts.append(b"ET Q")
    raw = b" ".join(parts)
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True)


def build_split_by_char(phrase, size=13):
    """Even more fragmented: one Tj per CHARACTER, real per-glyph advances —
    the true worst case (and exactly how ultra-precise custom letter-spacing
    gets flattened by some design tools)."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontfile=TTF, fontname="F0", fontsize=size)
    x = 72.0
    parts = [b"q BT /F0 %g Tf 0.1 0.1 0.15 rg" % size]
    for ch in phrase:
        gid = glyph_ids_for(ch)
        parts.append(b"1 0 0 1 %.3f %g Tm <%s> Tj" % (x, 100, hexstr(gid).encode()))
        x += _FONT.text_length(ch, size)
    parts.append(b"ET Q")
    raw = b" ".join(parts)
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True)


def build_kerning_split_within_run(phrase, size=13):
    """One TJ array, but the phrase is split via a kerning number INSIDE it —
    e.g. [<hexA> -40 <hexB>]TJ — the "custom letter-spacing inside one
    instruction" case, which we deliberately do NOT support."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontfile=TTF, fontname="F0", fontsize=size)
    mid = len(phrase) // 2
    a, b = phrase[:mid], phrase[mid:]
    ga, gb = glyph_ids_for(a), glyph_ids_for(b)
    raw = (b"q BT /F0 %g Tf 0.1 0.1 0.15 rg 1 0 0 1 72 100 Tm "
          b"[<%s> -40 <%s>]TJ ET Q" % (size, hexstr(ga).encode(), hexstr(gb).encode()))
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True)


def build_ragged_boundary(prefix, phrase, size=13):
    """First run mixes UNRELATED prefix text with the start of the target
    phrase in ONE token; the rest of the phrase is a separate, whole run.
    The match starts mid-token -> must be refused, not silently mis-edited."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontfile=TTF, fontname="F0", fontsize=size)
    mid = len(phrase) // 2
    a, b = phrase[:mid], phrase[mid:]
    g_first_run = glyph_ids_for(prefix + a)   # unrelated prefix + partial target, ONE token
    g_rest = glyph_ids_for(b)
    x2 = 72.0 + _FONT.text_length(prefix + a, size)
    raw = (b"q BT /F0 %g Tf 0.1 0.1 0.15 rg 1 0 0 1 72 100 Tm <%s> Tj "
          b"1 0 0 1 %.3f 100 Tm <%s> Tj ET Q"
          % (size, hexstr(g_first_run).encode(), x2, hexstr(g_rest).encode()))
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True)


def build_simple_font_split(phrase):
    """Simple (non-CID) font, text split across multiple Tj calls too, real
    font-metric advances (Helvetica base-14 — has known, exact widths)."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    helv = fitz.Font("helv")
    x = 72.0
    page.insert_text((72, 20), " ", fontname="helv", fontsize=13)  # register /helv
    parts = [b"q BT /helv 13 Tf 0.1 0.1 0.15 rg"]
    for i, w in enumerate(phrase.split(" ")):
        piece = (" " if i > 0 else "") + w
        parts.append(b"1 0 0 1 %.3f 100 Tm (%s) Tj" % (x, piece.encode("latin-1")))
        x += helv.text_length(piece, 13)
    parts.append(b"ET Q")
    raw = b" ".join(parts)
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    return doc.tobytes(garbage=4, deflate=True)


def build_word_gap_split(phrase, size=13):
    """One TJ array, one string token per WORD, with NO literal space
    character anywhere — the inter-word gap is pure positioning, e.g.
    [(Google)-250(Brain)]TJ. This is exactly how dvips/pdfTeX (and other
    space-saving typesetters) draw ordinary justified text with a SIMPLE
    (non-CID) font; a search for the phrase WITH its normal spaces must
    still find it. (Scoped to simple fonts — see _SPACE_GAP_THRESHOLD's use
    in _flatten — since that's where this was found on a real document; a
    CID font never renders a space glyph either way in this synthetic
    fixture, which is a separate, not-yet-addressed gap.)"""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontname="helv", fontsize=size)
    words = phrase.split(" ")
    parts = [b"q BT /helv %g Tf 0.1 0.1 0.15 rg 1 0 0 1 72 100 Tm [" % size]
    for i, w in enumerate(words):
        if i > 0:
            parts.append(b"-250")
        parts.append(b"(%s)" % w.encode("latin-1"))
    parts.append(b"]TJ ET Q")
    raw = b"".join(parts)
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    return doc.tobytes(garbage=4, deflate=True)


def build_octal_escaped_literal(phrase):
    """A simple-font literal string where the FIRST character is written as a
    PDF octal escape (\\ddd) instead of the raw byte — the same mechanism
    real PDF writers use for non-printable font codes (e.g. a ligature glyph
    packed into byte 2, written as the four characters \\002). Uses a
    printable stand-in so the fixture doesn't need a custom-encoded font,
    but exercises the exact same tokenizer path."""
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontname="helv", fontsize=13)
    escaped_first = ("\\%03o" % ord(phrase[0])).encode()
    rest = phrase[1:].encode("latin-1")
    raw = (b"q BT /helv 13 Tf 0.1 0.1 0.15 rg 1 0 0 1 72 100 Tm (%s%s) Tj ET Q"
          % (escaped_first, rest))
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    return doc.tobytes(garbage=4, deflate=True)


def build_winansi_special_chars(phrase):
    """A simple font declaring /Encoding /WinAnsiEncoding (PyMuPDF's own
    base-14 Helvetica does this) with text containing characters common in
    everyday documents but OUTSIDE Latin-1's range in the 0x80-0x9F byte
    slots WinAnsiEncoding (~= Windows-1252) actually uses for them: a smart
    quote, an en-dash, a bullet. `text.encode("latin-1")` raises outright on
    these — this is a real, ubiquitous Word/Acrobat-document pattern, not an
    edge case. Writes the WinAnsi byte codes directly (page.insert_text()'s
    own Unicode handling for base-14 fonts substitutes an unrelated
    placeholder glyph for them rather than the real WinAnsi byte — a
    fixture-construction quirk, not something being tested here)."""
    winansi_byte = {"’": 0x92, "–": 0x96, "•": 0x95}
    doc = fitz.open(); page = doc.new_page(width=595, height=200)
    page.insert_text((72, 20), " ", fontname="helv", fontsize=13)  # registers /helv
    raw_bytes = bytes(winansi_byte.get(ch, ord(ch)) for ch in phrase)
    raw = (b"q BT /helv 13 Tf 0.1 0.1 0.15 rg 1 0 0 1 72 100 Tm (%s) Tj ET Q" % raw_bytes)
    xref = page.get_contents()[0]
    doc.update_stream(xref, raw)
    return doc.tobytes(garbage=4, deflate=True)


def prove(label, pdf_bytes, old, new, expect_ok, expect_reason=None, expect_case=None):
    r = sp.edit(pdf_bytes, old, new)
    shown = {k: v for k, v in r.items() if k != "pdf_b64"}
    print(f"  [{label}] {shown}")
    check(f"{label}: ok={expect_ok}", r.get("ok") == expect_ok, str(shown))
    if expect_ok:
        check(f"{label}: guarantee (0 px outside)", r.get("guarantee") is True, str(shown))
        if expect_case:
            check(f"{label}: case={expect_case}", r.get("case") == expect_case, str(shown))
    else:
        if expect_reason:
            check(f"{label}: reason={expect_reason}", r.get("reason") == expect_reason, str(shown))
    return r


PHRASE = "Facture N W2026 04 089"

print("=== 1) contiguous (regression — must still work) ===")
base = build_contiguous(PHRASE)
prove("contiguous", base, "N W2026 04 089", "N W2099 99 999", True, expect_case="single_token")

print("\n=== 2) split per WORD — the real-world hard case ===")
words = build_split_by_words(PHRASE)
prove("word-split (full phrase)", words, PHRASE, "Facture N W2099 99 999", True, expect_case="multi_run")

print("\n=== 3) split per CHARACTER — the extreme case ===")
chars = build_split_by_char(PHRASE)
prove("char-split (full phrase)", chars, PHRASE, "Facture N W2099 99 999", True, expect_case="multi_run")

print("\n=== 4) kerning-split WITHIN one instruction — rebuilt, and disclosed ===")
# This used to be refused outright. It no longer is: the match covers BOTH
# string tokens completely, so rewriting them as one token discards only the
# kerning nudge BETWEEN them — spacing that belonged to the text being
# replaced. Refusing over it was the largest category of otherwise-editable
# fields (18 of 55 refusals across four real documents).
#
# What must still hold: the replacement is exactly what was asked for, every
# character of it actually draws, nothing outside the field moves, and the
# dropped adjustment is REPORTED rather than silently applied. Partial token
# coverage is a different matter and stays refused — see case 5.
kern = build_kerning_split_within_run(PHRASE)
rk = sp.edit(kern, PHRASE, "Facture N W2099 99 999")
print("  [kerning-within-run]", {k: v for k, v in rk.items() if k != "pdf_b64"})
check("kerning-within-run: now edited", rk.get("ok") is True)
check("kerning-within-run: reported as de-kerned", rk.get("dekerned") is True)
check("kerning-within-run: names the adjustment it dropped",
      bool(rk.get("dropped_gaps")), f"{rk.get('dropped_gaps')}")
check("kerning-within-run: guarantee (0 px outside)", rk.get("diff_outside") == 0)
if rk.get("ok"):
    _kd = fitz.open(stream=base64.b64decode(rk["pdf_b64"]), filetype="pdf")
    check("kerning-within-run: extracts as the requested text",
          _kd[0].get_text().strip() == "Facture N W2099 99 999",
          repr(_kd[0].get_text().strip()))
    _blank = []
    for _b in _kd[0].get_text("rawdict")["blocks"]:
        for _l in _b.get("lines", []):
            for _sp in _l.get("spans", []):
                for _c in _sp["chars"]:
                    if not _c["c"].strip():
                        continue
                    _pm = _kd[0].get_pixmap(matrix=fitz.Matrix(8, 8),
                                            clip=fitz.Rect(*_c["bbox"]))
                    _buf, _ch = _pm.samples, _pm.n
                    if not any(min(_buf[i:i + min(3, _ch)]) < 200
                               for i in range(0, len(_buf), _ch)):
                        _blank.append(_c["c"])
    # The point of rebuilding a run is that it still DRAWS. Extraction alone
    # would report success for glyphs that render as nothing.
    check("kerning-within-run: every character actually has ink",
          not _blank, f"blank: {_blank}")
    _kd.close()

print("\n=== 5) ragged boundary (unrelated text sharing a token) — must be refused ===")
ragged = build_ragged_boundary("REF:", PHRASE)
prove("ragged-boundary", ragged, PHRASE, "Facture N W2099 99 999", False, expect_reason="ragged_multirun_boundary")

print("\n=== 6) simple (non-CID) font, split across Tj calls ===")
simple_split = build_simple_font_split("Reference REF 2026 001")
prove("simple-split", simple_split, "Reference REF 2026 001", "Reference REF 2099 777", True, expect_case="multi_run")

print("\n=== 7) missing-glyph / extend tier ===")
# 'é' is not in this fixture's subset. What SHOULD happen depends on whether a
# donor for the real family is reachable, and both outcomes are correct:
#
#   donor available  -> the glyph is injected and must actually DRAW. The
#                       fixture's font is Liberation Sans, so on a machine
#                       that has it installed the donor is the genuine
#                       typeface rather than a lookalike.
#   no donor         -> honest refusal, naming the reason.
#
# The one thing that must never happen is the middle case: an edit that
# reports success while rendering nothing, which is what encoding a character
# from /ToUnicode alone used to produce. Extraction cannot see that, so ink is
# measured directly.
r = sp.edit(base, "Facture", "Fécture")
print("  [extend]", {k: v for k, v in r.items() if k != "pdf_b64"})
if r.get("ok"):
    check("extend: injected the missing glyph", r.get("extended_chars") == ["é"],
          f"{r.get('extended_chars')}")
    check("extend: guarantee (0 px outside)", r.get("diff_outside") == 0)
    _ed = fitz.open(stream=base64.b64decode(r["pdf_b64"]), filetype="pdf")
    check("extend: extracts as the requested text",
          _ed[0].get_text().strip() == "Fécture N W2026 04 089",
          repr(_ed[0].get_text().strip()))
    _blank = []
    for _b in _ed[0].get_text("rawdict")["blocks"]:
        for _l in _b.get("lines", []):
            for _sp in _l.get("spans", []):
                for _c in _sp["chars"]:
                    if not _c["c"].strip():
                        continue
                    _pm = _ed[0].get_pixmap(matrix=fitz.Matrix(8, 8),
                                            clip=fitz.Rect(*_c["bbox"]))
                    _buf, _ch = _pm.samples, _pm.n
                    if not any(min(_buf[i:i + min(3, _ch)]) < 200
                               for i in range(0, len(_buf), _ch)):
                        _blank.append(_c["c"])
    check("extend: the injected glyph actually has ink", not _blank,
          f"blank: {_blank}")
    _ed.close()
else:
    check("extend: refusal names a reason", bool(r.get("reason")), f"{r}")
    check("extend: refusal names which characters were missing",
          bool(r.get("missing")), f"{r.get('missing')}")

print("\n=== 8) analyze() probe — per-field editability, no edit needed to see it ===")
a = sp.analyze(words)
for f in a["fields"]:
    print(f"   editable={f['editable']!s:5} “{f['text']}”" + (f"  [{f['limitation']}]" if not f['editable'] else ""))
check("analyze: word-split field reports editable=True", all(f["editable"] for f in a["fields"]))

a2 = sp.analyze(kern)
# Editable now, for the reason set out in case 4. The genuinely-unsupported
# shape — a match covering only PART of a token — is case 5.
check("analyze: kerning-split field now reports editable=True",
     all(f["editable"] for f in a2["fields"]),
     f"{[(f['text'][:24], f['editable']) for f in a2['fields']]}")

print("\n=== 9) word-space drawn as pure positioning, no space glyph (dvips/pdfTeX-style) ===")
gap_split = build_word_gap_split(PHRASE)
prove("word-gap-split", gap_split, PHRASE, "Facture N W2099 99 999", True, expect_case="multi_token")

print("\n=== 10) PDF octal-escaped literal-string byte (\\\\ddd) decodes correctly ===")
octal_fixture = build_octal_escaped_literal("Reference REF 2026 001")
prove("octal-escape", octal_fixture, "Reference REF 2026 001", "Reference REF 2099 777", True)

print("\n=== 11) WinAnsiEncoding special chars (smart quote / en-dash / bullet) ===")
winansi = build_winansi_special_chars("What’s New – test •")
prove("winansi-special-chars", winansi, "What’s New – test •", "What’s Old – demo •", True)

print(f"\n{'='*70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
