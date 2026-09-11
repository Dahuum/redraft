"""
test_invariants.py — property-based stress of the in-place engine across
structurally different real documents.

WHY THIS EXISTS
---------------
Every other suite here checks specific known cases. This one drives MANY spans
through MANY kinds of replacement and asserts only the properties that must
hold whatever the document is. It was written because the engine's claims all
rested on one fixture with one font family, and it immediately found three
real bugs that the case-by-case suites did not:

  1. An over-long replacement ran off the page on LaTeX documents. The
     overflow guard could not read Type1 width tables (it demanded
     /Subtype/TrueType, which glyph injection needs but width lookup does
     not), fell back to extraction — and extraction TRUNCATES a run that
     leaves the media box, so the text came back looking like it fit.
  2. Editing one of two identical strings on a page rewrote the other one.
     The caller named the occurrence it meant by bbox, but the splice always
     took the first match in content-stream order.
  3. The fix for (2) was itself fooled whenever the replacement was a
     substring of the original: checking that the target span "contains the
     new text" is satisfied by a span that never changed, so replacing
     'models' with 'mod' kept the wrong occurrence.
  4. Encoding a character from the font's /ToUnicode drew NOTHING for it.
     /ToUnicode is a reverse map for extraction and can name a code the glyph
     program has no outline for, so the page extracted as 'Fécture' with zero
     ink where the 'é' should be. Only I6 catches that class: every other
     invariant here, and every check in the other suites, reads text back
     through extraction — which reports exactly what the edit intended
     regardless of whether a rasterizer can draw it.

INVARIANTS
----------
  I1  never draws text past the page edge
  I2  an accepted in-place edit adds no font resource and no form XObject
  I3  the replacement reads back as the text that was asked for
  I4  no line other than the edited one moves
  I5  a refusal always names a reason
  I6  every visible character of the replacement actually has INK
  I7  an accepted edit never drives a gap on the edited line negative
  I8  a decoration stays registered with the text it decorates
  I9  no text that was on the page before the edit disappears from it

  7. Reflow pushed two neighbouring runs off the right edge of the paper, to
     x=618 and x=783 on a 595.92pt page. I1 could not see it: glyphs outside
     the media box are not extracted, so the runs simply vanished from the
     text and the rightmost x it measures went DOWN. I9 catches the class —
     an edit may move text and may replace text, but text never just
     disappears.

  6. Reflow moved the text and left its underline behind. A link underline is
     a filled rectangle, not text, so nothing carried it along: on a
     Wikipedia page a footnote marker moved 30.4pt right and its rule stayed,
     ruling straight through the value that took its place. I1-I7 are all
     blind to it — every one of them looks only at text — and only a
     rendering showed it. I8 pins text to its own decoration. It must stay
     narrow: the same line crosses a column rule 655pt tall and a row
     background 16.9pt tall, and neither of those may move.

  5. A longer replacement drew straight over the text beside it. Nothing
     moves it: every run is positioned by its own operator, and PDF's Td is
     measured from the previous line's matrix rather than from the pen, so
     glyph advances never push the next run along. Reflow has to move it
     explicitly, and where reflow cannot — a run that also draws on another
     line, or a page whose content stream is under a transform, as headless
     Chrome's is — the edit has to be REFUSED instead. Measured before that
     was true, 7 accepted edits across these four documents drew over their
     neighbours, the worst by 209pt. I7 is the invariant that catches it, and
     it has to be measured in VISUAL space: neither document order nor span
     identity survives an edit, because the local fixture draws every label
     before every value and extraction merges two spans as soon as they
     become contiguous.

The local fixture runs always; the three downloaded documents are skipped
cleanly without network, matching test_real_world_pdfs.py's convention.
"""
import base64
import os
import random
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
import inplace_spike as sp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "..", "examples", "attestation-demo.pdf")
FAIL = []
VIOLATIONS = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "redraft-invariants/1"})
        return urllib.request.urlopen(req, timeout=45).read()
    except Exception:  # noqa: BLE001
        return None


def snapshot(pdf, page_no):
    """{baseline: [(x0, x1, text)]} plus the page width."""
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        lines = {}
        for b in d[page_no].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    if s["text"].strip():
                        lines.setdefault(round(s["origin"][1], 1), []).append(
                            (round(s["bbox"][0], 2), round(s["bbox"][2], 2), s["text"]))
        return lines, d[page_no].rect.width
    finally:
        d.close()


