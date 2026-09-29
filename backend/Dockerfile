# Redraft API — FastAPI + PyMuPDF on Hugging Face Spaces (Docker)
FROM python:3.12-slim

# The fonts documents are most often set in, and fontconfig to find them. The editor
# draws a letter a document's font never used from the GENUINE family when one is
# installed (pdf_editor._find_system_font -> fc-list); the slim image has none, and
# DejaVu — LibreOffice's default — is not in the Google Fonts catalogue it falls back
# to, so every such edit was refused in production while passing on a workstation.
# Liberation / Carlito / Caladea are the metric twins of Arial, Times New Roman,
# Courier New, Calibri and Cambria.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core \
       fonts-liberation fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces best practice: run as a non-root user (uid 1000).
# Pre-create /app + .font_cache owned by that user so the engine can write
# downloaded fonts into .font_cache at runtime (path is relative to pdf_editor.py).
RUN useradd -m -u 1000 user \
    && mkdir -p /app/.font_cache \
    && chown -R user:user /app

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python deps first (better layer caching)
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# App code: api.py, annex_model.py, pdf_editor.py, warm_fonts.py
COPY --chown=user . .

# Pre-warm common substitute fonts into .font_cache (best-effort — the engine
# downloads anything else on demand at runtime from raw.githubusercontent.com/google/fonts).
RUN python warm_fonts.py || true

# HF Spaces routes to the port declared as app_port (7860) in README.md
EXPOSE 7860
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860"]
