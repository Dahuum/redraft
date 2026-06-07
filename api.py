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
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path
from typing import List

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

sys.path.insert(0, os.path.dirname(__file__))
import pdf_editor as _pe  # noqa: E402  — module state (memo, cache dir) for font upload
from pdf_editor import PDFEditor, font_source, get_spans, resolve_full_font  # noqa: E402

app = FastAPI(title="Redraft API", version="1.0")

# Dev CORS: the Vite dev server runs on a different origin (5173). Allow all for
# local development — tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Redraft-Font-Report"],
)


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


def apply_replacements(pdf_bytes: bytes, replacements: list) -> tuple:
    """Apply [(span_dict, new_text), …] → (edited_bytes, font_report).

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
                elif src.startswith("substitute"):
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


def _font_cache_name(fontname: str) -> str:
    """The exact .font_cache filename the engine looks up for *fontname*."""
    fam, weight, style = _pe._parse_font_name(fontname)
    nospace = fam.replace(" ", "").replace("-", "")
    return f"{nospace}-{weight}-{style}.ttf"


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
            "endpoints": ["/extract", "/edit", "/bulk"]}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    """Upload a PDF → JSON of every text span (+ page dimensions)."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
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
async def edit(file: UploadFile = File(...), edits: str = Form(...)):
    """Apply replacements and return the edited PDF bytes.

    `edits` is a JSON array: [{"index": <span_id>, "new_text": "..."}].
    Spans are re-extracted server-side; index references the /extract ordering.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    try:
        edit_list = json.loads(edits)
        assert isinstance(edit_list, list)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "`edits` must be a JSON array of "
                                 "{index, new_text} objects.")
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

    if not replacements:
        raise HTTPException(400, "No valid edits to apply.")

    try:
        edited, report = apply_replacements(data, replacements)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to apply edits "
                                 f"({type(exc).__name__}: {exc}).")

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
               mapping: str = Form(...)):
    """Generate one PDF per data row → a ZIP.

    `mapping` is a JSON object {"<span_index>": "<column_name>", …}. Each mapped
    field is replaced by that column's value for the row.
    """
    tmpl_bytes = await template.read()
    data_bytes = await data.read()
    if not tmpl_bytes or not data_bytes:
        raise HTTPException(400, "Both a template PDF and a data file are required.")

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

    try:
        spans = extract_spans(tmpl_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the template ({type(exc).__name__}).")

    unknown = [c for c in mp.values() if c not in headers]
    if unknown:
        raise HTTPException(400, f"Column(s) not found in data file: "
                                 f"{', '.join(sorted(set(unknown)))}.")

    zip_buf = io.BytesIO()
    failed = 0
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row_idx, row in enumerate(rows):
            reps = [(spans[i], str(row.get(col, "")))
                    for i, col in mp.items()
                    if 0 <= i < len(spans) and str(row.get(col, ""))]
            try:
                out, _ = apply_replacements(tmpl_bytes, reps)
                zf.writestr(f"row_{row_idx + 1:04d}.pdf", out)
            except Exception:  # noqa: BLE001 — skip the bad row, keep going
                failed += 1

    if failed == len(rows):
        raise HTTPException(500, "Every row failed to generate.")

    stem = Path(template.filename or "template").stem
    return Response(
        content=zip_buf.getvalue(), media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}_bulk.zip"',
            "X-Redraft-Generated": str(len(rows) - failed),
            "X-Redraft-Failed": str(failed),
        },
    )


@app.post("/fonts")
async def fonts(file: UploadFile = File(...)):
    """Upload a PDF → status of every distinct font it uses.

    Lets the UI flag fonts that won't match exactly (`fallback`/`substitute`)
    and offer to upload the real file. First call may be slow if fonts still
    need downloading; results are cached afterwards.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    try:
        spans = extract_spans(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read the PDF ({type(exc).__name__}).")
    distinct = sorted({s["font"] for s in spans})
    report = [_font_status(f) for f in distinct]
    return {"count": len(report), "fonts": report}


@app.post("/font")
async def upload_font(fontname: str = Form(...), file: UploadFile = File(...)):
    """Install a user-supplied .ttf/.otf so *fontname* matches exactly.

    Saves it under the engine's cache name and clears the resolution memo so the
    next edit picks it up. Returns the font's new status.
    """
    raw = await file.read()
    if len(raw) < 4 or raw[:4] not in _SFNT_MAGICS:
        if raw[:4] in (b"wOFF", b"wOF2"):
            raise HTTPException(400, "WOFF/WOFF2 isn't supported — upload the .ttf "
                                     "or .otf version of this font.")
        raise HTTPException(400, "That doesn't look like a .ttf or .otf font file.")

    name = _font_cache_name(fontname)
    path = os.path.join(_pe._FONT_CACHE_DIR, name)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
