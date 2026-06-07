"""
app.py — Redraft
Modern SaaS UI (dark sidebar nav + white-card content) over the existing,
unchanged backend (pdf_editor.py / pdf_extractor.py / merge_engine.py).

Three pages, switched from a dark left sidebar:
  1. PDF Editor     — click any text field on the rendered PDF → edit → download
  2. Bulk Generator — upload template + CSV → map fields → generate ZIP
  3. Dashboard      — usage stats + recent activity

Design language (from the Stitch "Redraft Pro" design system):
  • Sidebar          #1a1a2e (deep navy), light text, indigo active accent
  • Content surface   soft off-white #f7f8fa, clean white cards
  • Primary accent    indigo #4f46e5
  • Status pills      green = match · amber = substitute · red = fallback
"""

import base64
import csv
import io
import os
import sys
import tempfile
import time
import warnings
import zipfile
from pathlib import Path

import fitz
import streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

sys.path.insert(0, os.path.dirname(__file__))
from pdf_editor import PDFEditor, get_spans  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Redraft",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Design tokens (kept in one place so the CSS + Python stay in sync)
NAVY    = "#1a1a2e"
NAVY_2  = "#23233f"
INDIGO  = "#4f46e5"
SURFACE = "#f7f8fa"
BORDER  = "#e5e7eb"
GREEN   = "#16a34a"
AMBER   = "#d97706"
RED     = "#dc2626"


