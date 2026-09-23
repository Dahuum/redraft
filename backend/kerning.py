"""kerning.py — set new text with the same kerning its producer would have.

Chrome/Skia (and every HarfBuzz-based producer) kerns: in "Fès" the F
advances 6.24pt, not its nominal 6.90, because the font pairs F with è.
An edit placed at nominal widths therefore sits 0.66pt away from where the
producer would have put it — and every glyph after it with it. LibreOffice
does NOT kern the same HTML, so kerning cannot simply be switched on.

So this module answers two questions from the fonts and the page themselves:

  pair_kern(font, a, b)  — the font's own kerning between two characters,
                           from its GPOS 'kern' pair positioning (formats 1
                           and 2) or the legacy 'kern' table, in font units.
  producer_kerns(...)    — does THIS document kern? Measured: for kern pairs
                           that already occur in its text, the observed
                           advance is compared with nominal and with
                           nominal + kern. Only if no pair occurs at all does
                           it fall back to the producer's name.
"""
from __future__ import annotations

import hashlib
import io

_CACHE: dict = {}


class _Kern:
    def __init__(self, font_bytes: bytes):
        from fontTools.ttLib import TTFont
        tt = TTFont(io.BytesIO(font_bytes), lazy=True)
        self.upem = tt["head"].unitsPerEm
        self.cmap = tt.getBestCmap() or {}
        self.pairs: dict = {}
        self.class_tables: list = []      # (coverage set, cd1, cd2, matrix)
        try:
            self._read_gpos(tt)
        except Exception:  # noqa: BLE001 — a malformed table means no kerning
            pass
        if not self.pairs and not self.class_tables:
            try:
                self._read_kern(tt)
            except Exception:  # noqa: BLE001
                pass

    def _read_gpos(self, tt):
        if "GPOS" not in tt:
            return
        gpos = tt["GPOS"].table
        if not gpos.FeatureList or not gpos.LookupList:
            return
        idx = set()
        for fr in gpos.FeatureList.FeatureRecord:
            if fr.FeatureTag == "kern":
                idx.update(fr.Feature.LookupListIndex)
        for li in sorted(idx):
            lookup = gpos.LookupList.Lookup[li]
            for st in lookup.SubTable:
                if lookup.LookupType == 9:
                    st = st.ExtSubTable
                if getattr(st, "LookupType", 2) != 2 and lookup.LookupType not in (2, 9):
                    continue
                fmt = getattr(st, "Format", None)
                if fmt == 1:
                    for first, ps in zip(st.Coverage.glyphs, st.PairSet):
                        for rec in ps.PairValueRecord:
                            v = getattr(rec.Value1, "XAdvance", 0) if rec.Value1 else 0
                            if v:
                                self.pairs.setdefault((first, rec.SecondGlyph), v)
                elif fmt == 2:
                    cov = set(st.Coverage.glyphs)
                    cd1 = st.ClassDef1.classDefs if st.ClassDef1 else {}
                    cd2 = st.ClassDef2.classDefs if st.ClassDef2 else {}
                    matrix = [[(getattr(c2.Value1, "XAdvance", 0) if c2.Value1 else 0)
                               for c2 in c1.Class2Record] for c1 in st.Class1Record]
                    self.class_tables.append((cov, cd1, cd2, matrix))

    def _read_kern(self, tt):
        if "kern" not in tt:
            return
        for sub in tt["kern"].kernTables:
            for (a, b), v in getattr(sub, "kernTable", {}).items():
                if v:
                    self.pairs.setdefault((a, b), v)

    def glyph_pair(self, ga: str, gb: str) -> int:
        v = self.pairs.get((ga, gb))
        if v is not None:
            return v
        for cov, cd1, cd2, matrix in self.class_tables:
            if ga not in cov:
                continue
            c1, c2 = cd1.get(ga, 0), cd2.get(gb, 0)
            if c1 < len(matrix) and c2 < len(matrix[c1]):
                v = matrix[c1][c2]
                if v:
                    return v
                return 0          # first subtable that covers the pair decides
        return 0

    def char_pair(self, a: str, b: str) -> int:
        ga, gb = self.cmap.get(ord(a)), self.cmap.get(ord(b))
        if not ga or not gb:
            return 0
        return self.glyph_pair(ga, gb)


def font_kern(font_bytes: bytes) -> _Kern | None:
    if not font_bytes:
        return None
    key = hashlib.sha256(font_bytes).hexdigest()
    if key not in _CACHE:
        try:
            _CACHE[key] = _Kern(font_bytes)
        except Exception:  # noqa: BLE001
            _CACHE[key] = None
    return _CACHE[key]


def pair_kern_pt(kern: _Kern, a: str, b: str, size: float) -> float:
    """Kerning between characters *a* and *b* at *size*, in points."""
    if kern is None:
        return 0.0
    return kern.char_pair(a, b) * size / kern.upem


def producer_kerns(page, font_display_name: str, kern: _Kern, producer: str = "") -> bool:
    """Does the page's producer apply the font's kerning? Measured first.

    Every adjacent pair of glyphs in one run of this font whose font kern is
    non-zero is a vote: the observed advance (next origin - this origin)
    against the nominal advance (the glyph box width) — kerned if it matches
    nominal + kern, not kerned if it matches nominal. No votes at all falls
    back to the producer: HarfBuzz-based ones (Skia/Chrome) kern by default.
    """
    if kern is None:
        return False
    yes = no = 0
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["font"].split("+")[-1] != font_display_name:
                    continue
                cs = s["chars"]
                for c1, c2 in zip(cs, cs[1:]):
                    k = pair_kern_pt(kern, c1["c"], c2["c"], s["size"])
                    if abs(k) < 0.05:
                        continue
                    nominal = c1["bbox"][2] - c1["bbox"][0]
                    observed = c2["origin"][0] - c1["origin"][0]
                    if abs(observed - (nominal + k)) < 0.15:
                        yes += 1
                    elif abs(observed - nominal) < 0.15:
                        no += 1
    if yes or no:
        return yes > no
    return "skia" in (producer or "").lower()
