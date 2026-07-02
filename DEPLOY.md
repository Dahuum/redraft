# Deploy — Redraft (free hosting)

Two independent deploys from this one repo, branch **`redraft-react`**:

| Piece      | Folder (root dir) | Host                 |
|------------|-------------------|----------------------|
| Backend    | `backend/`        | Render (web service) |
| Frontend   | `frontend/`       | Vercel (static)      |

---

## 1. Backend — Render web service

In the Render service settings:

- **Branch:** `redraft-react`
- **Root Directory:** `backend`
- **Runtime:** Python (auto-detected from `requirements.txt`)
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`
- **Python version:** pinned by `backend/.python-version` (3.12.7)

Deps (`backend/requirements.txt`) are intentionally lean — the API only uses
FastAPI + PyMuPDF (fonts download via stdlib `urllib`; the 47 cached fonts in
`backend/.font_cache/` ship with the repo, so no cold-download for common fonts).

Free tier note: the service **sleeps after ~15 min idle** → first request wakes
it (~40 s). Fine for a demo/validation link.

After deploy you get a URL like `https://redraft-xxxx.onrender.com`.
Test it: opening that URL should return `{"app":"Redraft API",...}`.

## 2. Frontend — Vercel

Import the repo, then:

- **Branch:** `redraft-react`
- **Root Directory:** `frontend`
- **Framework Preset:** Vite
- **Build Command:** `npm run build`  (default)
- **Output Directory:** `dist`  (default)
- **Environment Variable:**
  - `VITE_API_BASE` = the Render backend URL (e.g. `https://redraft-xxxx.onrender.com`)
    — no trailing slash.

`frontend/src/api.js` reads `import.meta.env.VITE_API_BASE` (defaults to
`http://localhost:8000` for local dev), so setting it at build time points the
UI at the live backend.

## Local dev (unchanged)

```
# backend
uvicorn api:app --app-dir backend --host 0.0.0.0 --port 8000
# frontend
npm --prefix frontend run dev
```
