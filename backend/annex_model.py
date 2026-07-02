"""annex_model.py — structure a line-item *annex* PDF into a row/column model.

NEW module (engine untouched). This is **Layer 1** of the rules-based annex
automation: it turns the flat list of text spans (from the engine's extractor)
into a real table the app can reason about —

    sections  → the bold/italic group headings
    items     → each line item: label · unit · qty · unitPrice · amount,
                each carrying the span *ids* needed later to restamp or erase it
    total     → the "Total HT" cell

**Template-driven & page-aware.** All the layout geometry (which x-range each
column occupies, where the table/header bands are, the erase edges and the
width-relax floors) lives in a plain-dict *template*, not in code. That's what
makes "scan a customer's annex once → save the template → reuse it every month"
possible, and lets one annex span multiple pages. `DEFAULT_TEMPLATE` holds the
SWAM/INWI values; `detect_template()` infers a fresh template from any PDF.

Nothing here touches pdf_editor.py / the engine.
"""

import re

# ── number parsing/formatting (fr / Morocco: '.' groups, ',' decimals) ──────────

def parse_num(text: str):
    """'1.884,30' → 1884.3 · '6.281 ' → 6281.0 · '0,3' → 0.3 · '' → None."""
    t = (text or "").strip().replace(" ", " ").replace(" ", "")
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


# ── the template: every layout constant lives here, not in code ──────────────────
# A template describes ONE annex family's geometry. Column matchers test a span's
# x0 / x1 / xc (centre) against a [min,max] band; the numeric columns are right-
# aligned so they match on x1. `floor` (numeric cols) is a safe left edge the cell
# can be widened to when a longer number is stamped, sitting in the inter-column
# gap so the erase never touches a neighbour.
DEFAULT_TEMPLATE = {
    "id": "default-inwi",
    "name": "SWAM/INWI default",
    "numberFormat": "fr-MA",
    "rowTolerance": 6.0,
    "tableRegion":  {"yTop": 280.0, "yBottom": 700.0},   # per-page y band
    "headerRegion": {"yTop": 100.0, "yBottom": 280.0},
    "tableEdges":   {"x0": 23.0, "x1": 540.5},           # full-width band erase
    "totalFloor":   472.0,
    "columns": {
        "label":  {"by": "x0", "max": 300.0},
        "unit":   {"by": "xc", "min": 310.0, "max": 366.0},
        "qty":    {"by": "x1", "min": 372.0, "max": 412.0, "floor": 360.0},
        "price":  {"by": "x1", "min": 440.0, "max": 478.0},
        "amount": {"by": "x1", "min": 495.0, "max": 545.0, "floor": 478.0},
    },
    "headers": [
        {"key": "factureNo",  "label": "Facture N°",       "pattern": r"^(Annexe.*?N°\s*)(.+)$"},
        {"key": "clientName", "label": "Nom client",       "pattern": r"^(Nom\s+Client\s*:\s*)(.+)$"},
        {"key": "clientRef",  "label": "Référence client", "pattern": r"^(R[ée]f[ée]rence\s+Client\s*:\s*)(.*)$"},
        {"key": "ice",        "label": "ICE N°",           "pattern": r"^(ICE\s*N°\s*:?\s*)(.+)$"},
        # Période keeps no prefix — the whole "Du … au …" is the value.
        {"key": "periode",    "label": "Période",          "pattern": r"^()(Du\s+.+)$"},
    ],
}

_NUMERIC_COLS = ("unit", "qty", "price", "amount")


def _val(bbox, by: str) -> float:
    x0, _y0, x1, _y1 = bbox
    if by == "x0":
        return x0
    if by == "x1":
        return x1
    return (x0 + x1) / 2  # "xc"


def _column_of(bbox, columns: dict) -> str | None:
    """Which table column a span sits in, per the template's column bands."""
    for name in _NUMERIC_COLS:
        c = columns.get(name)
        if not c:
            continue
        v = _val(bbox, c.get("by", "x1"))
        if c.get("min", -1e18) < v < c.get("max", 1e18):
            return name
    c = columns.get("label")
    if c:
        v = _val(bbox, c.get("by", "x0"))
        if c.get("min", -1e18) < v < c.get("max", 1e18):
            return "label"
    return None


def _cy(span) -> float:
    b = span["bbox"]
    return (b[1] + b[3]) / 2


# ── header fields (label + value living in one span) ─────────────────────────────

