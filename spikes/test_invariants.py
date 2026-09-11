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

            edited_line = round(s["origin"][1], 1)
            for ln, items in base_lines.items():                           # I4
                if abs(ln - edited_line) < 0.6:
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
print(f"       {n_local + n_remote} edit attempts checked against I1-I7")

print()
print("=" * 70)
print("RESULT: ALL PASS" if not FAIL else f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