# ─────────────────────────────────────────────────────────────────────────────
# Global CSS — turns vanilla Streamlit into the Notion/Linear/Vercel look
# ─────────────────────────────────────────────────────────────────────────────
def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"], .stApp {{
            font-family: 'Inter', -apple-system, sans-serif;
        }}

        /* ---- Main content surface ---- */
        .stApp {{ background: {SURFACE}; }}
        .block-container {{ padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1400px; }}

        /* ---- Sidebar: deep navy ---- */
        [data-testid="stSidebar"] {{
            background: {NAVY};
            border-right: 1px solid rgba(255,255,255,.06);
        }}
        [data-testid="stSidebar"] * {{ color: #c7c9d9; }}
        [data-testid="stSidebar"] .stButton > button {{
            width: 100%;
            text-align: left;
            justify-content: flex-start;
            background: transparent;
            color: #c7c9d9;
            border: none;
            border-radius: 8px;
            padding: .6rem .8rem;
            font-weight: 500;
            font-size: .95rem;
            margin: 2px 0;
            transition: background .12s ease, color .12s ease;
        }}
        [data-testid="stSidebar"] .stButton > button:hover {{
            background: {NAVY_2};
            color: #ffffff;
        }}
        /* active nav item (primary-styled button) */
        [data-testid="stSidebar"] .stButton > button[kind="primary"] {{
            background: {INDIGO};
            color: #ffffff;
            box-shadow: 0 1px 8px rgba(79,70,229,.45);
        }}
        [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {{
            background: #4338ca;
        }}

        /* ---- Cards: bordered containers ---- */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            background: #ffffff;
            border: 1px solid {BORDER} !important;
            border-radius: 12px;
            box-shadow: 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.04);
        }}

        /* ---- Headings ---- */
        h1, h2, h3 {{ color: #111827; font-weight: 700; letter-spacing: -.01em; }}

        /* ---- Primary buttons in main area: indigo ---- */
        .stApp [data-testid="stMain"] .stButton > button[kind="primary"],
        .stApp [data-testid="stMain"] [data-testid="stDownloadButton"] > button {{
            background: {INDIGO};
            border: 1px solid {INDIGO};
            color: #fff;
            border-radius: 8px;
            font-weight: 600;
        }}
        .stApp [data-testid="stMain"] .stButton > button[kind="primary"]:hover,
        .stApp [data-testid="stMain"] [data-testid="stDownloadButton"] > button:hover {{
            background: #4338ca; border-color: #4338ca;
        }}
        .stApp [data-testid="stMain"] .stButton > button[kind="secondary"] {{
            background: #fff;
            border: 1px solid {BORDER};
            color: #374151;
            border-radius: 8px;
            font-weight: 600;
        }}
        .stApp [data-testid="stMain"] .stButton > button[kind="secondary"]:hover {{
            border-color: #c7cad1; color: #111827;
        }}

        /* ---- File uploader → big dashed dropzone ---- */
        [data-testid="stFileUploaderDropzone"] {{
            background: #fbfbfd;
            border: 2px dashed #cdd2dc;
            border-radius: 12px;
            padding: 2rem 1rem;
            min-height: 150px;
        }}
        [data-testid="stFileUploaderDropzone"]:hover {{
            border-color: {INDIGO};
            background: #f5f5ff;
        }}

        /* ---- Misc ---- */
        [data-testid="stMetric"] {{
            background: #fff; border: 1px solid {BORDER};
            border-radius: 12px; padding: 1.1rem 1.2rem;
            box-shadow: 0 1px 2px rgba(16,24,40,.04);
        }}
        .rd-pill {{
            display:inline-block; padding:2px 10px; border-radius:999px;
            font-size:.74rem; font-weight:600; line-height:1.5;
        }}
        .rd-green  {{ background:#dcfce7; color:{GREEN}; }}
        .rd-amber  {{ background:#fef3c7; color:{AMBER}; }}
        .rd-red    {{ background:#fee2e2; color:{RED}; }}
        .rd-edit-row {{
            display:flex; align-items:center; gap:.5rem; padding:.45rem .6rem;
            border:1px solid {BORDER}; border-radius:8px; margin-bottom:.4rem;
            background:#fcfcfd; font-size:.86rem;
        }}
        .rd-old {{ color:#9ca3af; text-decoration:line-through; }}
        .rd-new {{ color:{GREEN}; font-weight:600; }}
        .rd-muted {{ color:#6b7280; }}
        .rd-act {{
            display:flex; align-items:center; gap:.6rem; padding:.55rem .2rem;
            border-bottom:1px solid {BORDER}; font-size:.9rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Usage tracking (Dashboard) — lives in session_state, no persistence needed
# ─────────────────────────────────────────────────────────────────────────────
def _stats() -> dict:
    return st.session_state.setdefault(
        "stats",
        {"edited": 0, "generated": 0, "fonts": 0, "activity": []},
    )


def log_activity(icon: str, text: str) -> None:
    s = _stats()
    s["activity"].insert(0, {"icon": icon, "text": text,
                             "t": time.strftime("%H:%M:%S")})
    s["activity"] = s["activity"][:12]


# ─────────────────────────────────────────────────────────────────────────────
# Rendering helpers (pure Streamlit — no custom JS)  [verified-working logic]
# ─────────────────────────────────────────────────────────────────────────────

DPI   = 150
SCALE = DPI / 72.0   # PDF points → image pixels at DPI


def render_page_b64(pdf_bytes: bytes, page_num: int = 0) -> tuple:
    """Render *page_num* at DPI, return (base64_png_str, img_w_px, img_h_px)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    mat = fitz.Matrix(SCALE, SCALE)
    pix = doc[page_num].get_pixmap(matrix=mat)
    doc.close()
    b64 = base64.b64encode(pix.tobytes("png")).decode()
    return b64, pix.width, pix.height


def render_base_png_cached(display_bytes: bytes, page_num: int) -> bytes:
    """Render *page_num* (NO overlays) to PNG once and cache it in session_state.

    Rasterising the PDF is the slow part of each click rerun; highlight outlines
    are cheap PIL draws done on top of this cached base.  The cache is keyed by a
    light signature (page + length + edge bytes) so it auto-invalidates when the
    displayed PDF changes (e.g. switching to the edited preview).
    """
    sig = (page_num, len(display_bytes), display_bytes[:24], display_bytes[-24:])
    cache = st.session_state.get("ed_png_cache")
    if cache and cache.get("sig") == sig:
        return cache["png"]
    doc = fitz.open(stream=display_bytes, filetype="pdf")
    pix = doc[page_num].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    png = pix.tobytes("png")
    doc.close()
    st.session_state["ed_png_cache"] = {"sig": sig, "png": png}
    return png


def overlay_highlights(base_png: bytes, spans: list, page_num: int,
                       selected=None, edited_idxs=()) -> Image.Image:
    """Draw edited (green) and selected (blue) outline boxes onto the base image.

    Pure PIL — no PDF re-render.  bbox is in PDF points; multiply by SCALE to get
    pixel coordinates in the DPI-rendered image.
    """
    img  = Image.open(io.BytesIO(base_png)).convert("RGB")
    draw = ImageDraw.Draw(img)

    def _px(b):
        return [b[0] * SCALE, b[1] * SCALE, b[2] * SCALE, b[3] * SCALE]

    for i in edited_idxs:
        if spans[i]["page"] == page_num:
            draw.rectangle(_px(spans[i]["bbox"]), outline=(34, 197, 94), width=2)
    if selected is not None and spans[selected]["page"] == page_num:
        draw.rectangle(_px(spans[selected]["bbox"]), outline=(79, 70, 229), width=3)
    return img


def _pos_hint(bbox: list, pw: float, ph: float) -> str:
    """Human-readable position of *bbox* on a page of size (pw, ph) in PDF pts."""
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    col = "left"   if cx < pw / 3 else ("right"  if cx > 2 * pw / 3 else "center")
    row = "top"    if cy < ph / 3 else ("bottom" if cy > 2 * ph / 3 else "middle")
    return f"{row}-{col}"


def _span_at(spans: list, page_num: int, px: float, py: float):
    """Return the global index of the span whose bbox contains PDF point (px, py).

    When several bboxes overlap the point, the smallest-area one wins (the most
    specific target).  A small padding makes thin text lines easier to hit.
    Returns None if the click landed on empty space.
    """
    PAD = 1.5  # pt — forgiveness for thin glyph rows
    best, best_area = None, None
    for i, s in enumerate(spans):
        if s["page"] != page_num:
            continue
        x0, y0, x1, y1 = s["bbox"]
        if (x0 - PAD) <= px <= (x1 + PAD) and (y0 - PAD) <= py <= (y1 + PAD):
            area = max(x1 - x0, 1) * max(y1 - y0, 1)
            if best_area is None or area < best_area:
                best, best_area = i, area
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Shared utilities  [unchanged backend glue]
# ─────────────────────────────────────────────────────────────────────────────

class _TmpPDF:
    def __init__(self, data: bytes):
        self._data = data
        self._path = None

    def __enter__(self) -> str:
        fd, self._path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(self._path, "wb") as f:
            f.write(self._data)
        return self._path

    def __exit__(self, *_):
        if self._path and os.path.exists(self._path):
            try: os.unlink(self._path)
            except OSError: pass


class RedraftError(Exception):
    """User-facing error — message is safe to show, never a raw traceback."""


def extract_spans(pdf_bytes: bytes) -> list:
    """Extract all spans from all pages as plain serialisable dicts."""
    result = []
    with _TmpPDF(pdf_bytes) as path:
        doc = fitz.open(path)
        for pn in range(len(doc)):
            for span in get_spans(doc, pn):
                result.append({
                    "page":   pn,
                    "text":   span["text"],
                    "font":   span["font"],
                    "size":   round(span["size"], 1),
                    "color":  list(span["color"]),
                    "flags":  span["flags"],
                    "bbox":   list(span["bbox"]),     # [x0,y0,x1,y1] in PDF pts
                    "origin": list(span["origin"]),
                })
        doc.close()
    return result


def apply_replacements(pdf_bytes: bytes, replacements: list) -> bytes:
    """Apply [(span_dict, new_text), …] to *pdf_bytes*, return new PDF bytes.

    Side effect: stores a per-font resolution report in
    ``st.session_state['font_report']`` so the UI can show where each font was
    resolved from (system / Google Fonts / substitute / builtin fallback) and
    surface any warnings — instead of silently swallowing them.
    """
    from pdf_editor import font_source  # local import: avoids touching module top

    with _TmpPDF(pdf_bytes) as in_path:
        fd, out_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ed = PDFEditor(in_path)

                by_page: dict = {}
                for sd, new_text in replacements:
                    by_page.setdefault(sd["page"], []).append((sd, new_text))

                for pn, items in by_page.items():
                    pairs = [({
                        "text":   sd["text"],
                        "bbox":   fitz.Rect(sd["bbox"]),
                        "origin": tuple(sd["origin"]),
                        "font":   sd["font"],
                        "size":   sd["size"],
                        "color":  tuple(sd["color"]),
                        "flags":  sd["flags"],
                    }, nt) for sd, nt in items]
                    ed.replace_all(pairs, page_num=pn)

                ed.save(out_path)

            # Build the font report: one row per distinct original font used.
            fonts_used = {sd["font"] for sd, _ in replacements}
            report = []
            for fn in sorted(fonts_used):
                src = font_source(fn)
                if (src.startswith("system:") or src.startswith("google")
                        or src.startswith("builtin:")):
                    status = "match"
                elif src.startswith("substitute"):
                    status = "substitute"
                elif src.startswith("BUILTIN") or src.startswith("SUBSET"):
                    status = "fallback"
                else:
                    status = "unknown"
                report.append({"font": fn, "status": status, "source": src})
            st.session_state["font_report"] = {
                "fonts": report,
                "warnings": [str(w.message) for w in caught],
            }

            with open(out_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(out_path):
                try: os.unlink(out_path)
                except OSError: pass


def safe_extract_spans(name: str, pdf_bytes: bytes) -> list:
    """extract_spans wrapped to raise a clean RedraftError on any failure."""
    try:
        return extract_spans(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        raise RedraftError(
            f"Couldn't read “{name}”. It may be corrupt, encrypted, or not a "
            f"valid PDF. (details: {type(exc).__name__})"
        ) from exc


def safe_apply(pdf_bytes: bytes, replacements: list) -> bytes:
    """apply_replacements wrapped to raise a clean RedraftError on failure."""
    try:
        return apply_replacements(pdf_bytes, replacements)
    except Exception as exc:  # noqa: BLE001
        raise RedraftError(
            f"Couldn't apply your edits to the PDF. "
            f"(details: {type(exc).__name__}: {exc})"
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Small UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f"<h2 style='margin-bottom:.1rem'>{title}</h2>"
        f"<p class='rd-muted' style='margin-top:0;font-size:.98rem'>{subtitle}</p>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 1 — PDF Editor
# ─────────────────────────────────────────────────────────────────────────────

def _editor_reset(name: str, pdf_bytes: bytes, spans: list):
    st.session_state.update({
        "ed_name":       name,
        "ed_bytes":      pdf_bytes,
        "ed_spans":      spans,
        "ed_page":       0,
        "ed_edits":      {},      # {span_idx: new_text}
        "ed_result":     None,    # bytes of last previewed PDF
        "ed_selected":   None,    # global index of currently selected span
        "ed_click_time": None,    # unix_time of last handled image click
        "ed_png_cache":  None,    # cached base page render (no overlays)
    })


def page_editor():
    page_header("PDF Editor", "Click any field on your PDF to edit it.")

    # ── Upload zone ───────────────────────────────────────────────────────────
    with st.container(border=True):
        uploaded = st.file_uploader(
            "Drag & drop your PDF here, or click to browse",
            type=["pdf"], key="editor_upload",
        )

    if uploaded is None:
        st.info("Upload a PDF above to start editing.")
        return

    if st.session_state.get("ed_name") != uploaded.name:
        with st.spinner("Reading PDF…"):
            raw = uploaded.getvalue()
            try:
                spans = safe_extract_spans(uploaded.name, raw)
            except RedraftError as e:
                st.error(f"⚠️ {e}")
                return
        _editor_reset(uploaded.name, raw, spans)
        log_activity("✏️", f"Opened {uploaded.name} in the editor")

    pdf_bytes   = st.session_state["ed_bytes"]
    spans       = st.session_state["ed_spans"]
    edits: dict = st.session_state.get("ed_edits", {})

    if not spans:
        st.warning("No editable text was found in this PDF.")
        return

    # Page dimensions / count
    doc      = fitz.open(stream=pdf_bytes, filetype="pdf")
    n_pages  = len(doc)
    page_num = min(st.session_state.get("ed_page", 0), n_pages - 1)
    pw, ph   = doc[page_num].rect.width, doc[page_num].rect.height
    doc.close()

    page_idxs = [i for i, s in enumerate(spans) if s["page"] == page_num]

    # ── Resolve a fresh image click into the selected span ────────────────────
    click_key = f"ed_click_{page_num}"
    click = st.session_state.get(click_key)
    if click and click.get("unix_time") != st.session_state.get("ed_click_time"):
        st.session_state["ed_click_time"] = click["unix_time"]
        cx_px, cy_px = click["x"], click["y"]
        # Image shown at natural render size ⇒ displayed px == render px, and the
        # render scale is exactly DPI/72 px per PDF point ⇒ pdf_pt = pixel / SCALE.
        nat_w = pw * SCALE
        disp_w = click.get("width") or nat_w
        sx = SCALE if abs(disp_w - nat_w) < 1.0 else disp_w / pw
        sy = sx
        pdf_x = cx_px / sx
        pdf_y = cy_px / sy
        hit = _span_at(spans, page_num, pdf_x, pdf_y)
        _hit_txt = spans[hit]["text"][:30] if hit is not None else "<none>"
        print(f"[click] px=({cx_px:.0f},{cy_px:.0f}) disp_w={disp_w:.0f} "
              f"scale={sx:.4f}px/pt (DPI/72={SCALE:.4f}) → "
              f"pdf=({pdf_x:.1f},{pdf_y:.1f}) → span #{hit}: {_hit_txt!r}",
              file=sys.stderr, flush=True)
        if hit is not None:
            st.session_state["ed_selected"] = hit

    # Current selection (default: first field on the page)
    selected = st.session_state.get("ed_selected")
    if selected not in page_idxs:
        selected = page_idxs[0] if page_idxs else None
        st.session_state["ed_selected"] = selected

    col_img, col_panel = st.columns([3, 2], gap="large")

    # ── Left: clickable page image ────────────────────────────────────────────
    with col_img:
        with st.container(border=True):
            result_bytes  = st.session_state.get("ed_result")
            display_bytes = result_bytes if result_bytes else pdf_bytes

            top_l, top_r = st.columns([3, 1])
            with top_l:
                if result_bytes:
                    st.caption("📋 Edited preview · click any field to keep editing")
                else:
                    st.caption("🖱️ Click any text on the page to select it")
            with top_r:
                if n_pages > 1:
                    page_num = st.selectbox(
                        "Page", list(range(n_pages)), index=page_num,
                        format_func=lambda x: f"Page {x + 1}/{n_pages}",
                        key="ed_page_sel", label_visibility="collapsed",
                    )
                    st.session_state["ed_page"] = page_num

            base_png = render_base_png_cached(display_bytes, page_num)
            img = overlay_highlights(
                base_png, spans, page_num,
                selected=selected, edited_idxs=list(edits.keys()),
            )
            # Natural render size ⇒ exact DPI/72 click→PDF mapping.
            streamlit_image_coordinates(
                img, width=int(round(pw * SCALE)), key=click_key,
            )

    # ── Right: selected field + edit controls ─────────────────────────────────
    with col_panel:
        if selected is None:
            st.info("No editable text found on this page.")
            return

        with st.container(border=True):
            st.markdown("#### Selected field")
            sp = spans[selected]
            font_label = sp["font"].split("+")[-1]
            st.markdown(
                f"<span class='rd-muted' style='font-size:.85rem'>"
                f"{font_label} · {sp['size']:.0f} pt · "
                f"{_pos_hint(sp['bbox'], pw, ph)}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='rd-muted' style='font-size:.8rem;margin:.4rem 0 .1rem'>"
                f"ORIGINAL</div><div style='font-family:monospace;background:#f3f4f6;"
                f"padding:.4rem .6rem;border-radius:6px;font-size:.85rem'>"
                f"{(sp['text'][:80] or '∅')}</div>",
                unsafe_allow_html=True,
            )

            # …or pick from list (keeps clicks + list in sync)
            def _label(i: int) -> str:
                s    = spans[i]
                mark = "🟩 " if i in edits else ""
                shown = edits.get(i, s["text"])
                txt  = (shown[:34] + "…") if len(shown) > 34 else shown
                return f"{mark}{txt}  —  {_pos_hint(s['bbox'], pw, ph)}"

            sel_pos = page_idxs.index(selected) if selected in page_idxs else 0
            picked = st.selectbox(
                "Or pick a field", page_idxs, index=sel_pos, format_func=_label,
            )
            if picked != selected:
                selected = picked
                st.session_state["ed_selected"] = selected
                sp = spans[selected]

            current = edits.get(selected, sp["text"])
            new_val = st.text_input(
                "Replacement text", value=current,
                key=f"ed_input_{selected}", placeholder="Type replacement…",
            )

            b1, b2 = st.columns(2)
            with b1:
                if st.button("Apply", type="primary", width="stretch"):
                    if new_val.strip() and new_val != sp["text"]:
                        edits[selected] = new_val
                    else:
                        edits.pop(selected, None)
                    st.session_state["ed_edits"]  = edits
                    st.session_state["ed_result"] = None   # preview now stale
                    st.rerun()
            with b2:
                if st.button("Revert", width="stretch",
                             disabled=selected not in edits):
                    edits.pop(selected, None)
                    st.session_state["ed_edits"]  = edits
                    st.session_state["ed_result"] = None
                    st.rerun()

        # ── Edited fields list ────────────────────────────────────────────────
        n_edits = len(edits)
        with st.container(border=True):
            st.markdown(f"#### Edited fields ({n_edits})")
            if n_edits == 0:
                st.caption("No edits yet — click a field and apply a change.")
            else:
                for idx, nv in list(edits.items()):
                    o = (spans[idx]["text"][:26] or "∅")
                    st.markdown(
                        f"<div class='rd-edit-row'>"
                        f"<span class='rd-old'>{o}</span>"
                        f"<span class='rd-muted'>→</span>"
                        f"<span class='rd-new'>{nv[:26]}</span></div>",
                        unsafe_allow_html=True,
                    )

        # ── Actions ───────────────────────────────────────────────────────────
        with st.container(border=True):
            a1, a2 = st.columns(2)
            with a1:
                if st.button("🔍 Preview", type="primary",
                             width="stretch", disabled=n_edits == 0):
                    reps = [(spans[i], v) for i, v in edits.items()]
                    with st.spinner("Applying edits…"):
                        try:
                            st.session_state["ed_result"] = safe_apply(pdf_bytes, reps)
                            _stats()["edited"] += 1
                            _bump_fonts_resolved()
                            log_activity("🔍", f"Previewed {n_edits} edit(s) "
                                               f"in {st.session_state['ed_name']}")
                        except RedraftError as e:
                            st.error(f"⚠️ {e}")
                    st.rerun()
            with a2:
                if st.button("↺ Reset all", width="stretch"):
                    _editor_reset(st.session_state["ed_name"], pdf_bytes, spans)
                    st.rerun()

            result_bytes = st.session_state.get("ed_result")
            if result_bytes:
                if st.download_button(
                    "⬇ Download edited PDF", data=result_bytes,
                    file_name=f"edited_{Path(st.session_state['ed_name']).stem}.pdf",
                    mime="application/pdf", width="stretch", type="primary",
                ):
                    log_activity("⬇️", f"Downloaded edited "
                                       f"{st.session_state['ed_name']}")
            elif n_edits:
                st.caption("Click **Preview** to prepare the download.")

        # ── Font warnings (collapsible) ───────────────────────────────────────
        report = st.session_state.get("font_report")
        if st.session_state.get("ed_result") and report and report["fonts"]:
            n_bad = sum(1 for r in report["fonts"]
                        if r["status"] in ("substitute", "fallback"))
            label = ("Font warnings — all matched ✓" if n_bad == 0
                     else f"Font warnings — {n_bad} substituted/fallback")
            with st.expander(label, expanded=n_bad > 0):
                for r in report["fonts"]:
                    nm = r["font"].split("+")[-1]
                    if r["status"] == "match":
                        pill = f"<span class='rd-pill rd-green'>match</span>"
                        note = "exact font"
                    elif r["status"] == "substitute":
                        pill = f"<span class='rd-pill rd-amber'>substitute</span>"
                        note = r["source"]
                    elif r["status"] == "fallback":
                        pill = f"<span class='rd-pill rd-red'>fallback</span>"
                        note = "no full font found; may render differently"
                    else:
                        pill = f"<span class='rd-pill'>—</span>"
                        note = r["source"]
                    st.markdown(
                        f"<div style='margin:.25rem 0'>{pill} "
                        f"<b>{nm}</b> <span class='rd-muted'>· {note}</span></div>",
                        unsafe_allow_html=True,
                    )


def _bump_fonts_resolved():
    """Count distinct fonts that resolved to a real (non-fallback) font."""
    report = st.session_state.get("font_report")
    if not report:
        return
    n = sum(1 for r in report["fonts"] if r["status"] in ("match", "substitute"))
    _stats()["fonts"] += n


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 2 — Bulk Generator
# ─────────────────────────────────────────────────────────────────────────────

def _bulk_reset(name: str, pdf_bytes: bytes, spans: list):
    st.session_state["bk_name"]  = name
    st.session_state["bk_bytes"] = pdf_bytes
    st.session_state["bk_spans"] = spans
    st.session_state["bk_zip"]   = None


def parse_table(name: str, data: bytes) -> tuple:
    """Parse CSV or Excel → (headers, list-of-row-dicts). Raises RedraftError."""
    ext = Path(name).suffix.lower()
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa: F401
            import pandas as pd
            df = pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
            return list(df.columns), df.to_dict("records")
        except ImportError:
            raise RedraftError(
                "Excel support needs the openpyxl/pandas packages. "
                "Please upload a CSV instead."
            )
        except Exception as exc:  # noqa: BLE001
            raise RedraftError(f"Couldn't read the Excel file. ({type(exc).__name__})")
    # CSV
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text   = data.decode(enc)
            reader = csv.DictReader(io.StringIO(text))
            rows   = [dict(r) for r in reader]
            hdrs   = list(reader.fieldnames or [])
            if hdrs:
                return hdrs, rows
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise RedraftError(f"Couldn't parse the CSV file. ({type(exc).__name__})")
    raise RedraftError("The data file appears to be empty or has no header row.")


def page_bulk():
    page_header("Bulk Generator",
                "Generate one PDF per data row from a template and a spreadsheet.")

    # ── Step 1 + 2: uploads side by side ──────────────────────────────────────
    c1, c2 = st.columns(2, gap="large")
    with c1:
        with st.container(border=True):
            st.markdown("#### Step 1 · Template PDF")
            tmpl = st.file_uploader("Drop a PDF template", type=["pdf"], key="bk_tmpl")
            if tmpl is not None:
                if st.session_state.get("bk_name") != tmpl.name:
                    with st.spinner("Reading template…"):
                        try:
                            spans = safe_extract_spans(tmpl.name, tmpl.getvalue())
                        except RedraftError as e:
                            st.error(f"⚠️ {e}")
                            return
                    _bulk_reset(tmpl.name, tmpl.getvalue(), spans)
                    log_activity("📄", f"Loaded template {tmpl.name}")
                try:
                    thumb = render_page_b64(st.session_state["bk_bytes"], 0)[0]
                    st.image(thumb, caption=f"{tmpl.name} · page 1", width=240)
                except Exception:
                    st.caption("Preview unavailable.")

    with c2:
        with st.container(border=True):
            st.markdown("#### Step 2 · Data file (CSV or Excel)")
            data_file = st.file_uploader("Drop a CSV / Excel file",
                                         type=["csv", "xlsx", "xls"], key="bk_csv")
            headers, all_rows = [], []
            if data_file is not None:
                try:
                    headers, all_rows = parse_table(data_file.name, data_file.getvalue())
                except RedraftError as e:
                    st.error(f"⚠️ {e}")
                    return
                st.caption(f"**{len(all_rows)} rows · {len(headers)} columns** — "
                           f"first 3 rows:")
                st.dataframe(all_rows[:3], width="stretch", hide_index=True)

    if tmpl is None:
        st.info("Upload a template PDF to begin.")
        return

    pdf_bytes: bytes = st.session_state["bk_bytes"]
    spans: list      = st.session_state["bk_spans"]

    # ── Step 3: field mapping ─────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("#### Step 3 · Map fields → columns")
        st.caption("For each text field, choose the data column that replaces it. "
                   "Leave **— skip —** to keep the original text.")

        col_opts = ["— skip —"] + headers
        h1, h2 = st.columns([3, 2])
        h1.markdown("<span class='rd-muted' style='font-size:.78rem'>"
                    "PDF FIELD</span>", unsafe_allow_html=True)
        h2.markdown("<span class='rd-muted' style='font-size:.78rem'>"
                    "MAPS TO COLUMN</span>", unsafe_allow_html=True)

        rows_ui = []
        for i, span in enumerate(spans):
            cc1, cc2 = st.columns([3, 2])
            with cc1:
                st.markdown(
                    f"<div style='padding:6px 0;font-size:.9em'>"
                    f"<b>{(span['text'][:48] or '∅')}</b><br>"
                    f"<span class='rd-muted' style='font-size:.78em'>"
                    f"{span['font'].split('+')[-1]} · {span['size']}pt · "
                    f"p{span['page']+1}</span></div>",
                    unsafe_allow_html=True,
                )
            with cc2:
                sel = st.selectbox("col", col_opts, key=f"bk_map_{i}",
                                   label_visibility="collapsed",
                                   disabled=not headers)
            rows_ui.append((i, sel))

        mapping = {i: col for i, col in rows_ui if col != "— skip —"}

    # ── Step 4: preview row 1 ─────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("#### Step 4 · Preview (row 1)")
        if mapping and all_rows:
            first = all_rows[0]
            reps  = [(spans[i], str(first.get(col, "")))
                     for i, col in mapping.items() if str(first.get(col, ""))]
            if reps:
                with st.spinner("Rendering preview…"):
                    try:
                        prev = safe_apply(pdf_bytes, reps)
                        st.image(render_page_b64(prev, 0)[0],
                                 caption="Row 1 preview", width=300)
                    except RedraftError as e:
                        st.error(f"⚠️ {e}")
            else:
                st.caption("Row 1 has no values for the mapped columns.")
        else:
            st.caption("Map at least one field and upload data to see a preview.")

    # ── Step 5: generate ──────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("#### Step 5 · Generate")
        if not mapping:
            st.warning("Map at least one field to a column.")
            return
        if not all_rows:
            st.warning("Upload a data file with rows.")
            return

        n_rows   = len(all_rows)
        n_mapped = len(mapping)
        st.info(f"**{n_rows}** PDF(s) will be generated · "
                f"**{n_mapped}** field(s) mapped.")

        if st.button("⚡ Generate all PDFs", type="primary"):
            progress = st.progress(0.0, text="Generating…")
            status   = st.empty()
            zip_buf  = io.BytesIO()
            failed   = 0
            try:
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for row_idx, row in enumerate(all_rows):
                        reps = [(spans[i], str(row.get(col, "")))
                                for i, col in mapping.items() if str(row.get(col, ""))]
                        try:
                            pdf_out = apply_replacements(pdf_bytes, reps)
                            zf.writestr(f"row_{row_idx + 1:04d}.pdf", pdf_out)
                        except Exception:  # noqa: BLE001 — skip the bad row, keep going
                            failed += 1
                        pct = (row_idx + 1) / n_rows
                        progress.progress(pct, text=f"Row {row_idx + 1} / {n_rows}")
                        status.caption(f"Generated row_{row_idx + 1:04d}.pdf")
            except Exception as exc:  # noqa: BLE001
                st.error(f"⚠️ Generation failed: {type(exc).__name__}: {exc}")
                return

            progress.progress(1.0, text="Done!")
            status.empty()
            st.session_state["bk_zip"] = zip_buf.getvalue()
            ok = n_rows - failed
            _stats()["generated"] += ok
            log_activity("⚡", f"Generated {ok} PDF(s) from {st.session_state['bk_name']}")
            if failed:
                st.warning(f"✅ {ok} PDFs generated · {failed} row(s) skipped (errors).")
            else:
                st.success(f"✅ {ok} PDFs generated.")
            st.rerun()

        bk_zip = st.session_state.get("bk_zip")
        if bk_zip:
            if st.download_button(
                f"⬇ Download ZIP ({n_rows} PDFs)", data=bk_zip,
                file_name=f"{Path(st.session_state['bk_name']).stem}_bulk.zip",
                mime="application/zip", type="primary",
            ):
                log_activity("⬇️", "Downloaded bulk ZIP")


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 3 — Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def page_dashboard():
    page_header("Dashboard", "Your Redraft activity at a glance.")
    s = _stats()

    m1, m2, m3 = st.columns(3, gap="large")
    m1.metric("PDFs edited", s["edited"])
    m2.metric("PDFs generated", s["generated"])
    m3.metric("Fonts resolved", s["fonts"])

    st.write("")
    with st.container(border=True):
        st.markdown("#### Recent activity")
        if not s["activity"]:
            st.caption("Nothing yet — edit or generate a PDF and it'll show up here.")
        else:
            for a in s["activity"]:
                st.markdown(
                    f"<div class='rd-act'><span style='font-size:1.05rem'>{a['icon']}</span>"
                    f"<span>{a['text']}</span>"
                    f"<span class='rd-muted' style='margin-left:auto;font-size:.8rem'>"
                    f"{a['t']}</span></div>",
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────────────────────────────────────
# App shell — dark sidebar nav
# ─────────────────────────────────────────────────────────────────────────────

PAGES = [
    ("editor",    "📝  PDF Editor",     page_editor),
    ("bulk",      "⚡  Bulk Generator", page_bulk),
    ("dashboard", "📊  Dashboard",      page_dashboard),
]


def main():
    inject_css()
    _stats()  # ensure initialised
    current = st.session_state.setdefault("nav", "editor")

    with st.sidebar:
        st.markdown(
            "<div style='padding:.4rem .2rem 1.2rem'>"
            "<span style='font-size:1.45rem;font-weight:800;color:#fff'>📄 Redraft</span>"
            "<div style='color:#7b7e96;font-size:.78rem;margin-top:2px'>"
            "PDF editing & automation</div></div>",
            unsafe_allow_html=True,
        )
        for key, label, _ in PAGES:
            if st.button(label, key=f"nav_{key}",
                         type="primary" if current == key else "secondary"):
                st.session_state["nav"] = key
                st.rerun()

        st.markdown("<div style='flex:1'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='position:fixed;bottom:1rem;left:1rem;display:flex;"
            "align-items:center;gap:.6rem;color:#c7c9d9'>"
            "<div style='width:32px;height:32px;border-radius:50%;background:"
            f"{INDIGO};display:flex;align-items:center;justify-content:center;"
            "color:#fff;font-weight:700;font-size:.85rem'>R</div>"
            "<div style='font-size:.82rem'><b style='color:#fff'>Redraft</b><br>"
            "<span style='color:#7b7e96'>Local workspace</span></div></div>",
            unsafe_allow_html=True,
        )

    # Render the active page, with a final safety net so the user never sees a
    # raw Python traceback.
    page_fn = dict((k, fn) for k, _, fn in PAGES).get(current, page_editor)
    try:
        page_fn()
    except RedraftError as e:
        st.error(f"⚠️ {e}")
    except Exception as exc:  # noqa: BLE001
        st.error("⚠️ Something went wrong while rendering this page. "
                 f"({type(exc).__name__})")
        st.caption("Try re-uploading your file or resetting. "
                   "Your other work is unaffected.")


if __name__ == "__main__" or True:
    main()
