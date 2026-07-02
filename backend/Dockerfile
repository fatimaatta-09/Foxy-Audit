# Foxy Audit backend image — runs both the API (uvicorn) and the grading poller
# (python -m app.worker_main); docker-compose picks the command per service.
FROM python:3.13-slim

# System deps:
#  - libpq5: psycopg (v3) runtime
#  - pango/cairo/gdk-pixbuf/harfbuzz/mime/fonts: what weasyprint needs to render
#    the PDF passport (without them /v1/passport falls back to HTML — see passport.py)
#  - curl: the compose healthcheck hits /health/ready
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libharfbuzz0b libffi8 shared-mime-info fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend (app/, migrations/, scripts/, alembic.ini). The dashboard HTML
# lives at the repo root (outside this build context) and is mounted at /app at
# runtime by docker-compose; main.py resolves it there.
COPY . .

EXPOSE 8000

# Default: the API. Overridden per-service in docker-compose (worker/seed).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
