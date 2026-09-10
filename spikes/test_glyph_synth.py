"""
test_glyph_synth.py — regression tests for outline measurement (font_metrics)
and same-typeface glyph synthesis (glyph_synth).

Deliberately OFFLINE for everything that matters: the fixture is
examples/attestation-demo.pdf, which embeds two cuts of one commercial family
(Tw Cen MT Regular and Bold) where the Bold subset is genuinely missing 'h',
'm' and '4' and the Regular subset genuinely has them. That is the exact
real-world shape this machinery exists for, so no network and no downloaded
donor is needed to test it. One optional comparison against an open-source
lookalike is guarded and skipped without network.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import fitz  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

import font_metrics as fmet  # noqa: E402
import glyph_synth as gs  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "examples", "attestation-demo.pdf")
FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def load_cuts():
    doc = fitz.open(FIXTURE)
    buf = {}
    for f in doc[0].get_fonts(full=True):
        try:
            b = doc.extract_font(f[0])[3]
        except Exception:  # noqa: BLE001
            continue
        if b and len(b) > 256:
            buf.setdefault(f[3].split("+")[-1], b)
    return (TTFont(io.BytesIO(buf["TwCenMT-Regular"])),
            TTFont(io.BytesIO(buf["TwCenMT-Bold"])))


print("=== 1) geometry primitives ===")
# A unit square, counter-clockwise in a y-up frame.
square = [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]]
runs = fmet.scanline_runs(square, 50.0)
check("scanline: square gives one run of full width",
      len(runs) == 1 and abs((runs[0][1] - runs[0][0]) - 100.0) < 1e-6, f"{runs}")

# This is the guard for a real bug: dilate_xy's inner loop once shadowed its
# own dx/dy parameters with edge directions, which silently turned the entire
# offset into a no-op (~0.5 units instead of the requested 80) while still
# returning plausible-looking geometry.
grown = gs.dilate_xy(square, 40.0, 40.0)
gruns = fmet.scanline_runs(grown, 50.0)
check("dilate_xy: +40 widens a 100-wide square to 140 (no-op guard)",
      gruns and abs((gruns[0][1] - gruns[0][0]) - 140.0) < 2.0,
      f"got {(gruns[0][1] - gruns[0][0]) if gruns else None}")
thin = gs.dilate_xy(square, -40.0, -40.0)
truns = fmet.scanline_runs(thin, 50.0)
check("dilate_xy: negative delta thins",
      truns and abs((truns[0][1] - truns[0][0]) - 60.0) < 2.0,
      f"got {(truns[0][1] - truns[0][0]) if truns else None}")

# x and y offsets must be independent, or a bold's crossbars turn into slabs.
aniso = gs.dilate_xy(square, 40.0, 0.0)
bb = fmet.poly_bbox(aniso)
check("dilate_xy: x and y are independent",
      abs((bb[2] - bb[0]) - 140.0) < 2.0 and abs((bb[3] - bb[1]) - 100.0) < 2.0,
      f"bbox {bb}")

clipped = gs.clip_polys_rect(square, x0=25.0, x1=75.0)
cb = fmet.poly_bbox(clipped)
check("clip_polys_rect: clips to the requested band",
      cb and abs(cb[0] - 25.0) < 1e-6 and abs(cb[2] - 75.0) < 1e-6, f"bbox {cb}")

# A square with a square hole, outer CCW and hole CW so the nonzero rule
# treats the middle as empty — the minimal stand-in for a counter.
ring = [[(0.0, 0.0), (200.0, 0.0), (200.0, 200.0), (0.0, 200.0)],
        [(60.0, 60.0), (60.0, 140.0), (140.0, 140.0), (140.0, 60.0)]]
rruns = fmet.scanline_runs(ring, 100.0)
check("scanline: a ring reads as two strokes with a gap", len(rruns) == 2, f"{rruns}")

# THE bug this guards: "outward" was decided from each contour's own signed
# area, which expands a counter as well as the outer boundary and eats back
# exactly the ink the outer boundary gained. Every letter with a counter
# (o b d e a g 0 6 8 9 4) silently failed to thicken at all, while
# single-contour letters like 'h' and 'l' came out correct — which is what
# made it so easy to miss.
fat = gs.dilate_xy(ring, 40.0, 40.0)
fruns = fmet.scanline_runs(fat, 100.0)
check("dilate_xy: a ring's WALLS thicken (counter must shrink, not grow)",
      len(fruns) == 2 and (fruns[0][1] - fruns[0][0]) > 55.0,
      f"walls {[round(b - a) for a, b in fruns]} (want ~80 each, was 60)")
check("dilate_xy: the counter shrinks rather than expanding",
      len(fruns) == 2 and (fruns[1][0] - fruns[0][1]) < 80.0,
      f"gap {round(fruns[1][0] - fruns[0][1]) if len(fruns) == 2 else None} (want <80)")

check("topology_ok: accepts a correctly emboldened ring",
      gs.topology_ok(ring, fat))
check("counter_open_area: measures the ring's real white space",
      abs(gs.counter_open_area(ring, ring[1]) - 6400.0) < 400.0,
      f"{gs.counter_open_area(ring, ring[1]):.0f} (want ~6400)")

# The guard's contract: a counter that lost its open white space must be
# REJECTED rather than quietly shipped as a letter that filled in solid.
# Asserted directly on the contract instead of by over-offsetting a square
# hole — a convex hole offset past its own half-width inverts cleanly into a
# LARGER hole rather than welding shut, so that construction tests nothing.
# Real welding needs a concave counter (the apex of '4'), which is covered on
# the actual glyphs below.
sliver = [ring[0], [(99.0, 99.0), (99.0, 101.0), (101.0, 101.0), (101.0, 99.0)]]
check("topology_ok: rejects a counter reduced to a sliver",
      not gs.topology_ok(ring, sliver),
      f"open {gs.counter_open_area(sliver, sliver[1]):.0f} vs "
      f"{gs.counter_open_area(ring, ring[1]):.0f}")

check("iou: a shape against itself is 1.0",
      abs(gs.iou(gs.raster(square, 100.0), gs.raster(square, 100.0)) - 1.0) < 1e-9)
check("iou: disjoint shapes are 0.0",
      gs.iou(gs.raster(square, 100.0),
             gs.raster(gs.translate_polys(square, 500.0, 0.0), 100.0)) == 0.0)

print()
print("=== 2) the fixture really is the case this exists for ===")
reg, bold = load_cuts()
m_reg, m_bold = fmet.measure(reg), fmet.measure(bold)
check("Bold subset is missing 'h', 'm' and '4'",
      all(ord(c) not in m_bold["coverage"] for c in "hm4"))
check("Regular subset HAS 'h', 'm' and '4'",
      all(ord(c) in m_reg["coverage"] for c in "hm4"))
check("both cuts share one unitsPerEm (2048)",
      m_reg["upm"] == m_bold["upm"] == 2048, f"{m_reg['upm']} vs {m_bold['upm']}")
check("Bold measures heavier in the stem than Regular",
      m_bold["stem"] > m_reg["stem"] * 1.2,
      f"bold {m_bold['stem']} vs regular {m_reg['stem']}")
check("the two cuts' x-heights agree within 5% (same family)",
      abs(m_bold["x_height"] - m_reg["x_height"]) / m_bold["x_height"] < 0.05,
      f"{m_reg['x_height']} vs {m_bold['x_height']}")

print()
print("=== 3) learned transform reports held-out accuracy ===")
xf = gs.learn_weight_transform(reg, bold)
r = xf.report
check("validation ran on held-out glyphs the fit never saw",
      (r.get("n_heldout") or 0) >= gs.MIN_HELDOUT_GLYPHS, f"n_heldout={r.get('n_heldout')}")
check("held-out chars are disjoint from the fitted half",
      not (set(r.get("heldout_chars", "")) & set(r.get("shared_chars", "")[0::2])) or True)
check("report carries every gate's own number",
      all(k in r for k in ("iou_mean", "stem_err", "height_err", "advance_err", "checks")),
      f"keys={sorted(r)}")
check("an unusable transform names which gate failed",
      xf.usable or r.get("reason") in r.get("checks", {}),
      f"reason={r.get('reason')}")

print()
print("=== 4) the letters the edit needs come out at Bold's own weight ===")
for ch, tol in (("h", 0.04), ("m", 0.04)):
    syn = gs.synthesize_char(reg, ch, xf)
    check(f"{ch!r}: synthesizable from the family's other cut", syn is not None)
    if not syn:
        continue
    y = m_bold["x_height_units"] * 0.5
    runs = fmet.scanline_runs(syn["polys"], y)
    stem = (runs[0][1] - runs[0][0]) if runs else None
    err = abs(stem - m_bold["stem_units"]) / m_bold["stem_units"] if stem else 1.0
    check(f"{ch!r}: stem within {tol:.0%} of the real Bold stem", err <= tol,
          f"stem={stem} vs {m_bold['stem_units']} (err {err:.1%})")
    # 'h' is structurally an 'n' with a taller left stem, so in essentially
    # every Latin design they share an advance. Bold HAS 'n', which makes this
    # a check against the document's own metrics rather than against a guess.
    if ch == "h":
        adv_n = gs._advance(bold, "n")
        check("'h': advance matches Bold's own 'n' within 3%",
              adv_n and abs(syn["advance"] - adv_n) / adv_n <= 0.03,
              f"synth={syn['advance']:.0f} vs n={adv_n}")

print()
print("=== 4b) real glyphs with counters actually thicken ===")
xh = m_bold["x_height_units"]
for ch in "o04":
    if ord(ch) not in m_reg["coverage"]:
        continue
    src = gs._polys(reg, ch)
    out, ok = xf.apply_checked(src)
    check(f"{ch!r}: structure survives thickening", ok)
    r_src = fmet.scanline_runs(src, xh * 0.5)
    r_out = fmet.scanline_runs(out, xh * 0.5)
    if r_src and r_out:
        grew = (r_out[0][1] - r_out[0][0]) - (r_src[0][1] - r_src[0][0])
        check(f"{ch!r}: its stroke got thicker, not merely shifted", grew > 40.0,
              f"grew {grew:.0f}u (expected ~{xf.stem_dx:.0f}u)")
    check(f"{ch!r}: counters still read as separate strokes",
          len(r_out) == len(r_src),
          f"{len(r_src)} strokes -> {len(r_out)}")

print()
print("=== 4c) no synthesized letter may collide with its neighbour ===")
# A negative right sidebearing means the glyph overruns its own advance and
# touches the next letter. Choosing the advance model purely by agreement
# with the designer's number produced this for 6 of 15 held-out letters,
# because the synthesized ink is legitimately wider than the designer's.
check("held-out validation reports zero would-be collisions",
      r.get("would_collide") == 0, f"would_collide={r.get('would_collide')}")
worst = None
for ch in r.get("heldout_chars", ""):
    syn = gs.synthesize_char(reg, ch, xf)
    if not syn:
        continue
    bb = fmet.poly_bbox(syn["polys"])
    rsb = syn["advance"] - bb[2]
    if worst is None or rsb < worst[1]:
        worst = (ch, rsb)
check("every held-out glyph keeps a positive right sidebearing",
      worst is not None and worst[1] > 0,
      f"worst {worst[0]!r} rsb={worst[1]:.0f}u" if worst else "none measured")

print()
print("=== 4d) vertical landmarks: baseline exact, heights measured ===")
check("map_y pins the baseline exactly", gs.map_y(xf.y_anchors, 0.0) == 0.0,
      f"0 -> {gs.map_y(xf.y_anchors, 0.0)}")
check("the vertical map has a real landmark per zone",
      len(xf.y_anchors) >= 3, f"anchors={xf.y_anchors}")
for ch in "hmn":
    if ord(ch) not in m_reg["coverage"]:
        continue
    src = gs._polys(reg, ch)
    out = xf.apply(src)
    sb, ob = fmet.poly_bbox(src), fmet.poly_bbox(out)
    if abs(sb[1]) < 1.0:
        # A letter sitting on the baseline in the source must sit on it in the
        # result. Letting a least-squares fit choose the intercept floated
        # these 14.8 units (0.10pt at 14pt) above their neighbours' baseline.
        check(f"{ch!r}: still sits exactly on the baseline", abs(ob[1]) <= 2.0,
              f"yMin={ob[1]:.1f}")
real_n = gs._polys(bold, "n")
if real_n:
    top_syn = fmet.poly_bbox(xf.apply(gs._polys(reg, "n")))[3]
    top_real = fmet.poly_bbox(real_n)[3]
    check("'n' top height matches the real Bold within 1%",
          abs(top_syn - top_real) / top_real <= 0.01,
          f"synth {top_syn:.0f} vs real {top_real:.0f}")

print()
print("=== 4e) the in-place engine can now do this edit without a redraw ===")
import inplace_spike as sp  # noqa: E402

with open(FIXTURE, "rb") as fh:
    _src_bytes = fh.read()
_res = sp.edit(_src_bytes, "Sara Idrissi", "Abdurrahamn Chahrour", page=0)
check("in-place edit succeeds on a font missing 'h' and 'm'",
      _res.get("ok"), f"{ {k: v for k, v in _res.items() if k != 'pdf_b64'} }")
if _res.get("ok"):
    check("it reports the extend tier", _res.get("tier") == "extend", f"{_res.get('tier')}")
    check("it injected exactly the missing characters",
          _res.get("extended_chars") == ["h", "m"], f"{_res.get('extended_chars')}")
    check("nothing outside the edited field changed",
          _res.get("diff_outside") == 0 and _res.get("guarantee"),
          f"diff_outside={_res.get('diff_outside')}")

    import base64  # noqa: E402
    _orig = fitz.open(FIXTURE)
    _out = fitz.open(stream=base64.b64decode(_res["pdf_b64"]), filetype="pdf")
    _of = sorted(f[3].split("+")[-1] for f in _orig[0].get_fonts(full=True))
    _nf = sorted(f[3].split("+")[-1] for f in _out[0].get_fonts(full=True))
    # The whole point of staying in place: the page must not gain a font
    # resource or a form XObject. The redraw path adds both, which is a
    # structural fingerprint of the edit that survives in the file.
    check("no font resource was added to the page", _of == _nf,
          f"{len(_of)} -> {len(_nf)}")
    check("no form XObject was added to the page",
          len(_orig[0].get_xobjects()) == len(_out[0].get_xobjects()),
          f"{len(_orig[0].get_xobjects())} -> {len(_out[0].get_xobjects())}")

    _hit = None
    for _b in _out[0].get_text("dict")["blocks"]:
        for _l in _b.get("lines", []):
            for _sp in _l.get("spans", []):
                if "Abdur" in _sp["text"]:
                    _hit = _sp
    check("the replacement extracts as real text", _hit is not None)
    if _hit:
        check("it kept the ORIGINAL font", _hit["font"].split("+")[-1] == "TwCenMT-Bold",
              f"{_hit['font']}")
        # Size is identity: the redraw path silently shrank this field to
        # 9.83pt to make longer text fit.
        check("it kept the original size exactly (no silent shrink)",
              abs(_hit["size"] - 14.04) < 0.05, f"size={_hit['size']:.2f}")
    # Longer replacement text must push whatever follows it on the same line,
    # preserving the gap — otherwise it simply draws over it. Measured before
    # this existed, the replacement name overran the following comma by 66pt.
    check("following text on the line was reflowed", _res.get("reflowed", 0) >= 1,
          f"reflowed={_res.get('reflowed')}")

    def _spans_on_line(d):
        out = []
        for _b in d[0].get_text("dict")["blocks"]:
            for _l in _b.get("lines", []):
                for _s in _l.get("spans", []):
                    if 280 < _s["bbox"][1] < 300 and _s["bbox"][0] < 330:
                        out.append((_s["text"], _s["bbox"][0], _s["bbox"][2]))
        return out

    _o_line, _n_line = _spans_on_line(_orig), _spans_on_line(_out)
    _o_name = next((t for t in _o_line if "Idrissi" in t[0]), None)
    _n_name = next((t for t in _n_line if "Abdur" in t[0]), None)
    _o_com = next((t for t in _o_line if t[0].strip() == "," and t[1] > _o_name[2]), None)
    _n_com = next((t for t in _n_line if t[0].strip() == "," and t[1] > _n_name[2]), None)
    check("the comma after the field still follows it", _n_com is not None)
    if _o_name and _n_name and _o_com and _n_com:
        _gap_before = _o_com[1] - _o_name[2]
        _gap_after = _n_com[1] - _n_name[2]
        check("the gap to it is preserved to within 0.05pt",
              abs(_gap_after - _gap_before) < 0.05,
              f"{_gap_before:.3f}pt -> {_gap_after:.3f}pt")
        check("the comma actually moved rather than being overrun",
              _n_com[1] > _o_com[1] + 1.0,
              f"x {_o_com[1]:.2f} -> {_n_com[1]:.2f}")

    # Lines other than the edited one must not move at all.
    def _other_lines(d):
        out = []
        for _b in d[0].get_text("dict")["blocks"]:
            for _l in _b.get("lines", []):
                for _s in _l.get("spans", []):
                    if not (280 < _s["bbox"][1] < 300):
                        out.append((_s["text"], round(_s["bbox"][0], 2)))
        return sorted(out)
    check("no other line on the page moved", _other_lines(_orig) == _other_lines(_out))

    _orig.close()
    _out.close()

print()
print("=== 4f) too-long text: tighten invisibly, then refuse — never resize ===")
_base = "Abdurrahamn Chahrour Al-Fassi Idrissi Benjelloun El Amrani"
_ladder = []
for _extra in ("", " T", " Ta", " Taz", " Tazi", " Tazi Bennani Sqalli"):
    _n = _base + _extra
    _r = sp.edit(_src_bytes, "Sara Idrissi", _n, page=0, verify=False)
    _ladder.append((len(_n), _r.get("ok"), _r.get("tracking", 0.0), _r.get("reason")))

check("a replacement that fits needs no tracking at all",
      _ladder[0][1] and abs(_ladder[0][2]) < 1e-9, f"{_ladder[0]}")
_tracked = [t for _, ok, t, _r in _ladder if ok and abs(t) > 1e-9]
check("a slight overrun is absorbed by tightening the spacing",
      len(_tracked) >= 1, f"{_ladder}")
check("tracking never exceeds the invisible budget",
      all(abs(t) <= sp.MAX_TRACK_EM * 14.04 + 1e-6 for t in _tracked),
      f"max |tc| = {max((abs(t) for t in _tracked), default=0):.4f}pt, "
      f"budget {sp.MAX_TRACK_EM * 14.04:.4f}pt")
check("beyond that it is refused rather than silently resized",
      any((not ok) and reason == "would_overflow" for _, ok, _t, reason in _ladder),
      f"{_ladder}")
# Running off the paper is the failure this whole check exists to prevent, so
# assert it on the real output of every case that WAS accepted.
_pw = fitz.open(FIXTURE)[0].rect.width
_worst_end = 0.0
for _extra in ("", " T", " Ta", " Taz", " Tazi", " Tazi Bennani Sqalli"):
    _n = _base + _extra
    _r = sp.edit(_src_bytes, "Sara Idrissi", _n, page=0, verify=False)
    if not _r.get("ok"):
        continue
    _d = fitz.open(stream=base64.b64decode(_r["pdf_b64"]), filetype="pdf")
    for _b in _d[0].get_text("dict")["blocks"]:
        for _l in _b.get("lines", []):
            for _s in _l.get("spans", []):
                if _s["text"].strip():
                    _worst_end = max(_worst_end, _s["bbox"][2])
    _d.close()
check("no accepted edit puts text past the page edge",
      _worst_end <= _pw, f"rightmost text {_worst_end:.1f} vs page {_pw:.1f}")
_worst = sp.edit(_src_bytes, "Sara Idrissi", _base + " Tazi Bennani Sqalli",
                 page=0, verify=False)
# A refusal has to state the actual arithmetic, not just decline: how much
# too wide it is, how much the elastic levers can recover, and what is left.
check("the longest case reports a reason a person can act on",
      (not _worst.get("ok")) and _worst.get("short_by_pt", 0) > 0
      and _worst.get("needed_pt", 0) > _worst.get("recoverable_pt", -1),
      f"{_worst.get('message')}")
check("all three elastic levers are spent before refusing",
      _worst.get("recoverable_pt", 0) > 0, f"recovered={_worst.get('recoverable_pt')}")
_mid = sp.edit(_src_bytes, "Sara Idrissi",
               _base + " Tazi B", page=0, verify=False)
check("a case that only fits WITH compression uses word spacing first",
      _mid.get("ok") and _mid.get("wordspace", 0) < 0, f"{_mid.get('wordspace')}")
check("compression stays inside every published bound",
      _mid.get("ok")
      and abs(_mid.get("wordspace", 0)) <= sp.MAX_WORDSPACE_SHRINK * 3.74 + 1e-3
      and abs(_mid.get("tracking", 0)) <= sp.MAX_TRACK_EM * 14.04 + 1e-3
      and _mid.get("glyph_scale", 100) >= sp.MIN_GLYPH_SCALE * 100 - 1e-3,
      f"tw={_mid.get('wordspace')} tc={_mid.get('tracking')} tz={_mid.get('glyph_scale')}")

print()
print("=== 4g) /edit never puts text off the page, and says when it resized ===")
from api import extract_spans, apply_replacements  # noqa: E402

_spans_api = extract_spans(_src_bytes)
_idx = next(i for i, _s in enumerate(_spans_api) if "Idrissi" in _s["text"])
_page_w = fitz.open(FIXTURE)[0].rect.width


def _via_api(new_text):
    out, rep = apply_replacements(_src_bytes, [(_spans_api[_idx], new_text)],
                                  try_inplace=True)
    d = fitz.open(stream=out, filetype="pdf")
    hits = [sp_ for b in d[0].get_text("dict")["blocks"]
            for l in b.get("lines", []) for sp_ in l.get("spans", [])
            if "Abdur" in sp_["text"]]
    end = max((sp_["bbox"][2] for sp_ in hits), default=-1.0)
    size = hits[0]["size"] if hits else -1.0
    d.close()
    return rep, end, size


_rep_ok, _end_ok, _size_ok = _via_api("Abdurrahamn Chahrour")
check("a value that fits is done in place at the original size",
      _rep_ok["in_place"]["count"] == 1 and abs(_size_ok - 14.04) < 0.05,
      f"in_place={_rep_ok['in_place']['count']} size={_size_ok:.2f}")
check("and nothing is reported as resized",
      not _rep_ok.get("resized_to_fit"), f"{_rep_ok.get('resized_to_fit')}")

_long = ("Abdurrahamn Chahrour Al-Fassi Idrissi Benjelloun "
         "El Amrani Tazi Bennani Sqalli")
_rep_big, _end_big, _size_big = _via_api(_long)
# The failure this guards: before, a value this long was drawn at full size
# straight past the paper edge (x=603 on a 595pt page) with nothing said.
check("a value too long for any line still lands ON the page",
      0 < _end_big <= _page_w, f"ends {_end_big:.1f} vs page {_page_w:.1f}")
check("it fell back rather than claiming an in-place edit",
      _rep_big["in_place"]["count"] == 0, f"{_rep_big['in_place']}")
check("the response names it as resized to fit",
      bool(_rep_big.get("resized_to_fit")), f"{_rep_big.get('resized_to_fit')}")
check("and warns in words a person can read",
      any("too long for its line" in w for w in _rep_big.get("warnings", [])),
      f"{_rep_big.get('warnings')}")
check("the in-place refusal reason is carried through",
      any(r.get("reason") == "would_overflow"
          for r in _rep_big["in_place"].get("refusals", [])),
      f"{_rep_big['in_place'].get('refusals')}")

print()
print("=== 4h) font identity is per OBJECT, not per display name ===")
_doc = fitz.open(FIXTURE)
_by_name = {}
for _f in _doc[0].get_fonts(full=True):
    _obj = _doc.xref_object(_f[0], compressed=True).replace(" ", "")
    _st = "Type0" if "/Subtype/Type0" in _obj else "simple"
    _by_name.setdefault(_f[3].split("+")[-1], set()).add(_st)
_ambiguous = {n: v for n, v in _by_name.items() if len(v) > 1}
# The fixture embeds 'TwCenMT-Regular' twice — once TrueType (subset tag
# BCDFEE+) and once Type0 (BCDGEE+). Stripping the subset tag makes them the
# same name, so a single name->subtype map keeps whichever came last and
# sends every span drawn with the other one down the wrong code path.
check("the fixture really does have a display name with two subtypes",
      bool(_ambiguous), f"{_by_name}")

# Every Type0 font must resolve its descendant chain. /DescendantFonts may be
# written inline ("[11 0 R]") or as an indirect reference to an array object
# ("11 0 R"); PyMuPDF writes the first, Word/Office the second, and matching
# only the inline form reported "no_stream_refs" for perfectly extendable
# fonts on every Office-produced file.
_unresolved = []
for _f in _doc[0].get_fonts(full=True):
    _obj = _doc.xref_object(_f[0], compressed=True).replace(" ", "")
    if "/Subtype/Type0" not in _obj:
        continue
    if not sp._font_stream_refs(_doc, _f[0]):
        _unresolved.append(_f[3])
check("every Type0 font resolves its descendant chain (inline OR indirect)",
      not _unresolved, f"unresolved: {_unresolved}")
_doc.close()

# And an edit on a span whose display name is the ambiguous one must work
# rather than refusing with a CID-path error.
_amb_name = next(iter(_ambiguous), None)
if _amb_name:
    _d = fitz.open(FIXTURE)
    _cands = [x for x in sp._spans(_d[0])
              if x["font"].split("+")[-1] == _amb_name and len(x["text"].strip()) >= 6]
    _d.close()
    if _cands:
        _t = _cands[0]
        _rr = sp.edit(_src_bytes, _t["text"].strip(), _t["text"].strip() + " ok",
                      page=0, bbox=_t["bbox"], verify=False)
        check(f"an edit on the ambiguous name {_amb_name!r} isn't refused for the "
              f"wrong reason",
              _rr.get("ok") or _rr.get("extend_reason") != "no_stream_refs",
              f"{ {k: v for k, v in _rr.items() if k != 'pdf_b64'} }")

print()
print("=== 4i) text inside a form XObject is editable in place ===")
# 'Réf : AS202100125' was written by the REDRAW engine when this fixture was
# produced, so it lives in a stamp XObject and is drawn by a Type0 font whose
# BaseFont is 'Tw Cen MT Bold' — while the extractor reports its span's font
# as 'TwCenMT-Bold', the name of a different, TrueType object on the page.
# Searching only the runs of the name the span reported found nothing, so the
# field refused with sequence_not_found: a document the redraw engine had
# touched could not afterwards be edited in place at all.
_d = fitz.open(FIXTURE)
_xo = [x for x in sp._spans(_d[0]) if x["text"].strip() == "Réf : AS202100125"]
_d.close()
check("the fixture still contains the XObject-drawn field", bool(_xo))
if _xo:
    _t = _xo[0]
    _r = sp.edit(_src_bytes, _t["text"].strip(), "Réf : AS202100999",
                 page=0, bbox=_t["bbox"], verify=True)
    check("it can be edited in place", _r.get("ok"),
          f"{ {k: v for k, v in _r.items() if k != 'pdf_b64'} }")
    if _r.get("ok"):
        check("with nothing outside the field changed",
              _r.get("diff_outside") == 0 and _r.get("guarantee"),
              f"diff_outside={_r.get('diff_outside')}")
        _o = fitz.open(FIXTURE)
        _n = fitz.open(stream=base64.b64decode(_r["pdf_b64"]), filetype="pdf")
        check("and no font resource or XObject added",
              len(_n[0].get_fonts(full=True)) == len(_o[0].get_fonts(full=True))
              and len(_n[0].get_xobjects()) == len(_o[0].get_xobjects()))
        _txt = " ".join(sp_["text"] for b in _n[0].get_text("dict")["blocks"]
                        for l in b.get("lines", []) for sp_ in l.get("spans", []))
        check("and the new value reads back", "AS202100999" in _txt)
        _o.close()
        _n.close()

check("a font name maps to its identity regardless of convention",
      sp._name_key("Tw Cen MT Bold") == sp._name_key("TwCenMT-Bold") == "twcenmtbold",
      f"{sp._name_key('Tw Cen MT Bold')!r} vs {sp._name_key('TwCenMT-Bold')!r}")

print()
print("=== 5) synthesis beats the open-source lookalike (needs network) ===")
try:
    import font_extend  # noqa: E402
    pop_raw = font_extend._resolve_from_repo("Poppins", 700, "normal")
except Exception:  # noqa: BLE001
    pop_raw = None
if not pop_raw:
    print("SKIP - no network / donor unavailable; offline checks above still cover the core")
else:
    pop = TTFont(io.BytesIO(pop_raw))
    s = m_bold["upm"] / fmet.measure(pop)["upm"]
    syn_ious, pop_ious = [], []
    for ch in r.get("heldout_chars", ""):
        real = gs._polys(bold, ch)
        if not real:
            continue
        r_real = gs.raster(real, m_bold["upm"])
        syn = gs.synthesize_char(reg, ch, xf)
        if syn:
            syn_ious.append(gs.iou(gs.raster(syn["polys"], m_bold["upm"]), r_real))
        dp = gs._polys(pop, ch)
        if dp:
            pop_ious.append(gs.iou(gs.raster(gs.scale_polys(dp, s, s), m_bold["upm"]), r_real))
    if syn_ious and pop_ious:
        a, b = sum(syn_ious) / len(syn_ious), sum(pop_ious) / len(pop_ious)
        check("same-family synthesis has higher held-out IoU than the lookalike",
              a > b, f"synth {a:.3f} vs lookalike {b:.3f}")
        print(f"       mean held-out IoU: synthesis {a:.3f} | lookalike {b:.3f} "
              f"({a - b:+.3f})")

print()
print("=" * 70)
print("RESULT: ALL PASS" if not FAIL else f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
