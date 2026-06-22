"""annex_model.py — structure a line-item *annex* PDF into a row/column model.

NEW module (engine untouched). This is **Layer 1** of the rules-based annex
automation: it turns the flat list of text spans (from the engine's extractor)
into a real table the app can reason about —

    sections  → the bold/italic group headings
    items     → each line item: label · unit · qty · unitPrice · amount,
                each carrying the span *ids* needed later to restamp or erase it
    total     → the "Total HT" cell

Geometry note: the column x-bands below are tuned to the SWAM/INWI annex family
(numeric columns are right-aligned, so they're matched on their right edge x1).
They live here, not in the engine, so nothing in pdf_editor.py changes.
"""

import re

# ── number parsing/formatting (fr / Morocco: '.' groups, ',' decimals) ──────────

def parse_num(text: str):
    """'1.884,30' → 1884.3 · '6.281 ' → 6281.0 · '0,3' → 0.3 · '' → None."""
    t = (text or "").strip().replace(" ", " ").replace(" ", "")
    if not t:
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def fmt_num(value: float, decimals: int = 2) -> str:
    """1884.3 → '1.884,30' · (6281, 0) → '6.281'  (grouping + decimal swapped)."""
    s = f"{value:,.{decimals}f}"          # '1,884.30'
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


# ── column classification ───────────────────────────────────────────────────────

def _column_of(bbox) -> str | None:
    """Which table column a span sits in, by its x position (None = outside)."""
    x0, _y0, x1, _y1 = bbox
    xc = (x0 + x1) / 2
    if 310 < xc < 366:        # 'Op.' / 'F'
        return "unit"
    if 372 < x1 < 412:        # Qté (right-aligned)
        return "qty"
    if 440 < x1 < 478:        # Prix Unitaire
        return "price"
    if 495 < x1 < 545:        # Montant Total HT
        return "amount"
    if x0 < 300:              # Prestation / section title
        return "label"
    return None


def _cy(span) -> float:
    b = span["bbox"]
    return (b[1] + b[3]) / 2


# ── header fields (label + value living in one span) ─────────────────────────────
# Each entry: (key, human label, regex with group(1)=prefix to KEEP, group(2)=value).
_HEADER_PATTERNS = [
    ("factureNo",  "Facture N°",        r"^(Annexe.*?N°\s*)(.+)$"),
    ("clientName", "Nom client",        r"^(Nom\s+Client\s*:\s*)(.+)$"),
    ("clientRef",  "Référence client",  r"^(R[ée]f[ée]rence\s+Client\s*:\s*)(.*)$"),
    ("ice",        "ICE N°",            r"^(ICE\s*N°\s*:?\s*)(.+)$"),
    # Période keeps no prefix — the whole "Du … au …" is the value, so what the
    # user sees is exactly what they type (avoids a doubled "Du").
    ("periode",    "Période",           r"^()(Du\s+.+)$"),
]


def detect_headers(spans: list) -> list:
    """Find the document-info fields (facture N°, client, ICE, période…) in the
    header band. Each is one span 'Label : value' — we keep the label (prefix)
    and expose the value so it can be swapped per client.
    """
    out, seen = [], set()
    for s in spans:
        if not (100 < s["bbox"][1] < 280):   # header band only (skip footer ICE)
            continue
        text = (s["text"] or "").rstrip()
        for key, label, pat in _HEADER_PATTERNS:
            if key in seen:
                continue
            m = re.match(pat, text)
            if m:
                out.append({"key": key, "label": label,
                            "prefix": m.group(1),
                            "value": (m.group(2) or "").strip(),
                            "spanId": s["id"]})
                seen.add(key)
                break
    return out


# ── model builder ────────────────────────────────────────────────────────────────

