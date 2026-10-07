"""test_real_defects.py — five defects real documents showed that the corpus never did.

1. Zeroed /Widths. A simple font advances by /Widths, not by its program, and some generators
   (OpenPDF/Jaspersoft, Word) zero the entry of every character the document never drew. The
   glyph still draws, so an edit "worked" — and then 'x' advanced by nothing and the next letter
   printed on top of it. The fixture is a NON-embedded Arial-BoldMT whose /Widths hold only the
   letters of the original text, edited through the real /extract + /edit path.

2. A full-measure left-aligned line redrawn centred. Its midpoint is the page centre, exactly
   where a centred footer's lines sit, and the footer outvoted the one line below it that
   shares its left edge.

3. Two edits on one page that each need glyphs the font subset never drew, in a font whose display
   name two PDF objects share (Word's TrueType object and its Type0 twin). The first edit
   extended the twin; the second then looked its glyph ids up by NAME and got the other
   object's, so a code collided and the second edit was refused as "sequence_not_found".

4. Letter-spacing in a one-glyph-per-Tj line (Canva): the tracking exists only as the part of
   each Td beyond the previous glyph's width, so new letters drawn in one TJ came out tighter
   than the letters around them.

5. A signature heading redrawn in Helvetica. MuPDF cuts a subset font's reported name to 31
   characters including the "ABCDEF+" tag, so "CAAAAA+BuongiornoRastellinoCyr-Script" reaches the
   editor as "BuongiornoRastellinoCyr-": no embedded font matches it and nothing says "script".

6. ReportLab's justified paragraphs. Helvetica is a standard-14 font there, with no /Widths, and
   every word is a text object of its own at an absolute position. Measured through the merged
   span MuPDF reports, a longer word looked like it overflowed its line: the app shrank the font
   and redrew the line. A word edit now stays in place at the document's size, the line's other
   words give up (or take) the difference so it still ends on the margin, and a bullet item that
   gains a word wraps like the producer would — its hanging "—" is the list's, not the paragraph's.

7. A donor lookup that failed once and then never again. The font catalogue is reached over the
   network; a timeout or the GitHub rate limit was cached as "this family has no donor", so every
   later edit in that font was refused (and redrawn at a different size) until the server restarted.


8. Ligatures in a Chrome print (examples/annex-cell.pdf). The stream draws "fi" as ONE glyph, and
   MuPDF reports it as 'f' (the ligature's id) then 'i' with id -1. The -1 overwrote the real id
   of every plain 'i', so a field needing a letter the subset never drew was looked up with a
   bogus code and refused ("sequence_not_found"); and a field that itself held the ligature
   ("Northfield") was encoded letter by letter, never found, and blamed on "more than one font".
   Both were redrawn in a look-alike at another size.

9. A Light letter in a Regular line. The installed Lato has only Light and Hairline files, and
   fontconfig lists "Regular" as a second style of each, so a Regular donor was taken from them:
   the weight veto allowed 150 either side and the tie went to the shortest file name. Every new
   capital ('O' in "Orion") came out visibly thinner than its neighbours.

10. A space typed at the end of a LaTeX field. Computer Modern has no space glyph (TeX moves the
   pen instead), so "Compute the cabs " asked for a glyph the font cannot have and the whole edit
   was refused as "no complete copy of the font" — for a character that draws nothing.

Fictional fixtures only (the repo's own examples/, plus a font from the donor cache).
"""
import base64
import json
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import api  # noqa: E402
from pdf_editor import _detect_alignments  # noqa: E402
import pdf_editor as _pe  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def zero_width_pdf(basefont, face, lines, drawn):
    """A page of non-embedded TrueType text whose /Widths cover only the characters in `drawn`."""
    ref = fitz.Font(face)
    widths = [round(ref.glyph_advance(c) * 1000) if chr(c) in drawn else 0 for c in range(32, 127)]
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    fd = doc.get_new_xref()
    doc.update_object(fd, "<< /Type /FontDescriptor /FontName /%s /Flags 32 /ItalicAngle 0 "
                          "/FontBBox [-628 -376 2000 1018] /Ascent 905 /Descent -212 "
                          "/CapHeight 716 /StemV 80 >>" % basefont)
    fx = doc.get_new_xref()
    doc.update_object(fx, "<< /Type /Font /Subtype /TrueType /BaseFont /%s /FirstChar 32 "
                          "/LastChar 126 /Widths [%s] /Encoding /WinAnsiEncoding "
                          "/FontDescriptor %d 0 R >>"
                      % (basefont, " ".join(map(str, widths)), fd))
    body = "".join("BT /F1 11 Tf 60 %d Td (%s) Tj ET\n" % (700 - 40 * i, t) for i, t in enumerate(lines))
    cx = doc.get_new_xref()
    doc.update_object(cx, "<< >>")
    doc.update_stream(cx, body.encode("latin-1"))
    doc.xref_set_key(page.xref, "Resources", "<< /Font << /F1 %d 0 R >> >>" % fx)
    doc.xref_set_key(page.xref, "Contents", "%d 0 R" % cx)
    out = doc.tobytes()
    doc.close()
    return out


