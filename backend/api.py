"""
api.py — Redraft FastAPI backend.

Thin HTTP layer over the existing, UNCHANGED engine modules:
  • pdf_editor.py     — PDFEditor (text replacement with font matching), get_spans
  • pdf_extractor.py  — (used indirectly by the engine)
  • merge_engine.py   — (kept available; not required by these endpoints)

Endpoints
  GET  /                health / metadata
  POST /extract         multipart {file}                       → spans JSON
  POST /edit            multipart {file, edits}                → edited PDF bytes
  POST /bulk            multipart {template, data, mapping}    → ZIP of PDFs

Design notes
  • Stateless: the client re-sends the PDF with each call. Spans are re-extracted
    server-side so edits/mappings reference spans by their stable index — the
    client never has to round-trip full geometry.
  • Errors are converted to clean HTTP 400/500 JSON, never a raw traceback.
"""

import base64
import csv
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import fitz
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, os.path.dirname(__file__))
import pdf_editor as _pe  # noqa: E402  — module state (memo, cache dir) for font upload
from pdf_editor import PDFEditor, font_source, get_spans, resolve_full_font  # noqa: E402
from annex_model import (  # noqa: E402  — annex rules
    build_model, plan_edits, plan_header_edits, parse_num, detect_template,
)

app = FastAPI(title="Redraft API", version="1.0")

# CORS: allow the Vercel app (prod + preview deploys) and local dev. The real
# security is the JWT check below, not CORS (CORS only constrains browsers).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:3000"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Redraft-Font-Report", "X-Redraft-Generated", "X-Redraft-Failed"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Auth + usage metering (Supabase). Entirely OPT-IN: if the SUPABASE_* env vars
# aren't set the API runs OPEN (local dev / before you configure secrets), so
# deploying this can't break the live app — you turn the lock on with config.
# ─────────────────────────────────────────────────────────────────────────────
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON    = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE = os.environ.get("SUPABASE_SERVICE_KEY", "")
AUTH_ON = bool(SUPABASE_URL and SUPABASE_ANON and SUPABASE_SERVICE)

# Plan limits — documents generated per calendar month.
PLANS = {
    "free": {"documents": 30},
    "pro":  {"documents": 10 ** 9},
}

_TOKEN_CACHE: dict = {}  # access_token -> (user_id, expires_at)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _sb_http(method: str, path: str, headers: dict, body=None, timeout: int = 10):
    """Minimal Supabase REST/Auth call over stdlib. Returns (status, json|None)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SUPABASE_URL}{path}", data=data,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001 — network/parse issues → treated as failure
        return 0, None


def _validate_token(token: str):
    """user_id for a valid Supabase access token, else None. Cached ~5 min."""
    if not token:
        return None
    now = time.time()
    hit = _TOKEN_CACHE.get(token)
    if hit and hit[1] > now:
        return hit[0]
    st, user = _sb_http("GET", "/auth/v1/user",
                        {"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON})
    if st == 200 and isinstance(user, dict) and user.get("id"):
        _TOKEN_CACHE[token] = (user["id"], now + 300)
        return user["id"]
    return None


def require_user(authorization: str = Header(default="")) -> str:
    """Dependency → the caller's user_id, or 401. Returns '' when auth is off."""
    if not AUTH_ON:
        return ""
    token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
    uid = _validate_token(token)
    if not uid:
        raise HTTPException(401, "Please sign in to use Redraft.")
    return uid


def optional_user(authorization: str = Header(default="")) -> str:
    """Like require_user but never 401s — returns '' for anonymous callers. Used
    on the guest-allowed endpoints (compose / extract / edit)."""
    if not AUTH_ON:
        return ""
    token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
    return _validate_token(token) or ""


# ── Guest (anonymous) daily limit, metered by IP (in-memory; soft cap) ──
GUEST_DAILY_LIMIT = int(os.environ.get("GUEST_DAILY_LIMIT", "5"))
_IP_USAGE: dict = {}  # (ip, "YYYY-MM-DD") -> count


def _client_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _guest_meter_check(ip: str, n: int):
    used = _IP_USAGE.get((ip, _period_day()), 0)
    if used + n > GUEST_DAILY_LIMIT:
        raise HTTPException(
            402, f"You've reached the free daily limit of {GUEST_DAILY_LIMIT} "
                 f"documents. Sign in to Redraft for more.")


def _guest_meter_add(ip: str, n: int):
    key = (ip, _period_day())
    _IP_USAGE[key] = _IP_USAGE.get(key, 0) + n


