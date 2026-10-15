# Redraft — one image: the editing API, the editor page, and an optional watched folder.
#
#   docker build -t redraft .
#   docker run -p 7860:7860 -e REDRAFT_API_KEY=choose-a-secret redraft
#       API   POST http://localhost:7860/v1/replace      (docs: /docs)
#       page  http://localhost:7860/app
#
# Stays inside the customer's network: no database, no accounts, nothing stored. Every font the
# engine can need is downloaded HERE, at build time, so the container itself can run with no route
# out at all:
#
#   docker run --network none -e REDRAFT_OFFLINE=1 ... redraft
#
# Optional watched folder (drop a PDF in in/, take the edited one from out/):
#
#   docker run -p 7860:7860 -e REDRAFT_API_KEY=... -e REDRAFT_WATCH=/data -v "$PWD/redraft:/data" redraft
#
# The hosted service (Hugging Face) is built from backend/Dockerfile and does not use this file.

# ── 1. the editor page, built same-origin and with no third-party host in it ─────────────────
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/ ./
# Empty VITE_* values beat any .env file: the page talks to the server it was served from
# (API_BASE ""), and there is no login (no Supabase). One step, and node_modules is removed in
# it, so the stage leaves behind only the built page.
RUN npm ci --no-audit --no-fund \
    && REDRAFT_UI=container VITE_API_BASE= VITE_SUPABASE_URL= VITE_SUPABASE_ANON_KEY= npm run build \
    && node scripts/container-ui.mjs dist \
    && rm -rf node_modules

# ── 2. the server ────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Same system fonts as the hosted image (see backend/Dockerfile for why).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core \
       fonts-liberation fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user \
    && mkdir -p /app/.font_cache /data \
    && chown -R user:user /app /data

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app

COPY --chown=user backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# The download is the slow step, so it comes before the code that changes most (api.py, watcher.py).
# This list is the modules warm_fonts.py imports; if one is missing the build fails (WARM_STRICT).
COPY --chown=user backend/warm_fonts.py backend/donor_manifest.json backend/font_donors.py \
     backend/font_extend.py backend/font_metrics.py backend/glyph_synth.py backend/inplace_spike.py \
     backend/kerning.py backend/pdf_editor.py backend/reflow.py backend/spanmerge.py \
     backend/type1_extend.py backend/type3_extend.py ./

# Every font the engine can reach for, fetched now. WARM_STRICT: a font that could not be
# downloaded fails the build, so an image is never "offline-capable" by accident.
RUN WARM_STRICT=1 python warm_fonts.py

COPY --chown=user backend/ .

COPY --from=ui --chown=user /ui/dist /app/ui

VOLUME /data
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','7860'), timeout=3)"
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-7860}"]