def build_model(spans: list) -> dict:
    """spans: the /extract list (each with a stable integer `id`).

    Returns {"items": [...], "sections": [...], "total": {...}|None}. Every item
    references span ids so a later step can fill (restamp) or remove (erase) it.
    """
    # 1. Restrict to the table band (below the column header, above the footer).
    region = [s for s in spans if 280 < s["bbox"][1] < 700]

    # 2. Cluster spans into rows by vertical centre.
    rows: list = []
    for s in sorted(region, key=_cy):
        if rows and abs(_cy(s) - rows[-1]["cy"]) <= 6:
            rows[-1]["spans"].append(s)
        else:
            rows.append({"cy": _cy(s), "spans": [s]})

    model = {"items": [], "sections": [], "total": None}
    current_section = None
    current_section_index = None   # which section the next item belongs under

    for r in rows:
        cols: dict = {}
        for sp in r["spans"]:
            c = _column_of(sp["bbox"])
            if c:
                cols.setdefault(c, []).append(sp)
        joined = " ".join(sp["text"].strip() for sp in r["spans"])

        # Total row (label sits in the price column, value in amount).
        if "Total" in joined and "amount" in cols:
            amt = cols["amount"][0]
            model["total"] = {
                "label": " ".join(sp["text"].strip()
                                  for sp in r["spans"] if sp is not amt),
                "value": amt["text"].strip(),
                "valueId": amt["id"],
            }
            continue

        is_item = "amount" in cols or "qty" in cols
        if is_item:
            def first(col):
                return cols[col][0] if col in cols else None
            qty, price, amt = first("qty"), first("price"), first("amount")
            unit = first("unit")
            label_sps = sorted(cols.get("label", []), key=lambda s: s["bbox"][0])
            # Link this line to the section above it (so an emptied section's
            # title can be removed with its last child).
            if current_section_index is not None:
                model["sections"][current_section_index]["itemIdx"].append(
                    len(model["items"]))
            model["items"].append({
                "section":   current_section,
                "label":     " ".join(sp["text"].strip() for sp in label_sps),
                "unit":      unit["text"].strip() if unit else "",
                "qty":       qty["text"].strip() if qty else "",
                "unitPrice": price["text"].strip() if price else "",
                "amount":    amt["text"].strip() if amt else "",
                "spanIds": {
                    "label": [sp["id"] for sp in label_sps],
                    "unit":  unit["id"] if unit else None,
                    "qty":   qty["id"] if qty else None,
                    "price": price["id"] if price else None,
                    "amount": amt["id"] if amt else None,
                },
            })
        elif cols.get("label"):
            # A heading with no numbers → a section title.
            label_sps = sorted(cols["label"], key=lambda s: s["bbox"][0])
            title = " ".join(sp["text"].strip() for sp in label_sps)
            current_section = title
            current_section_index = len(model["sections"])
            model["sections"].append({"title": title,
                                      "ids": [sp["id"] for sp in label_sps],
                                      "itemIdx": []})

    model["headers"] = detect_headers(spans)
    return model


# ── Layer 2: rules → concrete edits ──────────────────────────────────────────────

def _fmt_qty(value: float) -> str:
    """Quantities print as integers when whole ('6.281', '9'), else with decimals."""
    if value == int(value):
        return fmt_num(value, 0)
    return fmt_num(value)


def plan_edits(model: dict, spec: dict | None = None) -> list:
    """Turn a per-document *spec* into a flat list of (span_id, new_text) edits.

    spec maps an item's index in ``model["items"]`` to an action:
        {idx: {"remove": True}}            → blank the whole line
        {idx: {"qty": 500}}                → set qty, recompute that line's amount
    Items absent from spec keep their original quantity. The Total HT is always
    recomputed from the kept lines. Empty new_text ("") erases a cell (blank).

    Pure function — no engine, no I/O — so it's trivially testable.
    """
    spec = spec or {}
    edits: list = []
    total = 0.0

    for idx, it in enumerate(model["items"]):
        action = spec.get(idx, {})
        ids = it["spanIds"]

        if action.get("remove"):
            for key in ("unit", "qty", "price", "amount"):
                if ids[key] is not None:
                    edits.append((ids[key], ""))
            for sid in ids["label"]:
                edits.append((sid, ""))
            continue

        unit_price = parse_num(it["unitPrice"]) or 0.0

        # Quantity: overridden by spec, else the original.
        if action.get("qty") not in (None, ""):
            qv = (float(action["qty"]) if isinstance(action["qty"], (int, float))
                  else parse_num(str(action["qty"])))
            qv = qv if qv is not None else (parse_num(it["qty"]) or 0.0)
            new_qty_text = _fmt_qty(qv)
            if ids["qty"] is not None and new_qty_text != it["qty"]:
                edits.append((ids["qty"], new_qty_text))
        else:
            qv = parse_num(it["qty"]) or 0.0

        amount = round(qv * unit_price, 2)
        total += amount

        orig_amount = parse_num(it["amount"])
        if ids["amount"] is not None and (
                orig_amount is None or abs(amount - orig_amount) >= 0.005):
            edits.append((ids["amount"], fmt_num(amount)))

    # Dynamic title cleanup: a section whose every child line was removed loses
    # its title too (no dangling empty heading).
    removed = {idx for idx, action in spec.items() if action.get("remove")}
    for sec in model.get("sections", []):
        kids = sec.get("itemIdx", [])
        if kids and all(k in removed for k in kids):
            for sid in sec.get("ids", []):
                edits.append((sid, ""))

    if model.get("total") and model["total"].get("valueId") is not None:
        edits.append((model["total"]["valueId"], fmt_num(round(total, 2))))

    return edits


def plan_header_edits(headers: list, header_spec: dict | None = None) -> list:
    """Turn {header_key: new_value} into (span_id, new_text) edits, re-attaching
    each field's preserved label prefix (e.g. 'Nom Client : ' + 'Acme')."""
    by_key = {h["key"]: h for h in (headers or [])}
    edits = []
    for key, val in (header_spec or {}).items():
        h = by_key.get(key)
        if not h:
            continue
        v = "" if val is None else str(val).strip()
        edits.append((h["spanId"], h["prefix"] + v))
    return edits