def stacked_glyphs(pdf_bytes, size_frac=0.1):
    """Characters whose advance to the next non-space character is ~nothing: ink printed on ink."""
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    bad = []
    for blk in d[0].get_text("rawdict")["blocks"]:
        for ln in blk.get("lines", []):
            for sp in ln["spans"]:
                ch = [c for c in sp["chars"] if c["c"].strip()]
                for a, b in zip(ch, ch[1:]):
                    if abs(b["bbox"][3] - a["bbox"][3]) < 1 and b["origin"][0] - a["origin"][0] < size_frac * sp["size"]:
                        bad.append(a["c"] + b["c"])
    return bad


client = TestClient(api.app)


def edit_through_server(pdf, find, new):
    sp = client.post("/extract", files={"file": ("t.pdf", pdf)}).json()["spans"]
    idx = next(i for i, s in enumerate(sp) if s["text"].strip() == find)
    r = client.post("/edit", files={"file": ("t.pdf", pdf)},
                    data={"edits": json.dumps([{"index": idx, "new_text": new}])})
    rep = json.loads(base64.b64decode(r.headers["X-Redraft-Font-Report"]))
    return r.status_code, r.content, rep


# ── 1. zeroed /Widths ────────────────────────────────────────────────────────────────────────
for basefont, face, label in (("Arial-BoldMT", "hebo", "Arial-BoldMT"),
                              ("Helvetica-Bold", "hebo", "Helvetica-Bold")):
    src = zero_width_pdf(basefont, face, ["Bank agency", "Account holder"], "Bank agency Account holder")
    check("(fixture %s) the unedited page has no stacked glyphs" % label, not stacked_glyphs(src))
    status, out, rep = edit_through_server(src, "Bank agency", "Wxqzkjmb agency")
    check("%s: edit succeeds in place" % label,
          status == 200 and rep.get("in_place", {}).get("count") == 1, str(rep.get("in_place")))
    d = fitz.open(stream=out, filetype="pdf")
    check("%s: new text is on the page" % label, "Wxqzkjmb agency" in d[0].get_text())
    check("%s: no letter prints on top of another" % label, not stacked_glyphs(out),
          str(stacked_glyphs(out)))
    check("%s: the untouched line is untouched" % label, "Account holder" in d[0].get_text())

# ── 2. a full-measure left-aligned line is not "centred" because a footer shares its midpoint ──
R = fitz.Rect


def span(x0, x1, y, size=11.0):
    return {"bbox": R(x0, y - size, x1, y + 2), "origin": (x0, y), "size": size}


para = [span(50, 545, 100), span(50, 330, 114)]                 # left paragraph, full measure
footer = [span(150, 445, 700, 8), span(100, 495, 712, 8), span(200, 395, 724, 8)]  # centred footer
al = _detect_alignments(para + footer)
key = lambda s: (round(s["origin"][0], 1), round(s["origin"][1], 1))  # noqa: E731
check("full-measure first line of a left paragraph is 'left'", al[key(para[0])] == "left", al[key(para[0])])
check("its second line is 'left'", al[key(para[1])] == "left", al[key(para[1])])
check("the centred footer stays 'center'", all(al[key(s)] == "center" for s in footer),
      str([al[key(s)] for s in footer]))

title = [span(150, 445, 100, 20), span(100, 495, 124, 20), span(200, 395, 148, 20)]
al2 = _detect_alignments(title + [span(50, 545, 400)])
check("a centred title stack stays 'center' even with a full-width line elsewhere",
      all(al2[key(s)] == "center" for s in title), str([al2[key(s)] for s in title]))