def _decorations(pdf, target_bbox):
    """{(y0, y1, width): distance from the run it decorates} for every thin
    rule on the target's line.

    Only thin rules inside the line's own vertical band count as decoration.
    A taller rectangle is a cell background or a table border and is supposed
    to stay exactly where it is.
    """
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        # Half the shorter box's height, the same measured threshold the
        # engine uses. A bare overlap test pulled in a run from the NEXT line
        # (21% of its height), which stretched the band far enough to include
        # that line's underline — and then reported the engine for leaving
        # another line's decoration alone, which is exactly what it should do.
        spans = []
        for b in d[0].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for sp_ in l.get("spans", []):
                    if not sp_["text"].strip():
                        continue
                    ov = (min(sp_["bbox"][3], target_bbox[3])
                          - max(sp_["bbox"][1], target_bbox[1]))
                    ref = min(sp_["bbox"][3] - sp_["bbox"][1],
                              target_bbox[3] - target_bbox[1])
                    if ref > 0 and ov >= 0.5 * ref:
                        spans.append(sp_)
        if not spans:
            return {}
        top = min(sp_["bbox"][1] for sp_ in spans)
        bot = max(sp_["bbox"][3] for sp_ in spans)
        tall = max(2.0, 0.12 * (bot - top))
        out = {}
        for dr in d[0].get_drawings():
            r = dr["rect"]
            if not 0 < r.y1 - r.y0 <= tall:
                continue
            # A decoration sits under ONE run of the line, between that run's
            # baseline and the bottom of its box — not merely somewhere in the
            # line's overall vertical band. The band is stretched by any
            # superscript far enough to take in the previous line's
            # underlines, and those then get judged as this line's.
            if not any(sp_["bbox"][0] - 1.0 <= r.x0 and r.x1 <= sp_["bbox"][2] + 1.0
                       and r.y0 >= sp_["origin"][1] - 0.5
                       and r.y1 <= sp_["bbox"][3] + 1.5
                       for sp_ in spans):
                continue
            # Anchor the rule to the run that HORIZONTALLY COVERS it, not to
            # the nearest run start on its left. A decoration lies under its
            # own text, and covering is stable across the edit; "nearest to
            # the left" is not — a run that moved left past a stationary rule
            # becomes its new nearest neighbour, and the rule gets reported
            # for moving when it never did. Measured on this page, every real
            # decoration of a line is covered by a run of that line and every
            # rule belonging to the line above is covered by none.
            cover = [sp_["bbox"][0] for sp_ in spans
                     if sp_["bbox"][0] - 1.0 <= r.x0 and r.x1 <= sp_["bbox"][2] + 1.0
                     and r.y0 >= sp_["origin"][1] - 0.5
                     and r.y1 <= sp_["bbox"][3] + 1.5]
            if not cover:
                continue
            out[(round(r.y0, 2), round(r.y1, 2), round(r.x1 - r.x0, 2))] = \
                round(r.x0 - max(cover), 2)
        return out
    finally:
        d.close()


def _overhang(pdf):
    """(worst overhang, the run it belongs to) for any thin rule that reaches
    past the right edge of the text sitting directly above it, or None.

    This is the property a well-formed document has and a botched edit breaks:
    an underline ends where its word ends. It catches what registration
    cannot — a rule whose text was shortened out from under it — and needed no
    threshold guessing, because the untouched documents establish the floor.
    """
    d = fitz.open(stream=pdf, filetype="pdf")
    try:
        runs = [x for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", [])
                for x in l.get("spans", []) if x["text"].strip()]
        worst = None
        for dr in d[0].get_drawings():
            r = dr["rect"]
            if not 0 < r.y1 - r.y0 <= 2.0:
                continue
            above = [x for x in runs
                     if x["bbox"][0] - 1 <= r.x1 and r.x0 <= x["bbox"][2] + 1
                     and r.y0 >= x["origin"][1] - 0.5 and r.y1 <= x["bbox"][3] + 2.5]
            if not above:
                continue
            over = r.x1 - max(x["bbox"][2] for x in above)
            if worst is None or over > worst[0]:
                worst = (over, max(above, key=lambda x: x["bbox"][2])["text"][:20])
        return worst
    finally:
        d.close()