def _period_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _svc_headers(extra: dict = None) -> dict:
    h = {"apikey": SUPABASE_SERVICE, "Authorization": f"Bearer {SUPABASE_SERVICE}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _get_profile(uid: str) -> dict:
    """Get-or-create the user's profile row (service_role bypasses RLS)."""
    st, rows = _sb_http("GET",
                        f"/rest/v1/profiles?id=eq.{uid}&select=plan,used,period,doc_limit",
                        _svc_headers())
    if st == 200 and rows:
        return rows[0]
    st, rows = _sb_http("POST", "/rest/v1/profiles",
                        _svc_headers({"Prefer": "return=representation"}),
                        body={"id": uid})
    if st in (200, 201) and rows:
        return rows[0]
    return {"plan": "free", "used": 0, "period": _period()}


def _limit_for(prof: dict) -> int:
    """Monthly document limit: a per-user doc_limit override wins; else the plan."""
    dl = prof.get("doc_limit")
    if dl is not None:
        return int(dl)
    return PLANS.get(prof.get("plan", "free"), PLANS["free"])["documents"]


def _effective_used(prof: dict) -> int:
    return 0 if prof.get("period") != _period() else int(prof.get("used") or 0)


def _meter_check(uid: str, n: int):
    """Raise 402 if generating n docs would exceed the caller's monthly limit."""
    if not AUTH_ON or not uid:
        return
    prof = _get_profile(uid)
    limit = _limit_for(prof)
    used = _effective_used(prof)
    if used + n > limit:
        raise HTTPException(
            402, f"You've used {used} of {limit} documents this month. "
                 f"Upgrade for more.")


def _meter_add(uid: str, n: int):
    """Add n to the caller's monthly usage (resets on a new month)."""
    if not AUTH_ON or not uid or n <= 0:
        return
    prof = _get_profile(uid)
    _sb_http("PATCH", f"/rest/v1/profiles?id=eq.{uid}",
             _svc_headers({"Prefer": "return=minimal"}),
             body={"used": _effective_used(prof) + n, "period": _period(),
                   "updated_at": _now_iso()})




# ── Upload limits — protect the free-tier server from OOM / abuse ──
MAX_TEMPLATE_BYTES = 20 * 1024 * 1024   # 20 MB template PDF
MAX_DATA_BYTES     = 10 * 1024 * 1024   # 10 MB data file (CSV/XLSX)
MAX_PDF_BYTES      = 25 * 1024 * 1024   # 25 MB any single uploaded PDF
MAX_FONT_BYTES     = 8 * 1024 * 1024    # 8 MB uploaded .ttf/.otf
MAX_ROWS           = 500                # rows per bulk / annex run


def _check_size(label: str, data: bytes, limit: int) -> None:
    if len(data) > limit:
        raise HTTPException(
            413, f"{label} is too large ({len(data) // (1024 * 1024)} MB). "
                 f"The limit is {limit // (1024 * 1024)} MB.")


def _check_rows(rows: list) -> None:
    if len(rows) > MAX_ROWS:
        raise HTTPException(
            413, f"Too many rows ({len(rows)}). The limit is {MAX_ROWS} per run — "
                 f"split your data into smaller batches.")


def _merge_pdfs(pdfs: list) -> bytes:
    """Concatenate PDF byte-strings into a single PDF (PyMuPDF)."""
    merged = fitz.open()
    try:
        for pdf in pdfs:
            src = fitz.open(stream=pdf, filetype="pdf")
            try:
                merged.insert_pdf(src)
            finally:
                src.close()
        return merged.tobytes()
    finally:
        merged.close()


def _hex_to_rgb(hex_str: str):
    """'#RRGGBB' → (r,g,b) floats 0..1 for fitz; defaults to near-black."""
    s = (hex_str or "").lstrip("#")
    if len(s) != 6:
        return (0.07, 0.09, 0.15)
    try:
        return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return (0.07, 0.09, 0.15)


def _styled_font_name(fontname: str, bold: bool, italic: bool) -> str:
    """Build the styled variant name of a font family, e.g. 'Poppins-Regular' +
    bold+italic → 'Poppins-BoldItalic'. The resolver understands these names."""
    try:
        fam, _weight, _style = _pe._parse_font_name(fontname)
    except Exception:  # noqa: BLE001
        fam = fontname
    suffix = ("Bold" if bold else "") + ("Italic" if italic else "")
    return f"{fam}-{suffix or 'Regular'}"


def _apply_stamps(pdf_bytes: bytes, stamps: list) -> bytes:
    """Bake added text + signature-image overlays onto the PDF (PyMuPDF).

    Each stamp is in top-left PDF points:
      text: {kind:'text', page, x, y, text, size, color, font}
      sign: {kind:'sign', page, x, y, w, h, data}   (data = PNG data-URL)
    Failures on a single stamp are skipped, never fatal.
    """
    if not stamps:
        return pdf_bytes
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i, st in enumerate(stamps):
            try:
                page = doc[int(st.get("page", 0))]
            except Exception:  # noqa: BLE001 — page out of range
                continue
            kind = st.get("kind")
            if kind == "text":
                text = str(st.get("text", "")).rstrip("\n")
                if not text.strip():
                    continue
                size = float(st.get("size") or 14)
                color = _hex_to_rgb(st.get("color"))
                x = float(st.get("x") or 0)
                y = float(st.get("y") or 0) + size * 0.82   # box-top → baseline
                bold = bool(st.get("bold"))
                italic = bool(st.get("italic"))
                font_name = str(st.get("font") or "")
                # Resolve the styled variant (family-Bold / -Italic / -BoldItalic)
                # first; fall back to the picked font as-is; else a base-14 builtin
                # that carries the requested style so B/I always render.
                raw = None
                if font_name:
                    target = _styled_font_name(font_name, bold, italic) if (bold or italic) else font_name
                    try:
                        raw = resolve_full_font(target)
                    except Exception:  # noqa: BLE001
                        raw = None
                    if raw is None and target != font_name:
                        try:
                            raw = resolve_full_font(font_name)
                        except Exception:  # noqa: BLE001
                            raw = None
                builtin = {(False, False): "helv", (True, False): "hebo",
                           (False, True): "heit", (True, True): "hebi"}[(bold, italic)]
                try:
                    if raw:
                        page.insert_text(fitz.Point(x, y), text, fontsize=size,
                                         color=color, fontname=f"inj{i}", fontbuffer=raw)
                    else:
                        page.insert_text(fitz.Point(x, y), text, fontsize=size,
                                         color=color, fontname=builtin)
                except Exception:  # noqa: BLE001 — safe builtin fallback
                    try:
                        page.insert_text(fitz.Point(x, y), text, fontsize=size,
                                         color=color, fontname=builtin)
                    except Exception:  # noqa: BLE001
                        continue
            elif kind == "sign":
                blob = str(st.get("data") or "")
                if "," in blob:
                    blob = blob.split(",", 1)[1]
                try:
                    img = base64.b64decode(blob)
                except Exception:  # noqa: BLE001
                    continue
                x = float(st.get("x") or 0); y = float(st.get("y") or 0)
                w = float(st.get("w") or 0); h = float(st.get("h") or 0)
                if w <= 0 or h <= 0:
                    continue
                try:
                    page.insert_image(fitz.Rect(x, y, x + w, y + h), stream=img)
                except Exception:  # noqa: BLE001
                    continue
        return doc.tobytes()
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# Engine glue (ported verbatim from the proven Streamlit path; engine untouched)
# ─────────────────────────────────────────────────────────────────────────────

class _TmpPDF:
    def __init__(self, data: bytes):
        self._data, self._path = data, None

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


def extract_spans(pdf_bytes: bytes) -> list:
    """All spans across all pages as plain serialisable dicts (index == order)."""
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


def page_dims(pdf_bytes: bytes) -> list:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    dims = [{"index": i, "width": doc[i].rect.width, "height": doc[i].rect.height}
            for i in range(len(doc))]
    doc.close()
    return dims


def apply_replacements(pdf_bytes: bytes, replacements: list,
                       preserve_size: bool = True) -> tuple:
    """Apply [(span_dict, new_text), …] → (edited_bytes, font_report).

    `preserve_size` (default True) keeps each edited span's original font size
    exactly — in-place editing must never silently resize text. Set False for
    fit-to-box generation where long values should shrink into a fixed column.

    font_report mirrors the Streamlit UI's report so the client can surface
    substituted/fallback fonts instead of silently rendering something else.
    """
    with _TmpPDF(pdf_bytes) as in_path:
        fd, out_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ed = PDFEditor(in_path)
                ed.shrink_to_fit = not preserve_size

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

            fonts_used = {sd["font"] for sd, _ in replacements}
            report = []
            for fn in sorted(fonts_used):
                src = font_source(fn)
                if (src.startswith("system:") or src.startswith("google")
                        or src.startswith("builtin:")):
                    status = "match"
                elif src.startswith("substitute") or src.startswith("script-fallback"):
                    status = "substitute"
                elif src.startswith("BUILTIN") or src.startswith("SUBSET"):
                    status = "fallback"
                else:
                    status = "unknown"
                report.append({"font": fn.split("+")[-1], "status": status,
                               "source": src})

            with open(out_path, "rb") as f:
                edited = f.read()
            return edited, {"fonts": report,
                            "warnings": [str(w.message) for w in caught]}
        finally:
            if os.path.exists(out_path):
                try: os.unlink(out_path)
                except OSError: pass


# ─────────────────────────────────────────────────────────────────────────────
# Font health + user-supplied fonts.
#
# The engine already resolves fonts from `.font_cache/{Family}-{weight}-{style}.ttf`
# BEFORE downloading. So to give a fallback/substitute font a *perfect* match we
# simply drop the user's real font there with the engine's own naming — no engine
# change. We only reset the engine's in-memory resolution memo so the next edit
# re-resolves against the freshly installed file.
# ─────────────────────────────────────────────────────────────────────────────

_SFNT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1", b"ttcf")

# Characters an edited invoice realistically needs. A font is "complete enough"
# only if it can render all of these — otherwise edits risk boxes (▯).
_COVER = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    " .,:;/-_'\"()%°€&+*"
    "àâçéèêëîïôûùÀÂÇÉÈÊ"
)