col = [span(400, 540, 300), span(440, 540, 314), span(420, 540, 328), span(460, 540, 342)]
al3 = _detect_alignments(col)
check("a right-aligned figure column stays 'right'", all(al3[key(s)] == "right" for s in col),
      str([al3[key(s)] for s in col]))

# ── 3. two same-page edits in a font with a same-named twin object ─────────────────────────────
attestation = open(os.path.join(HERE, "..", "examples", "attestation-demo.pdf"), "rb").read()
sp_att = client.post("/extract", files={"file": ("a.pdf", attestation)}).json()["spans"]
di = next(i for i, s in enumerate(sp_att) if s["text"].strip() == "12/05/2001")
li = next(i for i, s in enumerate(sp_att) if s["text"].startswith("Larbi EL HILALI"))
name_new = sp_att[li]["text"].strip() + " Wxqz"
r = client.post("/edit", files={"file": ("a.pdf", attestation)},
                data={"edits": json.dumps([{"index": li, "new_text": name_new},
                                           {"index": di, "new_text": "Wxqzkjmb"}])})
rep = json.loads(base64.b64decode(r.headers["X-Redraft-Font-Report"]))
out_txt = fitz.open(stream=r.content, filetype="pdf")[sp_att[di]["page"]].get_text()
check("twin fonts: both edits go in place", rep.get("in_place", {}).get("count") == 2,
      str(rep.get("in_place")))
check("twin fonts: the second edit's text is on the page", "Wxqzkjmb" in out_txt)
check("twin fonts: the first edit's text is on the page", name_new in " ".join(out_txt.split()))

# ── 4. tracking carried over to new letters in a one-glyph-per-Tj line ──────────────────────────
import re  # noqa: E402
import font_extend  # noqa: E402

TRACK = 0.1
SIZE = 12.0


def canva_like_pdf(lines):
    donor = font_extend.resolve_donor("OpenSans-Regular")
    if donor is None:
        return None, None
    font = fitz.Font(fontbuffer=donor)
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    tw = fitz.TextWriter(page.rect)
    for i, t in enumerate(lines):
        tw.append((40, 60 + 40 * i), t, font=font, fontsize=SIZE)
    tw.write_text(page)
    doc.subset_fonts()
    xref = page.get_contents()[0]
    src = doc.xref_stream(xref).decode("latin-1")
    body = []
    for m, t in zip(re.finditer(r"/F0 [\d.]+ Tf\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n\[<([0-9a-f]+)>\]TJ", src), lines):
        x, y, hexs = m.group(1), m.group(2), m.group(3)
        codes = [hexs[k:k + 4] for k in range(0, len(hexs), 4)]
        body.append("BT /F0 %g Tf 1 0 0 1 %s %s Tm" % (SIZE, x, y))
        for j, (c, ch) in enumerate(zip(codes, t)):
            body.append("<%s> Tj" % c)
            if j + 1 < len(codes):
                body.append("%.5f 0 Td" % ((font.glyph_advance(ord(ch)) + TRACK) * SIZE))
        body.append("ET")
    doc.update_stream(xref, "\n".join(body).encode("latin-1"))
    return doc.tobytes(), font


def pitches(pdf_bytes, text):
    pg = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    for b in pg.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                if text in "".join(c["c"] for c in sp["chars"]):
                    return {c["c"]: round(n["origin"][0] - c["origin"][0], 2)
                            for c, n in zip(sp["chars"], sp["chars"][1:])}
    return None


canva, ref_font = canva_like_pdf(["Contract Closing 2030", "Due date 11.03.2030"])
if canva is None:
    print("SKIP - tracking (no donor font available offline)")
else:
    status, out, rep = edit_through_server(canva, "Contract Closing 2030", "Contract Closing Wxqzkj")
    check("tracking: edit succeeds in place", status == 200 and rep.get("in_place", {}).get("count") == 1,
          str(rep.get("in_place")))
    before, after = pitches(canva, "Contract Closing 2030"), pitches(out, "Contract Closing Wxqzkj")
    check("tracking: the edited line reads back", after is not None)
    if after:
        for ch in "Cnt":
            check("tracking: unedited %r keeps its pitch" % ch, abs(after[ch] - before[ch]) < 0.05,
                  "%s -> %s" % (before[ch], after[ch]))
        for ch in "xqz":
            want = (ref_font.glyph_advance(ord(ch)) + TRACK) * SIZE
            check("tracking: new %r advances like its neighbours (%.2f)" % (ch, want),
                  abs(after[ch] - want) < 0.1, "got %s" % after[ch])

