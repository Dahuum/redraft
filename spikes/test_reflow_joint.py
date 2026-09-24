"""test_reflow_joint.py — paragraphs set to a margin nothing on the page shows,
and phrases that wrap across two lines.

  * A WRAPPED PHRASE. "…issued by Atlas Consulting / SARL for…" sits in no
    single line; a user changes it by editing both. Each line alone fitted
    in place, and neither alone could re-wrap (the other edit sits lower), so
    the shortened first line stayed short where the producer pulls "for" up.
    Both edits are now re-wrapped together.
  * A FRAME MARGIN. ReportLab sets a paragraph in a Frame whose edge is
    neither the page's nor any line's end. The original's breaks pin it to an
    interval (each line fits; each break was forced); the re-wrap is taken
    only when it comes out the same at both ends of that interval.
  * HARD BREAKS. An address block's lines leave room for the next word, so
    no margin explains them as wrapping: it must never be merged.

Every document is generated here; the twin is the same generator run on the
changed text.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
import api  # noqa: E402
import reflow  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


FONT = fitz.Font("helv")
X0, SIZE, LEAD = 66.0, 11, 15
PARA = ("This statement confirms that Nadia Benali has settled every invoice issued by "
        "Atlas Consulting SARL for the period ending 14/03/2024, and that no further "
        "amount is due on this account under the agreement signed in Rabat.")


def wrap(text, margin):
    lines, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w) if cur else w
        if cur and X0 + FONT.text_length(cand, SIZE) > margin:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    return lines + [cur]


# A frame edge where the phrase wraps after "Atlas Consulting" — like
# ReportLab's 524pt on this text in DejaVu.
MARGIN = next(m / 4 for m in range(4 * 250, 4 * 530)
              if any(l.endswith("Atlas Consulting") for l in wrap(PARA, m / 4)[:-1]))


def ragged(text, lines=None):
    """A ragged paragraph wrapped greedily at MARGIN — or set to *lines*.
    A right-aligned amount ends at 530pt, past the paragraph's margin, as on
    a ReportLab statement: the page's widest line is not the margin."""
    if lines is None:
        lines = wrap(text, MARGIN)
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    pg.insert_text((60, 80), "STATEMENT OF ACCOUNT", fontsize=16, fontname="helv")
    pg.insert_text((60, 150), "Implementation support", fontsize=SIZE, fontname="helv")
    pg.insert_text((530 - FONT.text_length("11,400.00", SIZE), 150), "11,400.00",
                   fontsize=SIZE, fontname="helv")
    for i, l in enumerate(lines):
        pg.insert_text((X0, 200 + LEAD * i), l, fontsize=SIZE, fontname="helv")
    pg.insert_text((X0, 200 + LEAD * (len(lines) + 2)), "Signed in Rabat.", fontsize=SIZE,
                   fontname="helv")
    return d.tobytes(), lines


def words(pdf):
    return [(round(w[0], 1), round(w[3], 1), w[4])
            for w in fitz.open(stream=pdf, filetype="pdf")[0].get_text("words")]


# ── a phrase wrapping across two lines, changed by editing both ──
orig, olines = ragged(PARA)
wrapped = [i for i, l in enumerate(olines) if l.endswith("Atlas Consulting")]
check("fixture: the phrase wraps after 'Atlas Consulting'",
      wrapped and olines[wrapped[0] + 1].startswith("SARL "), str(olines))
if wrapped:
    twin, tlines = ragged(PARA.replace("Atlas Consulting SARL", "Atlas Group"))
    sp = api.extract_spans(orig)
    a = next(s for s in sp if s["text"].rstrip().endswith("Atlas Consulting"))
    b = next(s for s in sp if s["text"].startswith("SARL "))
    out, rep = api.apply_replacements(
        orig, [(a, a["text"].replace("Atlas Consulting", "Atlas Group")), (b, b["text"][5:])],
        try_inplace=True)
    check("wrapped phrase: both edits land in place, re-wrapped together",
          rep["in_place"]["count"] == 2 and rep["in_place"].get("reflowed"), str(rep["in_place"]))
    ow, tw = words(out), words(twin)
    off = [(p, q) for p, q in zip(ow, tw) if p[2] != q[2] or abs(p[0] - q[0]) > 0.5
           or abs(p[1] - q[1]) > 0.5]
    check("wrapped phrase: every word where the producer's re-print puts it",
          len(ow) == len(tw) and not off, str(off[:3]))

# ── a single edit in a frame-margin paragraph: proven, or left in place ──
orig2, lines2 = ragged(PARA)
sp2 = api.extract_spans(orig2)
tgt = next(s for s in sp2 if "Nadia Benali" in s["text"])
new = tgt["text"].replace("Nadia Benali", "Nadia B.")
rr = reflow.reflow(orig2, tgt, new, multiline_only=True)
twin2, _ = ragged(PARA.replace("Nadia Benali", "Nadia B."))
if rr.get("ok"):
    ow, tw = words(rr["pdf"]), words(twin2)
    off = [(p, q) for p, q in zip(ow, tw) if p != q and (p[2] != q[2] or abs(p[0] - q[0]) > 0.5
                                                         or abs(p[1] - q[1]) > 0.5)]
    check("frame margin: the re-wrap matches the producer's re-print",
          len(ow) == len(tw) and not off, str(off[:3]))
else:
    check("frame margin: the only refusal allowed is an ambiguous margin",
          rr.get("reason") == "ambiguous_margin", str(rr.get("reason")))

# ── an address block (hard breaks) is never merged ──
addr, alines = ragged(None, lines=["Atlas Consulting SARL", "12 Rue des Fleurs",
                                   "Casablanca 20000", "Maroc"])
