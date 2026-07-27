# Redraft API — FastAPI + PyMuPDF on Hugging Face Spaces (Docker)
FROM python:3.12-slim

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