# ── 5. a long script-font name survives MuPDF's truncation, and says "script" ───────────────────
import types  # noqa: E402
from pdf_editor import _style_substitute  # noqa: E402

fake_page = types.SimpleNamespace(get_fonts=lambda full=True: [
    (90, "ttf", "Type0", "CAAAAA+BuongiornoRastellinoCyr-Script", "F8", "Identity-H"),
    (91, "ttf", "Type0", "DAAAAA+OpenSans-Medium", "F9", "Identity-H")])
fake_self = types.SimpleNamespace(doc=[fake_page])
full = _pe.PDFEditor._full_font_name(fake_self, 0, "BuongiornoRastellinoCyr-")
check("a name MuPDF cut at 24 characters is restored from the page's font list",
      full == "BuongiornoRastellinoCyr-Script", full)
check("a name that is not cut is left alone",
      _pe.PDFEditor._full_font_name(fake_self, 0, "OpenSans-Medium") == "OpenSans-Medium")
check("a 24-character name that matches no embedded font is left alone",
      _pe.PDFEditor._full_font_name(fake_self, 0, "Abcdefghijklmnopqrstuvwx") == "Abcdefghijklmnopqrstuvwx")
check("a font named Script gets a script substitute, not Helvetica",
      (_style_substitute("BuongiornoRastellinoCyr-Script") or ("",))[0] == "Sacramento")
check("PostScript / Manuscript / Arial do not",
      not any(_style_substitute(n) for n in ("HelveticaPostScript", "Manuscript", "Arial")))

# ── 6. ReportLab-style justified paragraphs: std-14 Helvetica, no /Widths, one object per word ──
HELV = fitz.Font("helv")
HSIZE, LEFT, RIGHT, LEAD = 11.0, 76.54, 518.74, 16.0
HANG = 93.54


def hw(text):
    return sum(HELV.glyph_advance(ord(c)) for c in text) * HSIZE


def wrap(words, x0):
    lines, cur = [], []
    for w in words:
        trial = cur + [w]
        if cur and sum(hw(t) for t in trial) + hw(" ") * (len(trial) - 1) > RIGHT - x0:
            lines.append(cur)
            cur = [w]
        else:
            cur = trial
    return lines + [cur]