# Per-process memo: canonical real-font name → full clone bytes (or None). Avoids
# re-attempting a (possibly network) lookup for the same font on every request.
_CLONE_CACHE: dict = {}


def _font_covers(buf: bytes) -> bool:
    """True if the font file can render every character in _COVER."""
    try:
        fnt = fitz.Font(fontbuffer=buf)
    except Exception:  # noqa: BLE001
        return False
    return all(fnt.has_glyph(ord(c)) for c in _COVER)


def _font_cache_name(fontname: str) -> str:
    """The exact .font_cache filename the engine looks up for *fontname*.

    The family is reduced to alphanumerics so a crafted font name can never
    escape the cache directory (path traversal via '/', '..', etc.).
    """
    fam, weight, style = _pe._parse_font_name(fontname)
    nospace = re.sub(r"[^A-Za-z0-9]", "", fam)[:64] or "font"
    style = "italic" if str(style).lower().startswith("ital") else "normal"
    return f"{nospace}-{int(weight)}-{style}.ttf"


def _font_status(fontname: str) -> dict:
    """Resolve *fontname* and describe how well it matched (for the UI)."""
    resolve_full_font(fontname)          # populates font_source; result is memoised
    src = font_source(fontname) or ""
    if src.startswith("builtin:"):
        status = "builtin"
    elif src.startswith("system:") or src.startswith("google"):
        status = "match"
    elif src.startswith("substitute"):
        status = "substitute"
    else:
        status = "fallback"
    fam, weight, style = _pe._parse_font_name(fontname)
    return {
        "font":       fontname.split("+")[-1],
        "raw_font":   fontname,
        "family":     fam,
        "weight":     weight,
        "style":      style,
        "status":     status,            # builtin | match | substitute | fallback
        "source":     src,
        "cache_name": _font_cache_name(fontname),
    }


