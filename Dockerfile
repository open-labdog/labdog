# Barricade — single-image build
# Produces a container that runs the API, Celery worker+beat, and serves
# the static frontend — all from `python -m app`.

# ── Stage 1: Build frontend static export ─────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --silent
COPY frontend/ .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
# Output: /app/out/

# ── Stage 2: Build Python backend + install deps ──────────────────────
FROM python:3.12-slim AS backend-builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml .
COPY backend/app/ app/
RUN uv pip install --no-cache-dir --system . || pip install --no-cache-dir .

# ── Stage 3: Runtime ──────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 barricade

# Python packages from builder
COPY --from=backend-builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=backend-builder /usr/local/bin /usr/local/bin

# Backend source (app + alembic)
COPY --chown=barricade:barricade backend/app/ app/
COPY --chown=barricade:barricade backend/alembic/ alembic/
COPY --chown=barricade:barricade backend/alembic.ini alembic.ini

# Frontend static files
COPY --from=frontend-builder --chown=barricade:barricade /app/out/ /usr/lib/barricade/frontend/out/

USER barricade
EXPOSE 8000
CMD ["python", "-m", "app"]
