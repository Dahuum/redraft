# Redraft — React + FastAPI

Modern split architecture replacing the Streamlit app:

- **Backend** — `../api.py` (FastAPI) over the *unchanged* engine
  (`pdf_editor.py`, `pdf_extractor.py`, `merge_engine.py`).
- **Frontend** — this folder: React + Vite + Tailwind, PDF rendered in-browser
  with PDF.js; click a text span to select it.

## Run (two terminals)

**1. API (port 8000)**

```bash
cd ..
python3.12 -m pip install --break-system-packages python-multipart uvicorn   # once
python3.12 -m uvicorn api:app --host 0.0.0.0 --port 8000
```

**2. Frontend (port 5173)**

```bash
npm install        # once
npm run dev
```

Open <http://localhost:5173>. The frontend talks to the API at
`http://localhost:8000`; override with `VITE_API_BASE` if needed
(e.g. `VITE_API_BASE=http://1.2.3.4:8000 npm run dev`).

## API

| Method | Path       | Body (multipart)                          | Returns            |
| ------ | ---------- | ----------------------------------------- | ------------------ |
| GET    | `/`        | —                                         | metadata JSON      |
| POST   | `/extract` | `file`                                    | spans + pages JSON |
| POST   | `/edit`    | `file`, `edits` (JSON `[{index,new_text}]`) | edited PDF bytes   |
| POST   | `/bulk`    | `template`, `data` (CSV), `mapping` (JSON `{idx:col}`) | ZIP of PDFs |

`/edit` also returns the font-resolution report (base64 JSON) in the
`X-Redraft-Font-Report` response header; `/bulk` reports counts in
`X-Redraft-Generated` / `X-Redraft-Failed`.

## Layout

```
api.py                     FastAPI app (engine glue + routes)
frontend/
  src/
    api.js                 fetch helpers for the 3 endpoints
    App.jsx                sidebar shell + page switch
    lib/spans.js           click→span hit-test (ported from Python _span_at)
    components/
      Sidebar.jsx          dark navy nav
      UploadZone.jsx       drag & drop
      PdfCanvas.jsx        PDF.js render + click-to-select + highlight overlay
    pages/
      EditorPage.jsx       click-to-edit, preview, download, font warnings
      BulkPage.jsx         template + CSV → field mapping → ZIP
```