def reportlab_like(blocks):
    """blocks: (marker?, text). Greedy-wrapped, justified but for the last line of each."""
    # ReportLab sets the font once, in a text object of its own; the words that follow carry
    # only a position.
    ops, y, geom = ["1 0 0 1 0 0 cm  BT /F1 12 Tf 14.4 TL ET", "BT /F1 %g Tf %g TL ET" % (HSIZE, LEAD)], 760.0, []
    for marker, text in blocks:
        x0 = HANG if marker else LEFT
        ls = [t.split() for t in text] if isinstance(text, list) else wrap(text.split(), x0)
        for li, ln in enumerate(ls):
            if marker and li == 0:
                ops.append("BT 1 0 0 1 %.2f %.2f Tm (\\227) Tj T* ET" % (LEFT, y))
            gap = hw(" ") if li == len(ls) - 1 or len(ln) < 2 else (RIGHT - x0 - sum(hw(t) for t in ln)) / (len(ln) - 1)
            x = x0
            for w in ln:
                ops.append("BT 1 0 0 1 %.2f %.2f Tm (%s) Tj T* ET" % (x, y, w))
                x += hw(w) + gap
            geom.append((y, " ".join(ln)))
            y -= LEAD
        y -= 14.0
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    fx = doc.get_new_xref()
    doc.update_object(fx, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Name /F1 /Encoding /WinAnsiEncoding >>")
    cx = doc.get_new_xref()
    doc.update_object(cx, "<< >>")
    doc.update_stream(cx, "\n".join(ops).encode("latin-1"))
    doc.xref_set_key(page.xref, "Resources", "<< /Font << /F1 %d 0 R >> >>" % fx)
    doc.xref_set_key(page.xref, "Contents", "%d 0 R" % cx)
    out = doc.tobytes()
    doc.close()
    return out, geom


BODY = ("The undersigned Marlowe Fenwick Trading Company confirms that every consignment listed "
        "below was inspected on arrival, that no damage was found, and that the quantities "
        "received match the delivery notes issued by the carrier on the dates shown.")
ITEM1 = ("Being free of any commitment and of any employment contract with whichever employer "
         "whatsoever so that the engagement can start immediately;")
ITEM2 = ("Having held no salaried position in the past, this recruitment at Northgate Holdings "
         "being my first professional experience of any kind.")
# Breaks no greedy rule explains (a hand-set line, justified all the same): the re-wrap cannot
# model it, so a word edit there is the in-place engine's alone.
HAND = ["Payment shall be made within thirty days of the invoice date unless the buyer",
        "disputes the amount in writing before that date, in which case the undisputed part",
        "remains due and the balance is settled once the dispute has been resolved."]
rl, rl_geom = reportlab_like([(None, BODY), ("—", ITEM1), ("—", ITEM2), (None, HAND)])


def words_on(pdf, y_pdf, page_h=842.0):
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    return [w for w in pg.get_text("words") if abs(w[3] - 0 - (page_h - y_pdf)) < 4]


def line_ink(pdf, text_start):
    """(sizes, right end of the ink) of the line whose text starts with text_start."""
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    for b in pg.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(c["c"] for sp in ln["spans"] for c in sp["chars"])
            if t.strip().lstrip("—").strip().startswith(text_start):
                cs = [c for sp in ln["spans"] for c in sp["chars"] if c["c"].strip()]
                return {round(sp["size"], 2) for sp in ln["spans"]}, max(c["bbox"][2] for c in cs)
    return None, None


check("(fixture) the unedited ReportLab-style page has no stacked glyphs", not stacked_glyphs(rl))
sizes0, end0 = line_ink(rl, "The undersigned")
check("(fixture) its first justified line ends on the margin", end0 is not None and abs(end0 - RIGHT) < 0.5,
      str(end0))

# a word in a justified line, replaced by a longer one
status, out, rep = edit_through_server(rl, "confirms", "acknowledges")
check("justified word: edit succeeds in place",
      status == 200 and rep.get("in_place", {}).get("count") == 1, str(rep.get("in_place")))
check("justified word: nothing was shrunk or redrawn",
      not rep.get("resized_to_fit") and not rep.get("warnings"), str(rep.get("warnings")))
sizes1, end1 = line_ink(out, "The undersigned")
check("justified word: the line keeps the document's size", sizes1 == sizes0, "%s -> %s" % (sizes0, sizes1))
check("justified word: the line still ends on the margin", end1 is not None and abs(end1 - RIGHT) < 0.6,
      str(end1))
check("justified word: new text is on the page", "acknowledges" in fitz.open(stream=out, filetype="pdf")[0].get_text())
check("justified word: no letter prints on top of another", not stacked_glyphs(out), str(stacked_glyphs(out)))

# a hand-broken justified line: no re-wrap can model it, so only the in-place engine can keep it
# on the margin — by respacing the line's own words, not by shrinking the font or leaving a hole
def line_words(pdf, first_word):
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    ws = pg.get_text("words")
    head = next(w for w in ws if w[4] == first_word)
    return [w for w in ws if abs(w[3] - head[3]) < 2.0]


for old_w, new_w, first in (("thirty", "forty-five", "Payment"), ("amount", "sum", "disputes")):
    status, out, rep = edit_through_server(rl, old_w, new_w)
    tag = "hand-set line, %s -> %s" % (old_w, new_w)
    check(tag + ": edit succeeds in place, nothing shrunk or redrawn",
          status == 200 and rep.get("in_place", {}).get("count") == 1 and not rep.get("resized_to_fit")
          and not rep.get("warnings"), str(rep.get("warnings")) + str(rep.get("in_place")))
    ws = line_words(out, first)
    sizes_h = {round(sp_["size"], 2) for b in fitz.open(stream=out, filetype="pdf")[0].get_text("dict")["blocks"]
               for ln in b.get("lines", []) for sp_ in ln["spans"]}
    check(tag + ": every line keeps the document's size", sizes_h == {HSIZE}, str(sizes_h))
    check(tag + ": the line still ends on the margin", abs(max(w[2] for w in ws) - RIGHT) < 0.6,
          str(max(w[2] for w in ws)))
    gaps = [b_[0] - a_[2] for a_, b_ in zip(ws, ws[1:])]
    check(tag + ": its gaps are still uniform (%.2f..%.2f)" % (min(gaps), max(gaps)),
          max(gaps) - min(gaps) < 0.6 and min(gaps) > 1.5)
    check(tag + ": no letter prints on top of another", not stacked_glyphs(out), str(stacked_glyphs(out)))
    before_pos = {(w[4], round(w[1])) for w in fitz.open(stream=rl, filetype="pdf")[0].get_text("words")
                  if abs(w[3] - ws[0][3]) > 2.0}
    after_pos = {(w[4], round(w[1])) for w in fitz.open(stream=out, filetype="pdf")[0].get_text("words")
                 if abs(w[3] - ws[0][3]) > 2.0}
    check(tag + ": no other line changed", before_pos == after_pos, str(sorted(before_pos ^ after_pos)[:4]))

# a bullet item that gains a word: the marker stays, the text re-wraps beside it
sp = client.post("/extract", files={"file": ("t.pdf", rl)}).json()["spans"]
idx = next(i for i, s_ in enumerate(sp) if s_["text"].strip() == "Being")
r = client.post("/edit", files={"file": ("t.pdf", rl)},
                data={"edits": json.dumps([{"index": idx, "new_text": "Mrs Sofia Amrani"}])})
rep = json.loads(base64.b64decode(r.headers["X-Redraft-Font-Report"]))
check("bullet item: edit succeeds with nothing refused or shrunk",
      r.status_code == 200 and not rep.get("resized_to_fit") and not rep.get("warnings")
      and not rep.get("in_place", {}).get("refusals"), str(rep.get("warnings")) + str(rep.get("in_place")))
pg = fitz.open(stream=r.content, filetype="pdf")[0]
txt = " ".join(pg.get_text().split())
check("bullet item: reads as one item with the new words in order",
      "Mrs Sofia Amrani free of any commitment" in txt, txt[txt.find("Mrs") - 5:txt.find("Mrs") + 60])
sizes_b, _ = line_ink(r.content, "Mrs Sofia Amrani")
check("bullet item: the text keeps the document's size", sizes_b == {HSIZE}, str(sizes_b))
marks = [w for w in pg.get_text("words") if w[4] == "—"]
check("bullet item: both markers are still where they were (x=%.1f)" % LEFT,
      len(marks) == 2 and all(abs(w[0] - LEFT) < 0.6 for w in marks), str(marks))
first = next(w for w in pg.get_text("words") if w[4] == "Mrs")
check("bullet item: the text starts at the hanging indent", abs(first[0] - HANG) < 0.6, str(first[0]))
body_x = [w[0] for w in pg.get_text("words") if abs(w[1] - first[1]) > 4 and w[1] > first[1] and w[1] < first[1] + 20]
check("bullet item: its continuation line starts at the hanging indent too",
      body_x and abs(min(body_x) - HANG) < 0.6, str(body_x[:3]))
check("bullet item: no letter prints on top of another", not stacked_glyphs(r.content), str(stacked_glyphs(r.content)))
untouched = [w for w in pg.get_text("words") if "Northgate" in w[4]]
orig_pos = [w for w in fitz.open(stream=rl, filetype="pdf")[0].get_text("words") if "Northgate" in w[4]]
check("bullet item: the next item did not move", untouched and orig_pos and abs(untouched[0][1] - orig_pos[0][1]) < 0.6)

# 7. a catalogue lookup that fails for a moment is retried; one that says "not there" is remembered
import io as _io
import urllib.error as _uerr
import urllib.request as _ureq
import font_extend as fe

class _Resp:
    def __init__(self, body): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False

_calls = []
_mode = {"v": "down"}

def _fake_urlopen(req, timeout=0):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    _calls.append(url)
    if _mode["v"] == "down":
        raise _uerr.URLError("network down")
    if _mode["v"] == "absent":
        raise _uerr.HTTPError(url, 404, "Not Found", {}, None)
    if "api.github.com" in url:
        return _Resp(json.dumps([{"name": "Zzquuxfont-Regular.ttf", "type": "file"}]).encode())
    return _Resp(b"FAKE-DONOR-BYTES")

_real = (_ureq.urlopen, fe._save_listing_cache, dict(fe._listing_cache))
_ureq.urlopen = _fake_urlopen
fe._save_listing_cache = lambda: None
try:
    key = ("zzquuxfont", 400, "normal")
    fe._listing_cache.clear(); fe._donor_cache.pop(key, None); fe._donor_retry_at.pop(key, None)
    got = fe.resolve_donor_detailed("Zzquuxfont")
    check("donor lookup: unreachable catalogue gives no donor", got == (None, None), str(got))
    check("donor lookup: ...but that is not remembered as 'no such font'", key not in fe._donor_cache)
    n = len(_calls)
    fe.resolve_donor_detailed("Zzquuxfont")
    check("donor lookup: an immediate retry does not hammer the network", len(_calls) == n, str(len(_calls) - n))
    _mode["v"] = "up"; fe._donor_retry_at.clear()
    got = fe.resolve_donor_detailed("Zzquuxfont")
    check("donor lookup: once the catalogue answers, the same font resolves", got[0] == b"FAKE-DONOR-BYTES", str(got))

    key2 = ("zzabsentfont", 400, "normal")
    fe._listing_cache.clear(); fe._donor_cache.pop(key2, None)
    _mode["v"] = "absent"
    got = fe.resolve_donor_detailed("Zzabsentfont")
    n = len(_calls)
    got2 = fe.resolve_donor_detailed("Zzabsentfont")
    check("donor lookup: a family the catalogue really lacks is remembered as absent",
          got == (None, None) and key2 in fe._donor_cache and len(_calls) == n, str(got) + " " + str(len(_calls) - n))
finally:
    _ureq.urlopen, fe._save_listing_cache = _real[0], _real[1]
    fe._listing_cache.clear(); fe._listing_cache.update(_real[2])
    for k in (("zzquuxfont", 400, "normal"), ("zzabsentfont", 400, "normal")):
        fe._donor_cache.pop(k, None); fe._donor_retry_at.pop(k, None)


# 8. a ligature glyph is neither a plain 'f' nor a source of id -1
ANNEX = open(os.path.join(HERE, "..", "examples", "annex-cell.pdf"), "rb").read()
import inplace_spike as _sp
_gm = _sp._gid_maps(fitz.open(stream=ANNEX, filetype="pdf"))
_serif = _gm.get("DMSerifDisplay-Regular", {})
check("ligature: no letter is mapped to the id -1", all(g >= 0 for fm in _gm.values() for g in fm.values()))
check("ligature: a plain 'i' keeps its own id", _serif.get("i", -1) > 0, str(_serif.get("i")))
check("ligature: the ligature's id is not filed under its first letter", "f" not in _serif, str(_serif.get("f")))

_spans = api.extract_spans(ANNEX)
def _annex_span(t): return next(x for x in _spans if x["text"].strip() == t)

def _ink_runs(pdf, needle):
    out = []
    for sp in fitz.open(stream=pdf, filetype="pdf")[0].get_texttrace():
        if needle in "".join(chr(c[0]) for c in sp["chars"] if c[0] > 0):
            out.append([(chr(c[0]), c[1]) for c in sp["chars"]])
    return out

for old, new in (("Hosting and maintenance", "Manager and maintenance"),
                 ("Northfield Studio", "Sofia Amrani Studio")):
    out, rep = api.apply_replacements(ANNEX, [(_annex_span(old), new)], try_inplace=True)
    ref = [str(r) for r in rep["in_place"].get("refusals", [])]
    if rep["in_place"]["count"] == 0 and any("no_donor" in r or "missing_glyph" in r for r in ref):
        print("SKIP - ligature edit " + repr(new) + " (no donor reachable)")
        continue
    check("ligature: " + repr(old) + " -> " + repr(new) + " stays in place",
          rep["in_place"]["count"] == 1 and not rep.get("warnings"), str(rep["in_place"]) + str(rep.get("warnings")))
    check("ligature: " + repr(new) + " reads back",
          new in " ".join(fitz.open(stream=out, filetype="pdf")[0].get_text().split()))
    check("ligature: no font was added",
          len(fitz.open(stream=out, filetype="pdf")[0].get_fonts(full=True))
          == len(fitz.open(stream=ANNEX, filetype="pdf")[0].get_fonts(full=True)))

_out, _rep = api.apply_replacements(ANNEX, [(_annex_span("Northfield Studio"), "Sofia Amrani Studio")], try_inplace=True)
_run = _ink_runs(_out, "Sofia")
if _run:
    r0 = _run[0]
    fi = next((k for k, (ch, g) in enumerate(r0) if ch == "f"), None)
    check("ligature: 'fi' in the new text is the font's ligature glyph, as in the original",
          fi is not None and r0[fi][1] == 122 and r0[fi + 1] == ("i", -1), str(r0[:8]))
    check("ligature: no letter is drawn twice", "".join(c for c, g in r0) == "Sofia Amrani Studio", str(r0))


# 9. the donor taken from the machine's fonts has the weight that was asked for
import tempfile
import types
from fontTools.ttLib import TTFont as _TT

_tmp = tempfile.mkdtemp()
def _face(name, weight):
    f = _TT("/usr/share/fonts/TTF/DejaVuSerif.ttf") if os.path.exists("/usr/share/fonts/TTF/DejaVuSerif.ttf") else None
    if f is None:
        return None
    f["OS/2"].usWeightClass = weight
    path = os.path.join(_tmp, name)
    f.save(path)
    return path

_light, _reg, _hair = _face("Zzfont-Light.ttf", 300), _face("Zzfont-Regular.ttf", 400), _face("Zzfont-Hairline.ttf", 250)
if not _light:
    print("SKIP - no DejaVu Serif to build weight fixtures from")
else:
    def _fc_offers(paths):
        # fontconfig answers a ":style=Regular" query with every file that lists Regular as a style
        return lambda *a, **k: types.SimpleNamespace(stdout="\n".join(paths) + "\n", returncode=0)
    _real_run = _pe.subprocess.run
    try:
        _pe.subprocess.run = _fc_offers([_hair, _light, _reg])
        got = _pe._find_system_font("Zzfont", 400, "normal")
        check("system donor: Regular is asked for, the Regular file is taken over a shorter-named Light one",
              got is not None and _TT(__import__("io").BytesIO(got))["OS/2"].usWeightClass == 400)
        _pe.subprocess.run = _fc_offers([_hair, _light])
        got = _pe._find_system_font("Zzfont", 400, "normal")
        check("system donor: with only Light and Hairline installed, Regular finds nothing (so the catalogue is asked)",
              got is None, str(None if got is None else _TT(__import__("io").BytesIO(got))["OS/2"].usWeightClass))
        got = _pe._find_system_font("Zzfont", 300, "normal")
        check("system donor: a Light request still takes the Light file",
              got is not None and _TT(__import__("io").BytesIO(got))["OS/2"].usWeightClass == 300)
    finally:
        _pe.subprocess.run = _real_run


# 10. whitespace at the end of a field is not part of the edit
import api as _api
check("edges: a trailing space the field did not have is dropped", _api._keep_edges("Compute", "Compute ") == "Compute")
check("edges: the trailing space the field had is kept", _api._keep_edges("Compute ", "Compute") == "Compute ")
check("edges: a different trailing run becomes the field's own", _api._keep_edges("A ", "B   ") == "B ")
check("edges: leading space is still the field's own when the edit has none", _api._keep_edges(" A", "B") == " B")
check("edges: a whitespace-only edit is left alone", _api._keep_edges("A", "  ") == "  ")

LATEX = open(os.path.join(HERE, "..", "examples", "latex-cm.pdf"), "rb").read()
_lspans = api.extract_spans(LATEX)
_lt = next((s for s in _lspans if s["text"].strip() == "Compute the cabs"), None)
if _lt is None:
    print("SKIP - latex-cm.pdf has no 'Compute the cabs'")
else:
    for new, changes in (("Compute the cabs ", False), ("Compute the cabs  ", False), ("Compute ", True)):
        out, rep = api.apply_replacements(LATEX, [(_lt, new)], try_inplace=True)
        txt = " ".join(fitz.open(stream=out, filetype="pdf")[0].get_text().split())
        check("latex: " + repr(new) + (" is applied in place" if changes else " leaves the page as it was") + ", with no refusal",
              not rep.get("warnings") and not rep["in_place"].get("refusals")
              and (rep["in_place"]["count"] == 1 if changes else out == LATEX),
              str(rep["in_place"]) + str(rep.get("warnings")))
        check("latex: " + repr(new) + " reads back as " + repr(new.strip()), new.strip() in txt, txt[:120])


print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