def _ingest_embedded_fonts(pdf_bytes: bytes) -> list:
    """Use a PDF's OWN embedded fonts when their names have been stripped.

    Many generators (iText / JasperReports, etc.) embed the *full* real font but
    rename it to an anonymous tag like ``CIDFont+F1``. The engine resolves fonts
    by name, so it can't identify that tag and falls back — which is why new text
    can render as boxes even though the real font (e.g. full Arial / Calibri) is
    sitting right inside the file. Here we extract that embedded program and drop
    it into the engine's font cache under the *exact* name the engine looks up, so
    it loads the document's own font: pixel-perfect, automatic, no engine change.

    Picking the font for each stripped name (in order of preference):
      1. the embedded program itself, IF it can render the full editing charset
         (exact, best fidelity);
      2. else the full open-source clone of the embedded font's *real* name
         (e.g. Arial → Arimo: metric-identical, complete cmap, no boxes);
      3. else the embedded program as a best effort (covers the doc's own chars).

    An existing cache file that already covers the charset is left untouched (so a
    user-uploaded font, or a clone we installed earlier, is respected).

    Returns a list describing what was installed (empty if nothing changed).
    """
    installed: list = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return installed
    seen, changed = set(), False
    try:
        for pno in range(doc.page_count):
            for fo in doc[pno].get_fonts(full=True):
                xref, basefont = fo[0], fo[3]
                if xref in seen or not basefont:
                    continue
                seen.add(xref)
                cache_name = _font_cache_name(basefont)
                cache_path = os.path.join(_pe._FONT_CACHE_DIR, cache_name)

                # Already have a complete font under this name → done.
                if os.path.exists(cache_path):
                    with open(cache_path, "rb") as fh:
                        if _font_covers(fh.read()):
                            continue

                try:
                    _, _ext, _ftype, buf = doc.extract_font(xref)
                except Exception:  # noqa: BLE001
                    buf = None
                if not buf or len(buf) < 4 or buf[:4] not in _SFNT_MAGICS:
                    continue  # nothing usable embedded — leave to the name resolver
                try:
                    fnt = fitz.Font(fontbuffer=buf)
                    real_name, nglyph = fnt.name, fnt.glyph_count
                except Exception:  # noqa: BLE001
                    continue

                if _font_covers(buf):
                    chosen, via = buf, f"embedded:{real_name}"      # exact + complete
                else:
                    canonical = real_name.replace(" ", "")
                    if canonical not in _CLONE_CACHE:
                        # Probe for an open-source clone of the embedded font. A miss
                        # (e.g. Calibri → no clone) is EXPECTED — we keep the embedded
                        # font — so silence the engine's misleading "fallback" warning.
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            try:
                                _CLONE_CACHE[canonical] = resolve_full_font(canonical)
                            except Exception:  # noqa: BLE001
                                _CLONE_CACHE[canonical] = None
                    full = _CLONE_CACHE[canonical]
                    if full and _font_covers(full):
                        chosen, via = full, f"clone:{canonical}"     # full metric clone
                    elif nglyph >= 200:
                        chosen, via = buf, f"embedded-partial:{real_name}"
                    else:
                        continue

                if os.path.exists(cache_path):
                    with open(cache_path, "rb") as fh:
                        if fh.read() == chosen:
                            continue                                 # already installed
                with open(cache_path, "wb") as fh:
                    fh.write(chosen)
                changed = True
                installed.append({"font": basefont.split("+")[-1],
                                  "real_name": real_name,
                                  "via": via,
                                  "installed_as": cache_name})
    finally:
        doc.close()
    if changed:
        # Drop the in-memory resolution memo so the next edit picks up the files.
        _pe._RESOLVED.clear()
        _pe._FONT_SOURCE.clear()
    return installed


def parse_table(filename: str, data: bytes) -> tuple:
    """CSV or Excel → (headers, rows). Raises ValueError with a clean message."""
    ext = Path(filename).suffix.lower()
    if ext in (".xlsx", ".xls"):
        try:
            import pandas as pd
            df = pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
            return list(df.columns), df.to_dict("records")
        except ImportError:
            raise ValueError("Excel support requires pandas/openpyxl; upload CSV.")
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Couldn't read the Excel file ({type(exc).__name__}).")
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            reader = csv.DictReader(io.StringIO(data.decode(enc)))
            rows   = [dict(r) for r in reader]
            hdrs   = list(reader.fieldnames or [])
            if hdrs:
                return hdrs, rows
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Couldn't parse the CSV ({type(exc).__name__}).")
    raise ValueError("The data file is empty or has no header row.")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"app": "Redraft API", "version": "1.0",
            "endpoints": ["/extract", "/edit", "/bulk"], "auth": AUTH_ON}


@app.get("/me")
async def me(user: str = Depends(require_user)):
    """The caller's plan + monthly usage, for the UI. Open mode → unlimited."""
    if not AUTH_ON or not user:
        return {"auth": False, "plan": "pro", "used": 0, "limit": None}
    prof = await run_in_threadpool(_get_profile, user)
    plan = prof.get("plan", "free")
    limit = _limit_for(prof)
    return {"auth": True, "plan": plan, "used": _effective_used(prof),
            "limit": (None if limit >= 10 ** 9 else limit)}


# ─────────────────────────────────────────────────────────────────────────────
# Compose: turn plain text into a clean, editable, signable A4 PDF (no upload).
# Uses PyMuPDF's Story engine (HTML/CSS typography) so the result is properly
# formatted — real margins, headings, justified paragraphs, auto-pagination —
# then opens straight in the editor. This is the "text → document" primitive.
# ─────────────────────────────────────────────────────────────────────────────
import html as _html  # noqa: E402
from datetime import date as _date  # noqa: E402

MAX_COMPOSE_CHARS = 60_000

# Classic Legal treatment: serif body, centered title over a full-width rule,
# a Référence / Date row, bold "Article" headings, justified paragraphs, and a
# paired signature block. Notary-grade — reads as a real filed contract.
_COMPOSE_CSS = """
* { font-family: serif; color: #1a1a1a; }
h1 { font-size: 15pt; font-weight: bold; text-align: center;
     letter-spacing: .4pt; margin: 0 0 8pt 0; }
.trule { border-bottom: 1pt solid #1a1a1a; margin: 0 0 7pt 0; }
.ref { text-align: center; font-size: 9pt; color: #555; margin: 0 0 24pt 0; }
h2 { font-size: 11pt; font-weight: bold; margin: 16pt 0 6pt 0; }
p { font-size: 10.5pt; line-height: 1.55; margin: 0 0 9pt 0; text-align: justify; }
.sig { width: 100%; margin-top: 42pt; font-size: 10pt; }
.sig td { width: 50%; padding-right: 24pt; }
.ln { border-top: .8pt solid #1a1a1a; margin-top: 46pt; }
"""


