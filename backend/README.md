---
title: Radraft
emoji: 📉
colorFrom: indigo
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: radraft billings
---

# Redraft API

FastAPI + PyMuPDF backend for the Redraft PDF editor / annex-automation app.
Stateless: PDFs are processed in memory, nothing is stored server-side.

Endpoints: `/extract`, `/edit`, `/bulk`, `/fonts`, `/font`, `/annex/model`, `/annex/generate`.

The React frontend is deployed separately (Vercel) and points here via `VITE_API_BASE`.