def detect_headers(spans: list, template: dict | None = None) -> list:
    """Find the document-info fields (facture N°, client, ICE, période…) in the
    header band. Each is one span 'Label : value' — we keep the label (prefix)
    and expose the value so it can be swapped per client.
    """
    t = template or DEFAULT_TEMPLATE
    band = t.get("headerRegion", DEFAULT_TEMPLATE["headerRegion"])
    y_top, y_bot = band.get("yTop", -1e18), band.get("yBottom", 1e18)
    patterns = t.get("headers", DEFAULT_TEMPLATE["headers"])

    out, seen = [], set()
    for s in spans:
        y0 = s["bbox"][1]
        if not (y_top < y0 < y_bot):     # header band only (skip footer ICE)
            continue
        text = (s["text"] or "").rstrip()
        for h in patterns:
            key = h["key"]
            if key in seen:
                continue
            m = re.match(h["pattern"], text)
            if m:
                out.append({"key": key, "label": h.get("label", key),
                            "prefix": m.group(1),
                            "value": (m.group(2) or "").strip(),
                            "spanId": s["id"]})
                seen.add(key)
                break
    return out


# ── model builder (page-aware) ────────────────────────────────────────────────────

def build_model(spans: list, template: dict | None = None) -> dict:
    """spans: the /extract list (each with a stable integer `id` and a `page`).

    Returns {"items": [...], "sections": [...], "total": {...}|None, "headers":…}.
    Each PAGE is clustered into rows independently (so a multi-page annex works);
    sections and items accumulate across pages in reading order.
    """
    t = template or DEFAULT_TEMPLATE
    columns = t.get("columns", DEFAULT_TEMPLATE["columns"])
    reg = t.get("tableRegion", DEFAULT_TEMPLATE["tableRegion"])
    y_top = reg.get("yTop", -1e18)
    y_bot = reg.get("yBottom")
    if y_bot is None:
        y_bot = 1e18
    tol = t.get("rowTolerance", 6.0)

    # 1. Restrict to the table band, grouped by page.
    by_page: dict = {}
    for s in spans:
        y0 = s["bbox"][1]
        if y_top < y0 < y_bot:
            by_page.setdefault(s.get("page", 0), []).append(s)

    model = {"items": [], "sections": [], "total": None}
    current_section = None
    current_section_index = None   # which section the next item belongs under

    # 2. Process pages in order; cluster each page's spans into rows by centre-y.
    for page in sorted(by_page):
        rows: list = []
        for s in sorted(by_page[page], key=_cy):
            if rows and abs(_cy(s) - rows[-1]["cy"]) <= tol:
                rows[-1]["spans"].append(s)
            else:
                rows.append({"cy": _cy(s), "spans": [s]})

        for r in rows:
            cols: dict = {}
            for sp in r["spans"]:
                c = _column_of(sp["bbox"], columns)
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

    model["headers"] = detect_headers(spans, t)
    return model


# ── auto-detect a template from a PDF (the "scan") ────────────────────────────────

_NUM_TEXT = re.compile(r"[0-9]")