def _compose_html(title: str, body: str, meta: str = "") -> str:
    """Build the Classic Legal document from plain text: blank lines → justified
    paragraphs; a short line in CAPS or ending ':' → a bold heading. Header has a
    ruled title + Référence/Date row (date auto-filled today); ends with a paired
    signature block. `meta`, if given, overrides the reference line."""
    parts = []
    if title.strip():
        parts.append(f"<h1>{_html.escape(title.strip())}</h1>")
        parts.append('<div class="trule"></div>')
        ref = _html.escape(meta.strip()) if meta.strip() else (
            f"Référence : ____________&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"Date : {_date.today().strftime('%d / %m / %Y')}"
        )
        parts.append(f'<p class="ref">{ref}</p>')
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block:
            continue
        one_line = "\n" not in block
        # A heading is a short standalone line that is either a legal section
        # marker (Article/Clause/Section/Titre/Chapitre/Préambule/Annexe…), a
        # numbered item ("1." / "1)"), or ALL-CAPS. Colon lead-ins like
        # "Entre les soussignés :" stay as normal paragraphs.
        looks_heading = one_line and len(block) <= 72 and (
            block.isupper()
            or re.match(r"^(article|clause|section|titre|chapitre|pr[ée]ambule"
                        r"|annexe|art\.)\b", block, re.I)
            or re.match(r"^\d+[.)]\s+\S", block)
        )
        safe = _html.escape(block).replace("\n", "<br/>")
        parts.append(f"<h2>{safe}</h2>" if looks_heading else f"<p>{safe}</p>")
    parts.append(
        '<table class="sig"><tr>'
        '<td>Signature<div class="ln"></div></td>'
        '<td>Signature<div class="ln"></div></td>'
        "</tr></table>"
    )
    return "<html><body>" + "".join(parts) + "</body></html>"


def compose_pdf(title: str, body: str, meta: str = "") -> bytes:
    """Flow the HTML across A4 pages with clean margins → editable PDF bytes."""
    page_rect = fitz.paper_rect("a4")
    margin = 64
    where = page_rect + (margin, margin, -margin, -margin)
    story = fitz.Story(html=_compose_html(title, body, meta), user_css=_COMPOSE_CSS)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    more = 1
    guard = 0
    while more and guard < 50:               # 50-page hard cap (safety)
        dev = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        guard += 1
    writer.close()
    return buf.getvalue()


@app.post("/compose")
async def compose(text: str = Form(...), title: str = Form(""),
                  meta: str = Form(""), user: str = Depends(optional_user)):
    """Plain text → a clean, editable A4 PDF (opened in the editor client-side).
    Guest-allowed: composing is free; the daily cap applies at download (/edit)."""
    if len(text) > MAX_COMPOSE_CHARS:
        raise HTTPException(413, f"Text is too long (limit {MAX_COMPOSE_CHARS} chars).")
    if not text.strip():
        raise HTTPException(400, "Provide some text to build the document.")
    try:
        pdf = await run_in_threadpool(compose_pdf, title, text, meta)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Couldn't build the document ({type(exc).__name__}).")
    stem = "".join(c for c in (title or "document") if c.isalnum() or c in " -_").strip() or "document"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})


@app.post("/extract")
async def extract(file: UploadFile = File(...), user: str = Depends(optional_user)):
    """Upload a PDF → JSON of every text span (+ page dimensions). Guest-allowed
    (read-only) so a composed document can open in the editor without login."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    _check_size("The PDF", data, MAX_PDF_BYTES)
    _ingest_embedded_fonts(data)  # use the PDF's own embedded fonts (no boxes)
    try:
        spans = extract_spans(data)
        pages = page_dims(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            400, f"Couldn't read “{file.filename}”. It may be corrupt, encrypted, "
                 f"or not a valid PDF ({type(exc).__name__}).")
    for i, s in enumerate(spans):
        s["id"] = i
    return {"filename": file.filename, "pages": pages,
            "span_count": len(spans), "spans": spans}


@app.post("/edit")
async def edit(request: Request, file: UploadFile = File(...), edits: str = Form(...),
               stamps: str = Form("[]"), final: str = Form(""),
               user: str = Depends(optional_user)):
    """Apply replacements + baked overlays and return the edited PDF bytes.

    `edits` is a JSON array: [{"index": <span_id>, "new_text": "..."}].
    `stamps` (optional) is a JSON array of added text / signature overlays in
    top-left PDF points — see _apply_stamps. `final` = truthy only on the real
    download; it counts 1 toward the plan (signed-in) or the free daily cap
    (guest, by IP). Previews leave it empty. Guest-allowed so a composed doc can
    be edited + downloaded without login. Spans are re-extracted server-side.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    _check_size("The PDF", data, MAX_PDF_BYTES)
    _ingest_embedded_fonts(data)  # use the PDF's own embedded fonts (no boxes)
    try:
        edit_list = json.loads(edits)
        assert isinstance(edit_list, list)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "`edits` must be a JSON array of "
                                 "{index, new_text} objects.")
    try:
        stamp_list = json.loads(stamps)
        assert isinstance(stamp_list, list)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "`stamps` must be a JSON array.")
    try:
        spans = extract_spans(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the PDF ({type(exc).__name__}).")

    replacements = []
    for e in edit_list:
        try:
            i = int(e["index"])
            nt = str(e["new_text"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Each edit needs an integer `index` and "
                                     "a `new_text` string.")
        if 0 <= i < len(spans):
            replacements.append((spans[i], nt))

    if not replacements and not stamp_list:
        raise HTTPException(400, "Nothing to apply — no edits or added items.")

    ip = _client_ip(request)
    if final:
        if user:
            await run_in_threadpool(_meter_check, user, 1)
        elif AUTH_ON:  # guest download → per-IP daily cap
            _guest_meter_check(ip, 1)

    try:
        if replacements:
            edited, report = apply_replacements(data, replacements)
        else:
            edited, report = data, {"fonts": [], "warnings": []}
        edited = _apply_stamps(edited, stamp_list)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to apply edits "
                                 f"({type(exc).__name__}: {exc}).")

    if final:
        if user:
            await run_in_threadpool(_meter_add, user, 1)
        elif AUTH_ON:
            _guest_meter_add(ip, 1)

    hdr = base64.b64encode(json.dumps(report).encode()).decode()
    stem = Path(file.filename or "document").stem
    return Response(
        content=edited, media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="edited_{stem}.pdf"',
            "X-Redraft-Font-Report": hdr,
        },
    )