def stress(doc_name, data, n_spans=8, seed=7):
    """Drive spans through replacement shapes; append any invariant breach."""
    rng = random.Random(seed)
    d = fitz.open(stream=data, filetype="pdf")
    spans = [s for s in sp._spans(d[0])
             if len(s["text"].strip()) >= 6 and s["size"] > 5]
    d.close()
    if not spans:
        return 0
    rng.shuffle(spans)
    spans = spans[:n_spans]
    base_lines, page_w = snapshot(data, 0)
    attempts = 0
    ink_budget = [10]

    for s in spans:
        old = s["text"].strip()
        for label, new in (("reversed", old[::-1]),
                           ("longer", old + " Wxqz"),
                           ("much-longer", old + " " + ("Wxqzkj " * 6)),
                           ("shorter", old[: max(2, len(old) // 3)]),
                           ("digits", "".join(str((i * 7) % 10) for i in range(len(old))))):
            if not new.strip() or new == old:
                continue
            attempts += 1
            try:
                r = sp.edit(data, old, new, page=0, bbox=s["bbox"], verify=False)
            except Exception as exc:  # noqa: BLE001
                VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: CRASH "
                                  f"{type(exc).__name__}: {exc}")
                continue

            if not r.get("ok"):
                if not r.get("reason"):                                   # I5
                    VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: "
                                      f"refused with no reason")
                continue

            out = base64.b64decode(r["pdf_b64"])
            try:
                new_lines, pw = snapshot(out, 0)
            except Exception as exc:  # noqa: BLE001
                VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: "
                                  f"unreadable output: {exc}")
                continue

            worst = max((x1 for items in new_lines.values() for _, x1, _ in items),
                        default=0.0)
            if worst > pw + 0.5:                                          # I1
                VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: I1 text at "
                                  f"x={worst:.1f} past page {pw:.1f}")

            o = fitz.open(stream=data, filetype="pdf")
            n = fitz.open(stream=out, filetype="pdf")
            try:                                                          # I2
                if len(n[0].get_fonts(full=True)) > len(o[0].get_fonts(full=True)):
                    VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: I2 font added")
                if len(n[0].get_xobjects()) > len(o[0].get_xobjects()):
                    VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: I2 XObject added")
            finally:
                o.close()
                n.close()

            joined = " ".join(t for items in new_lines.values() for _, _, t in items)
            probe = new.strip()[:18]
            if probe and sp._norm(probe) not in sp._norm(joined):          # I3
                VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: I3 replacement "
                                  f"not found: {probe!r}")

            # I6 — ink where the replacement is. Deliberately measured off a
            # rasterization rather than read back through extraction, because
            # extraction reports what the edit INTENDED: a glyph with no
            # outline still extracts as its character.
            #
            # The span is found by POSITION, not by matching its text. A first
            # attempt matched on the replacement's opening characters and
            # picked up an unrelated paragraph — shortening 'propose' to 'pro'
            # matched the arXiv licence footer's "Provided proper attribution"
            # and reported that as inkless. Capped per document to keep the
            # suite quick.
            if ink_budget[0] > 0:
                ink_budget[0] -= 1
                dd = fitz.open(stream=out, filetype="pdf")
                try:
                    tgt = fitz.Rect(s["bbox"])
                    for b in dd[0].get_text("rawdict")["blocks"]:
                        for l in b.get("lines", []):
                            for hsp in l.get("spans", []):
                                if abs(hsp["origin"][1] - s["origin"][1]) > 1.0:
                                    continue
                                if abs(hsp["bbox"][0] - tgt.x0) > 2.0:
                                    continue
                                blank = []
                                for c in hsp["chars"]:
                                    if not c["c"].strip():
                                        continue
                                    cb = fitz.Rect(c["bbox"])
                                    if cb.is_empty or cb.width < 0.5 or cb.height < 0.5:
                                        continue
                                    if not fitz.Rect(dd[0].rect).contains(cb):
                                        continue    # off-page: nothing to draw
                                    pm = dd[0].get_pixmap(matrix=fitz.Matrix(8, 8), clip=cb)
                                    # Ink is any pixel that is not near-white
                                    # in EVERY channel. Sampling only the red
                                    # channel called pure-red text blank: the
                                    # arXiv licence footer is 0xff0000, so it
                                    # reads 255 there and the original page
                                    # "failed" its own invariant.
                                    buf, ch = pm.samples, pm.n
                                    dark = 0
                                    for i in range(0, len(buf), ch):
                                        if min(buf[i:i + min(3, ch)]) < 200:
                                            dark += 1
                                            break
                                    if dark == 0:
                                        blank.append(c["c"])
                                if blank:
                                    VIOLATIONS.append(
                                        f"[{doc_name}] {label} {old[:20]!r}: I6 "
                                        f"characters drew NO ink: {blank!r}")
                                break
                finally:
                    dd.close()

            # I7 — gaps on the edited line. Measured in VISUAL space (spans
            # sorted by x), because neither document order nor span identity
            # survives an edit: this fixture draws every label before every
            # value, and extraction MERGES two spans as soon as they become
            # contiguous. Only a gap that this edit drove negative counts; a
            # gap that merely got smaller was free space the longer value was
            # entitled to use.
            def _gaps(lines_by_y, y):
                items = sorted((a, b) for a, b, _ in lines_by_y.get(y, []))
                return [round(items[i + 1][0] - items[i][1], 2)
                        for i in range(len(items) - 1)]

            _ey = round(s["origin"][1], 1)
            _gb, _ga = _gaps(base_lines, _ey), _gaps(new_lines, _ey)
            if len(_gb) == len(_ga):
                for _before, _after in zip(_gb, _ga):
                    if _after < -0.5 and _after < _before - 0.5:
                        VIOLATIONS.append(
                            f"[{doc_name}] {label} {old[:20]!r}: I7 a gap on the "
                            f"edited line went from {_before:.2f}pt to "
                            f"{_after:.2f}pt — the edit drew over its neighbour")
                        break

            # I8 — decorations. A rule keeps its y and its width across an
            # edit, so (y0, y1, width) identifies the same rule afterwards,
            # and what must not change is its offset from the run it sits
            # under.
            _r_before = _decorations(data, s["bbox"])
            _r_after = _decorations(out, s["bbox"])
            # ...and no rule may end up ruling empty space. Calibrated on
            # the untouched documents: across 129 thin rules the worst
            # overhang past the text above one is 0.37pt, so anything past
            # 1.5pt is something this edit did.
            _over = _overhang(out)
            if _over and _over[0] > 1.5:
                VIOLATIONS.append(
                    f"[{doc_name}] {label} {old[:20]!r}: I8 a rule now extends "
                    f"{_over[0]:.2f}pt past the text above it ({_over[1]!r})")
            for _key, _off in _r_before.items():
                if _key not in _r_after:
                    continue
                if abs(_r_after[_key] - _off) > 0.5:
                    VIOLATIONS.append(
                        f"[{doc_name}] {label} {old[:20]!r}: I8 a rule at "
                        f"y={_key[0]} sat {_off:.2f}pt from the run it "
                        f"decorates and now sits {_r_after[_key]:.2f}pt away "
                        f"— text and decoration are out of register")
                    break

            # I9 — nothing vanishes. Checked before I4 because a run pushed
            # off the paper looks to every other check like a line that
            # merely changed.
            _before_texts = [t for items in base_lines.values() for _, _, t in items
                             if len(t.strip()) >= 3]
            _after_all = sp._norm(" ".join(
                t for items in new_lines.values() for _, _, t in items))
            for _t in _before_texts:
                if _t.strip() == old.strip():
                    continue                  # the field itself was replaced
                if sp._norm(_t) and sp._norm(_t) not in _after_all:
                    VIOLATIONS.append(
                        f"[{doc_name}] {label} {old[:20]!r}: I9 {_t.strip()[:24]!r} "
                        f"was on the page and is not any more")
                    break

            # I4 — no UNRELATED line moves. A superscript has a raised
            # baseline of its own and belongs to the edited line, so the
            # baselines of that whole line are excluded, judged the same way
            # the engine judges it: a box overlapping the field's by at
            # least half its height is part of the line.
            _tb = s["bbox"]
            _own = set()
            _dd = fitz.open(stream=data, filetype="pdf")
            for _b in _dd[0].get_text("dict")["blocks"]:
                for _l in _b.get("lines", []):
                    for _sp in _l.get("spans", []):
                        if not _sp["text"].strip():
                            continue
                        _ov = min(_sp["bbox"][3], _tb[3]) - max(_sp["bbox"][1], _tb[1])
                        _ref = min(_sp["bbox"][3] - _sp["bbox"][1], _tb[3] - _tb[1])
                        if _ref > 0 and _ov >= 0.5 * _ref:
                            _own.add(round(_sp["origin"][1], 1))
            _dd.close()

            edited_line = round(s["origin"][1], 1)
            for ln, items in base_lines.items():                           # I4
                if abs(ln - edited_line) < 0.6 or ln in _own:
                    continue
                if new_lines.get(ln) != items:
                    VIOLATIONS.append(f"[{doc_name}] {label} {old[:20]!r}: I4 line "
                                      f"y={ln} changed but wasn't the target")
                    break
    return attempts


print("=== local fixture (always) ===")
with open(FIXTURE, "rb") as fh:
    n_local = stress("attestation", fh.read())
check("local fixture produced edit attempts", n_local > 0, f"{n_local}")

print()
print("=== downloaded documents (skipped without network) ===")
CORPUS = [
    ("arXiv/pdfTeX", "https://arxiv.org/pdf/1706.03762"),
    ("Chrome print", "https://en.wikipedia.org/api/rest_v1/page/pdf/Python_(programming_language)"),
    ("Acrobat form", "https://www.irs.gov/pub/irs-pdf/fw9.pdf"),
]
n_remote = 0
for name, url in CORPUS:
    data = fetch(url)
    if not data:
        print(f"SKIP - {name} (no network)")
        continue
    got = stress(name, data)
    n_remote += got
    print(f"  {name}: {got} attempts")

print()
check("no invariant was violated on any document",
      not VIOLATIONS,
      "\n        " + "\n        ".join(VIOLATIONS[:12]))
print(f"       {n_local + n_remote} edit attempts checked against I1-I9")

print()
print("=" * 70)
print("RESULT: ALL PASS" if not FAIL else f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