def _is_number(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and _NUM_TEXT.search(t) is not None and parse_num(t) is not None


def _cluster(values: list, tol: float) -> list:
    """Cluster 1-D values within *tol* → [{center,min,max,count}], sorted by center."""
    out: list = []
    for v in sorted(values):
        if out and v - out[-1]["_last"] <= tol:
            g = out[-1]
            g["_vals"].append(v)
            g["_last"] = v
        else:
            out.append({"_vals": [v], "_last": v})
    res = []
    for g in out:
        vs = g["_vals"]
        res.append({"center": sum(vs) / len(vs), "min": min(vs),
                    "max": max(vs), "count": len(vs)})
    return res


def detect_template(spans: list) -> dict:
    """Infer a template from a PDF's spans (best-effort; the user confirms it).

    Strategy: the numeric columns are right-aligned, so cluster the right edges
    (x1) of every numeric span. The recurring clusters (a value on many rows) are
    the qty / price / amount columns, right-to-left. From their vertical extent we
    place the table band; the unit column is the text column just left of qty; the
    label column is everything further left. Falls back to DEFAULT_TEMPLATE for any
    part it can't determine.
    """
    tmpl = {k: (v.copy() if isinstance(v, dict) else v)
            for k, v in DEFAULT_TEMPLATE.items()}
    tmpl["id"] = "scanned"
    tmpl["name"] = "Scanned annex"
    tmpl["columns"] = {}
    tmpl["headers"] = [dict(h) for h in DEFAULT_TEMPLATE["headers"]]

    numeric = [s for s in spans if _is_number(s["text"])]
    if len(numeric) < 3:
        return DEFAULT_TEMPLATE  # not table-like; nothing to detect

    # 1. Numeric columns = clusters of right edges that recur across rows.
    x1_clusters = _cluster([s["bbox"][2] for s in numeric], tol=14.0)
    max_count = max(c["count"] for c in x1_clusters)
    cols_by_x = [c for c in x1_clusters if c["count"] >= max(2, max_count * 0.3)]
    cols_by_x.sort(key=lambda c: c["center"])
    if not cols_by_x:
        return DEFAULT_TEMPLATE

    # rightmost = amount, then price, then qty (right-to-left)
    names = ["amount", "price", "qty"]
    assigned: dict = {}
    for name, cluster in zip(names, reversed(cols_by_x)):
        assigned[name] = cluster

    columns: dict = {}
    for name, c in assigned.items():
        columns[name] = {"by": "x1", "min": c["min"] - 6.0, "max": c["max"] + 6.0}

    # 2. Spans belonging to the amount column anchor the table's vertical band.
    def in_amount(s):
        a = assigned["amount"]
        return a["min"] - 8 <= s["bbox"][2] <= a["max"] + 8 and _is_number(s["text"])
    amount_spans = [s for s in numeric if in_amount(s)]
    if not amount_spans:
        return DEFAULT_TEMPLATE
    item_top = min(s["bbox"][1] for s in amount_spans)
    item_bot = max(s["bbox"][3] for s in amount_spans)

    # yTop: just below the column-header label sitting in the amount column
    # (e.g. "Montant"), else just above the first numeric value.
    a = assigned["amount"]
    amount_band = (a["min"] - 10, a["max"] + 10)
    hdr_labels = [s for s in spans
                  if amount_band[0] <= s["bbox"][2] <= amount_band[1]
                  and not _is_number(s["text"])
                  and s["bbox"][3] <= item_top + 2]
    y_top = (max(s["bbox"][3] for s in hdr_labels) + 2.0) if hdr_labels else item_top - 2.0
    y_bottom = item_bot + 25.0
    tmpl["tableRegion"] = {"yTop": y_top, "yBottom": y_bottom}
    tmpl["headerRegion"] = {"yTop": 90.0, "yBottom": y_top}

    # 3. Body spans (inside the table band, any page) → place label/unit + edges.
    body = [s for s in spans if y_top < s["bbox"][1] < y_bottom]
    qty_left = assigned["qty"]["min"] if "qty" in assigned else assigned["amount"]["min"]

    # Unit: short non-numeric text whose centre sits left of qty but right of the
    # bulk of the labels. Cluster their centres; keep it only if it's a real column.
    unit_spans = [s for s in body
                  if not _is_number(s["text"])
                  and (s["bbox"][0] + s["bbox"][2]) / 2 < qty_left - 8
                  and len((s["text"] or "").strip()) <= 6
                  and (s["bbox"][2] - s["bbox"][0]) < 40]
    label_max = qty_left - 20.0
    if unit_spans:
        uc = _cluster([(s["bbox"][0] + s["bbox"][2]) / 2 for s in unit_spans], tol=18.0)
        uc = [c for c in uc if c["count"] >= 2]
        if uc:
            best = max(uc, key=lambda c: c["count"])
            columns["unit"] = {"by": "xc", "min": best["min"] - 10.0,
                               "max": best["max"] + 10.0}
            label_max = best["min"] - 12.0
    columns["label"] = {"by": "x0", "max": label_max}
    tmpl["columns"] = columns

    # 4. Width-relax floors: a numeric cell may widen LEFT into the gap up to just
    # past the previous column's right edge (never onto it). Compute each column's
    # real right/left extent by assigning body spans to columns.
    right_edge: dict = {}
    left_start: dict = {}
    for s in body:
        c = _column_of(s["bbox"], columns)
        if c:
            right_edge[c] = max(right_edge.get(c, -1e18), s["bbox"][2])
            left_start[c] = min(left_start.get(c, 1e18), s["bbox"][0])

    def floor_for(col: str, neighbor: str) -> float:
        ne = right_edge.get(neighbor)
        if ne is not None:
            return ne + 4.0                       # sit in the gap, 4pt clearance
        ls = left_start.get(col)
        return (ls - 30.0) if ls is not None else (columns[col]["min"] - 45.0)

    if "qty" in columns:
        columns["qty"]["floor"] = floor_for("qty",
                                            "unit" if "unit" in columns else "label")
    if "amount" in columns:
        columns["amount"]["floor"] = floor_for("amount",
                                               "price" if "price" in columns else "qty")
    tmpl["totalFloor"] = columns.get("amount", {}).get(
        "floor", assigned["amount"]["min"] - 22.0) - 6.0

    # Table edges for full-width band erases = the horizontal extent of the body.
    if body:
        tmpl["tableEdges"] = {
            "x0": min(s["bbox"][0] for s in body) - 3.0,
            "x1": max(s["bbox"][2] for s in body) + 3.0,
        }
    return tmpl


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