@app.post("/bulk")
async def bulk(template: UploadFile = File(...),
               data: UploadFile = File(...),
               mapping: str = Form(...),
               filename_col: str = Form(""),
               output: str = Form("zip"),
               user: str = Depends(require_user)):
    """Generate one PDF per data row → a ZIP (or one merged PDF).

    `mapping` is a JSON object {"<span_index>": "<column_name>", …}. Each mapped
    field is replaced by that column's value for the row. `filename_col`
    (optional) names each output file from a data column (falls back to
    row_NNNN.pdf). `output` = "zip" (default, one file per row) or "merged"
    (all rows concatenated into a single PDF).
    """
    tmpl_bytes = await template.read()
    data_bytes = await data.read()
    if not tmpl_bytes or not data_bytes:
        raise HTTPException(400, "Both a template PDF and a data file are required.")
    _check_size("The template PDF", tmpl_bytes, MAX_TEMPLATE_BYTES)
    _check_size("The data file", data_bytes, MAX_DATA_BYTES)
    _ingest_embedded_fonts(tmpl_bytes)  # use the template's own embedded fonts

    try:
        mp = json.loads(mapping)
        mp = {int(k): str(v) for k, v in mp.items()}
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "`mapping` must be a JSON object of "
                                 "{span_index: column_name}.")
    if not mp:
        raise HTTPException(400, "Map at least one field to a column.")

    try:
        headers, rows = parse_table(data.filename or "data.csv", data_bytes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not rows:
        raise HTTPException(400, "The data file has no rows.")
    _check_rows(rows)
    await run_in_threadpool(_meter_check, user, len(rows))

    try:
        spans = extract_spans(tmpl_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the template ({type(exc).__name__}).")

    unknown = [c for c in mp.values() if c not in headers]
    if unknown:
        raise HTTPException(400, f"Column(s) not found in data file: "
                                 f"{', '.join(sorted(set(unknown)))}.")

    files: list = []       # [(name, pdf_bytes)]
    failed = 0
    used_names: set = set()
    for row_idx, row in enumerate(rows):
        reps = [(spans[i], str(row.get(col, "")))
                for i, col in mp.items()
                if 0 <= i < len(spans) and str(row.get(col, ""))]
        try:
            out, _ = apply_replacements(tmpl_bytes, reps)
            name = _row_filename(row, filename_col, headers, row_idx, "row")
            if name in used_names:                       # avoid overwrite
                name = f"{name[:-4]}_{row_idx + 1}.pdf"
            used_names.add(name)
            files.append((name, out))
        except Exception:  # noqa: BLE001 — skip the bad row, keep going
            failed += 1

    if not files:
        raise HTTPException(500, "Every row failed to generate.")
    await run_in_threadpool(_meter_add, user, len(files))

    stem = Path(template.filename or "template").stem
    hdrs = {"X-Redraft-Generated": str(len(files)), "X-Redraft-Failed": str(failed)}
    if output == "merged":
        return Response(
            content=_merge_pdfs([b for _n, b in files]), media_type="application/pdf",
            headers={**hdrs,
                     "Content-Disposition": f'attachment; filename="{stem}_bulk.pdf"'})
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, pdf in files:
            zf.writestr(name, pdf)
    return Response(
        content=zip_buf.getvalue(), media_type="application/zip",
        headers={**hdrs,
                 "Content-Disposition": f'attachment; filename="{stem}_bulk.zip"'})


@app.post("/fonts")
async def fonts(file: UploadFile = File(...), user: str = Depends(require_user)):
    """Upload a PDF → status of every distinct font it uses.

    Lets the UI flag fonts that won't match exactly (`fallback`/`substitute`)
    and offer to upload the real file. First call may be slow if fonts still
    need downloading; results are cached afterwards.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    _check_size("The PDF", data, MAX_PDF_BYTES)
    auto = _ingest_embedded_fonts(data)  # adopt the PDF's own embedded fonts first
    try:
        spans = extract_spans(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the PDF ({type(exc).__name__}).")
    distinct = sorted({s["font"] for s in spans})
    report = [_font_status(f) for f in distinct]
    return {"count": len(report), "fonts": report, "auto_installed": auto}


@app.post("/font")
async def upload_font(fontname: str = Form(...), file: UploadFile = File(...),
                      user: str = Depends(require_user)):
    """Install a user-supplied .ttf/.otf so *fontname* matches exactly.

    Saves it under the engine's cache name and clears the resolution memo so the
    next edit picks it up. Returns the font's new status.
    """
    raw = await file.read()
    _check_size("The font file", raw, MAX_FONT_BYTES)
    if len(raw) < 4 or raw[:4] not in _SFNT_MAGICS:
        if raw[:4] in (b"wOFF", b"wOF2"):
            raise HTTPException(400, "WOFF/WOFF2 isn't supported — upload the .ttf "
                                     "or .otf version of this font.")
        raise HTTPException(400, "That doesn't look like a .ttf or .otf font file.")

    name = _font_cache_name(fontname)
    # Defence in depth: the resolved path must stay inside the font cache dir.
    path = os.path.abspath(os.path.join(_pe._FONT_CACHE_DIR, name))
    if os.path.dirname(path) != os.path.abspath(_pe._FONT_CACHE_DIR):
        raise HTTPException(400, "Invalid font name.")
    try:
        with open(path, "wb") as f:
            f.write(raw)
        # Sanity-check the engine can actually load it; otherwise back it out.
        fitz.Font(fontfile=path)
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(path):
            try: os.unlink(path)
            except OSError: pass
        raise HTTPException(400, f"Couldn't use that font file ({type(exc).__name__}).")

    # Force re-resolution against the freshly installed file on the next edit.
    _pe._RESOLVED.clear()
    _pe._FONT_SOURCE.clear()

    return {"ok": True, "installed_as": name, "font": _font_status(fontname)}


# ─────────────────────────────────────────────────────────────────────────────
# Annex automation (rules-based line-item generation).
#   /annex/model     PDF                          → structured rows (for the UI)
#   /annex/generate  template + data + mapping    → ZIP, one annex per data row
# Quantity rule: a mapped cell that is 0 or empty REMOVES that line; any other
# value sets the quantity and recomputes the line amount + the Total HT.
# ─────────────────────────────────────────────────────────────────────────────

def _annex_spans_and_model(pdf_bytes: bytes, template: dict | None = None):
    """Extract spans (id == index) and build the annex model, using *template*
    (auto-detected via detect_template when None).

    Returns (spans, model, template) so the caller can echo the template back to
    the client to save (scan-once) or reuse it for width/erase geometry.
    """
    spans = extract_spans(pdf_bytes)
    for i, s in enumerate(spans):
        s["id"] = i
    tmpl = template or detect_template(spans)
    return spans, build_model(spans, tmpl), tmpl


# Numeric columns are right-aligned, and the engine shrinks any value wider than
# its original cell (pdf_editor shrink-to-fit). Widening the cell leftward — right
# edge fixed — lets a longer number (qty 9 → 5000) render full-size instead of
# tiny. The floor (a safe left edge in the inter-column gap) comes from the
# template now, so it's correct for whatever annex was scanned — not hardcoded.
def _relax_numeric(span: dict, col: str | None, template: dict) -> dict:
    """Return a copy of *span* with its numeric cell widened leftward to the
    template's floor for *col* (so a longer number renders full-size)."""
    if col == "total":
        floor = template.get("totalFloor")
    else:
        floor = (template.get("columns", {}).get(col or "") or {}).get("floor")
    if floor is None:
        return span
    x0, y0, x1, y1 = span["bbox"]
    if x0 <= floor:
        return span
    widened = dict(span)
    widened["bbox"] = [floor, y0, x1, y1]
    return widened


def _measure_text(span: dict, text: str) -> float:
    """Width (pt) the engine will render *text* at, using the font it will use."""
    raw = None
    try:
        raw = resolve_full_font(span["font"])
    except Exception:  # noqa: BLE001
        raw = None
    try:
        fobj = fitz.Font(fontbuffer=raw) if raw else fitz.Font("helv")
    except Exception:  # noqa: BLE001
        fobj = fitz.Font("helv")
    return fobj.text_length(text, span["size"])


def _header_edits(span: dict, text: str) -> list:
    """A header field is label+value in ONE span, and the engine sometimes
    mis-detects its alignment as center/right — which makes the text drift when the
    value's length changes. Fix it positionally: erase the whole original, then
    stamp the new value in a cell sized to fit it exactly from the original left
    edge, so for left/center/right alignment the engine re-anchors it at x0.
    Returns [(erase_span, ""), (stamp_span, text)].
    """
    x0, y0, x1, y1 = span["bbox"]
    tw = _measure_text(span, text)
    erase = dict(span)
    erase["bbox"] = [x0 - 1.0, y0, max(x1, x0 + tw) + 2.0, y1]
    stamp = dict(span)
    stamp["bbox"] = [x0, y0, x0 + tw + 1.0, y1]
    return [(erase, ""), (stamp, text)]


def _annex_colmap(model: dict) -> dict:
    """span_id → numeric column ('qty'|'amount'|'total') for width-relaxing."""
    cm: dict = {}
    for it in model["items"]:
        if it["spanIds"]["qty"] is not None:
            cm[it["spanIds"]["qty"]] = "qty"
        if it["spanIds"]["amount"] is not None:
            cm[it["spanIds"]["amount"]] = "amount"
    if model.get("total") and model["total"].get("valueId") is not None:
        cm[model["total"]["valueId"]] = "total"
    return cm


def _annex_erase_bands(model: dict, spans: list,
                       removed_items: set, removed_secs: list,
                       template: dict) -> list:
    """Synthetic blank spans that erase a whole removed row / section band.

    Per-cell blanks only cover text; a removed row/section can also carry vector
    marks (the section-title underline, row background shading) the engine won't
    touch. Erasing the full band (text + marks) leaves a clean gap. Returns
    (span, "") pairs ready to append to the replacement list.
    """
    out: list = []
    edges = template.get("tableEdges") or {"x0": 23.0, "x1": 540.5}
    tx0, tx1 = edges.get("x0", 23.0), edges.get("x1", 540.5)

    def band(ids: list, pad_bottom: float = 1.0):
        boxes = [spans[i]["bbox"] for i in ids if 0 <= i < len(spans)]
        if not boxes:
            return None
        y0 = min(b[1] for b in boxes) - 1.0
        y1 = max(b[3] for b in boxes) + pad_bottom
        s = dict(spans[ids[0]])
        s["bbox"] = [tx0, y0, tx1, y1]
        s["text"] = ""
        return s

    for idx in removed_items:
        it = model["items"][idx]
        ids = list(it["spanIds"]["label"])
        ids += [it["spanIds"][k] for k in ("unit", "qty", "price", "amount")
                if it["spanIds"][k] is not None]
        s = band(ids)
        if s:
            out.append((s, ""))

    for si in removed_secs:
        s = band(model["sections"][si]["ids"], pad_bottom=3.0)  # +catch underline
        if s:
            out.append((s, ""))

    return out


@app.post("/annex/model")
async def annex_model(file: UploadFile = File(...), template: str = Form(None),
                      user: str = Depends(require_user)):
    """Upload an annex PDF → its detected line items, sections and total.

    Optional `template` (JSON) reuses a saved layout (scan-once); when omitted the
    layout is auto-detected. The template actually used is echoed back so the
    client can save it and skip re-detection next time.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    _check_size("The PDF", data, MAX_PDF_BYTES)
    _ingest_embedded_fonts(data)
    tmpl_in = None
    if template:
        try:
            tmpl_in = json.loads(template)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "`template` must be valid JSON.")
    try:
        _spans, model, tmpl = _annex_spans_and_model(data, tmpl_in)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the PDF ({type(exc).__name__}).")
    def _flat_ids(it):
        ids = list(it["spanIds"]["label"])
        ids += [it["spanIds"][k] for k in ("unit", "qty", "price", "amount")
                if it["spanIds"][k] is not None]
        return ids

    items = [{"index": idx,
              "section":   it["section"],
              "label":     it["label"],
              "unit":      it["unit"],
              "qty":       it["qty"],
              "unitPrice": it["unitPrice"],
              "amount":    it["amount"],
              "ids":       _flat_ids(it)}
             for idx, it in enumerate(model["items"])]
    headers = [{"key": h["key"], "label": h["label"],
                "value": h["value"], "spanId": h["spanId"]}
               for h in model.get("headers", [])]
    return {"filename": file.filename,
            "sections": [s["title"] for s in model["sections"]],
            "items": items,
            "headers": headers,
            "total": model["total"],
            "item_count": len(items),
            "template": tmpl}


def _row_filename(row: dict, col: str, data_headers: list, row_idx: int,
                  prefix: str = "doc") -> str:
    """Per-row output name from a data column (falls back to <prefix>_NNNN.pdf)."""
    if col and col in data_headers:
        raw = str(row.get(col, "")).strip()
        safe = "".join(c if (c.isalnum() or c in " -_.") else "_" for c in raw).strip()
        if safe:
            return safe if safe.lower().endswith(".pdf") else safe + ".pdf"
    return f"{prefix}_{row_idx + 1:04d}.pdf"


@app.post("/annex/generate")
async def annex_generate(template: UploadFile = File(...),
                         data: UploadFile = File(...),
                         mapping: str = Form(...),
                         headers: str = Form("{}"),
                         profile: str = Form(None),
                         filename_col: str = Form(""),
                         user: str = Depends(require_user)):
    """One annex per data row → ZIP.

    `mapping` is a JSON object {"<item_index>": "<column_name>", …} linking each
    line item to the data column holding its quantity. Per row: 0/empty quantity
    removes the line; any other value sets it and recomputes amount + Total HT.
    `headers` (optional) is {"<field_key>": "<column_name>", …} for the document
    info fields (facture N°, client, ICE, période…).
    `profile` (optional) is the saved layout template JSON (auto-detected if
    omitted); `filename_col` names each output file from a data column.
    """
    tmpl_bytes = await template.read()
    data_bytes = await data.read()
    if not tmpl_bytes or not data_bytes:
        raise HTTPException(400, "Both a template PDF and a data file are required.")
    _check_size("The template PDF", tmpl_bytes, MAX_TEMPLATE_BYTES)
    _check_size("The data file", data_bytes, MAX_DATA_BYTES)
    _ingest_embedded_fonts(tmpl_bytes)

    try:
        mp = json.loads(mapping)
        mp = {int(k): str(v) for k, v in mp.items()}
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "`mapping` must be a JSON object of "
                                 "{item_index: column_name}.")
    try:
        hmap = json.loads(headers)
        hmap = {str(k): str(v) for k, v in hmap.items()}
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "`headers` must be a JSON object of "
                                 "{field_key: column_name}.")
    if not mp and not hmap:
        raise HTTPException(400, "Map at least one line or field to a column.")

    try:
        data_headers, rows = parse_table(data.filename or "data.csv", data_bytes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not rows:
        raise HTTPException(400, "The data file has no rows.")
    _check_rows(rows)
    await run_in_threadpool(_meter_check, user, len(rows))

    prof_in = None
    if profile:
        try:
            prof_in = json.loads(profile)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "`profile` must be valid JSON.")
    try:
        spans, model, prof = _annex_spans_and_model(tmpl_bytes, prof_in)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the template ({type(exc).__name__}).")
    if not model["items"] and not model.get("headers"):
        raise HTTPException(400, "No line items or document fields were detected.")

    unknown = [c for c in list(mp.values()) + list(hmap.values())
               if c not in data_headers]
    if unknown:
        raise HTTPException(400, f"Column(s) not found in data file: "
                                 f"{', '.join(sorted(set(unknown)))}.")

    colmap = _annex_colmap(model)
    zip_buf = io.BytesIO()
    made = failed = 0
    used_names: set = set()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row_idx, row in enumerate(rows):
            spec: dict = {}
            for idx, col in mp.items():
                if not (0 <= idx < len(model["items"])):
                    continue
                qv = parse_num(str(row.get(col, "")))
                spec[idx] = {"remove": True} if not qv else {"qty": qv}
            reps = [(_relax_numeric(spans[sid], colmap.get(sid), prof) if txt else spans[sid], txt)
                    for sid, txt in plan_edits(model, spec)
                    if 0 <= sid < len(spans)]
            removed_items = {idx for idx, a in spec.items() if a.get("remove")}
            removed_secs = [si for si, sec in enumerate(model["sections"])
                            if sec.get("itemIdx")
                            and all(k in removed_items for k in sec["itemIdx"])]
            reps += _annex_erase_bands(model, spans, removed_items, removed_secs, prof)
            if hmap:
                hspec = {key: row.get(col, "") for key, col in hmap.items()}
                for sid, txt in plan_header_edits(model.get("headers", []), hspec):
                    if 0 <= sid < len(spans):
                        reps += _header_edits(spans[sid], txt)
            try:
                out, _ = apply_replacements(tmpl_bytes, reps)
                name = _row_filename(row, filename_col, data_headers, row_idx, "annex")
                if name in used_names:                       # avoid overwrite
                    name = f"{name[:-4]}_{row_idx + 1}.pdf"
                used_names.add(name)
                zf.writestr(name, out)
                made += 1
            except Exception:  # noqa: BLE001 — skip the bad row, keep going
                failed += 1

    if made == 0:
        raise HTTPException(500, "Every annex failed to generate.")
    await run_in_threadpool(_meter_add, user, made)

    stem = Path(template.filename or "annex").stem
    return Response(
        content=zip_buf.getvalue(), media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}_annexes.zip"',
            "X-Redraft-Generated": str(made),
            "X-Redraft-Failed": str(failed),
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