sa = next(s for s in api.extract_spans(addr) if s["text"] == "Atlas Consulting SARL")
out, rep = api.apply_replacements(addr, [(sa, "Atlas SARL")], try_inplace=True)
got = [l for l in fitz.open(stream=out, filetype="pdf")[0].get_text().splitlines() if l.strip()]
at = got.index("Atlas SARL") if "Atlas SARL" in got else 0
check("address block: its lines are kept as they were",
      got[at:at + 4] == ["Atlas SARL", "12 Rue des Fleurs", "Casablanca 20000", "Maroc"], str(got))
rr = reflow.reflow(addr, sa, "Atlas SARL", multiline_only=True)
check("address block: the re-wrap refuses to explain hard breaks as wrapping",
      not rr.get("ok") or not rr.get("breaks_changed"), str({k: v for k, v in rr.items()
                                                                if k != "pdf"}))

# ── underlines and links follow their words ──
# Chrome draws a link's underline as a thin filled rectangle per line and
# its clickable area as one link per line. Editing text BEFORE a linked word
# moved the word and left both behind, under blank paper.
CAP = "The designer of Python, Guido van Rossum, at PyCon US 2024 in Pittsburgh."
GREY = (0.6667, 0.6667, 0.6667)


def captioned(lines):
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    pg.insert_text((60, 150), "Implementation support", fontsize=SIZE, fontname="helv")
    pg.insert_text((530 - FONT.text_length("11,400.00", SIZE), 150), "11,400.00",
                   fontsize=SIZE, fontname="helv")
    for i, l in enumerate(lines):
        y = 200 + LEAD * i
        pg.insert_text((X0, y), l, fontsize=SIZE, fontname="helv")
        for wd in ("Guido", "van Rossum", "PyCon"):
            at = l.find(wd)
            if at >= 0:
                x = X0 + FONT.text_length(l[:at], SIZE)
                r = fitz.Rect(x, y + 1.5, x + FONT.text_length(wd, SIZE), y + 2.2)
                pg.draw_rect(r, color=None, fill=GREY, width=0)
                pg.insert_link({"kind": fitz.LINK_URI, "uri": "https://example.org/" + wd[:3],
                                "from": fitz.Rect(r.x0, y - SIZE, r.x1, y + 2.5)})
    return d.tobytes()


def under_ok(pdf, word):
    """Is *word* underlined, and is a link over it?"""
    pg = fitz.open(stream=pdf, filetype="pdf")[0]
    w = [x for x in pg.get_text("words") if x[4].strip(",.") == word]
    if not w:
        return False, False
    w = fitz.Rect(w[0][:4])
    # the word box may carry trailing punctuation ("Rossum,") the rule stops short of
    rule = any(abs(fitz.Rect(d["rect"]).x0 - w.x0) < 1.0 and w.x0 + 0.6 * w.width
               <= fitz.Rect(d["rect"]).x1 <= w.x1 + 1.2
               and w.y1 - 3 <= fitz.Rect(d["rect"]).y0 <= w.y1 + 2 for d in pg.get_drawings())
    link = any(fitz.Rect(lk["from"]).x0 <= w.x0 + 1 and fitz.Rect(lk["from"]).x1 >= w.x0 + 0.6 * w.width
               and fitz.Rect(lk["from"]).intersects(w) for lk in pg.get_links())
    return rule, link


# in place: the edit before "Guido" on its own line pushes it left
one = captioned(["The designer of Python, Guido"])
t1 = next(s for s in api.extract_spans(one) if s["text"].startswith("The designer"))
out, rep = api.apply_replacements(one, [(t1, t1["text"].replace("designer", "des"))],
                                  try_inplace=True)
check("in place: the pushed word keeps its underline and link",
      rep["in_place"]["count"] == 1 and under_ok(out, "Guido") == (True, True),
      str(under_ok(out, "Guido")))
check("in place: no underline left behind",
      not reflow.stranded_underlines(one, out, 0, None), str(reflow.stranded_underlines(one, out, 0, None)))

# re-wrap: "van" moves up a line, splitting "van Rossum"'s underline and link
cm = next(m / 4 for m in range(4 * 150, 4 * 530)
          if [l for l in wrap(CAP, m / 4)][0].endswith("Guido")
          and X0 + FONT.text_length(wrap(CAP, m / 4)[0].replace("designer", "des") + " van", SIZE)
          <= m / 4)
cl = wrap(CAP, cm)
cap = captioned(cl)
t2 = next(s for s in api.extract_spans(cap) if s["text"].startswith("The designer"))
out, rep = api.apply_replacements(cap, [(t2, t2["text"].replace("designer", "des"))],
                                  try_inplace=True)
got = [l for l in fitz.open(stream=out, filetype="pdf")[0].get_text().splitlines() if l.strip()]
check("re-wrap: 'van' comes up beside 'Guido', as the producer sets it",
      rep["in_place"].get("reflowed") and any(l.rstrip().endswith("Guido van") for l in got), str(got))
check("re-wrap: every underlined word is still underlined and linked",
      all(under_ok(out, w) == (True, True) for w in ("Guido", "van", "Rossum", "PyCon")),
      str({w: under_ok(out, w) for w in ("Guido", "van", "Rossum", "PyCon")}))
check("re-wrap: no underline left behind",
      not reflow.stranded_underlines(cap, out, 0, None), str(reflow.stranded_underlines(cap, out, 0, None)))
check("re-wrap: no link lost — 'van Rossum' now spans two lines, one link each",
      len(fitz.open(stream=out, filetype="pdf")[0].get_links())
      == len(fitz.open(stream=cap, filetype="pdf")[0].get_links()) + 1)
check("detector control: an unedited page has nothing left behind",
      not reflow.stranded_underlines(cap, cap, 0, None))

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
