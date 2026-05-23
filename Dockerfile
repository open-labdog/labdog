# LabDog — single-image build
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
COPY VERSION .
COPY backend/app/ app/
RUN uv pip install --no-cache-dir --system . || pip install --no-cache-dir .

# ── Stage 2b: Fetch bundled action pack at a pinned ref ───────────────
# The bundled pack used to be a byte-identical mirror committed at
# backend/app/ansible/. We replaced that with a build-time clone so the
# repo stays clean and the bundle's provenance is a single git ref
# tracked in the top-level LABDOG_PLAYBOOKS_REF file.
#
# All actual clone logic lives in scripts/fetch-bundled-pack.sh -- one
# source of truth shared with packaging/Makefile, dev/dev.sh, and the
# CI workflow.
#
# CI passes LABDOG_PLAYBOOKS_REF / LABDOG_PLAYBOOKS_REPO via build-args
# (sourced from the repo-root LABDOG_PLAYBOOKS_REF file + the workflow's
# own configuration). A local ``docker build`` without overrides uses
# whatever defaults are pinned below.
FROM alpine/git:v2.45.2 AS bundled-pack-fetcher
ARG LABDOG_PLAYBOOKS_REPO=https://github.com/open-labdog/labdog-playbooks.git
ARG LABDOG_PLAYBOOKS_REF=main
ENV LABDOG_PLAYBOOKS_REPO=${LABDOG_PLAYBOOKS_REPO}
ENV LABDOG_PLAYBOOKS_REF=${LABDOG_PLAYBOOKS_REF}
COPY scripts/fetch-bundled-pack.sh /usr/local/bin/fetch-bundled-pack
RUN chmod +x /usr/local/bin/fetch-bundled-pack \
    && /usr/local/bin/fetch-bundled-pack /bundle

# ── Stage 3: Runtime ──────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends openssl libssl3t64 openssl-provider-legacy \
    && apt-get install -y --no-install-recommends openssh-client git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 labdog \
    && mkdir -p /var/lib/labdog/packs \
    && chown -R labdog:labdog /var/lib/labdog

# Python packages from builder
COPY --from=backend-builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=backend-builder /usr/local/bin /usr/local/bin
# uv / uvx are install-time only — drop them from the runtime image.
# Their bundled rustls-webpki has produced HIGH-severity advisories
# (e.g. GHSA-82j2-j2ch-gfr8) that we'd otherwise need to track in
# .trivyignore for a binary that's never actually invoked at runtime.
RUN rm -f /usr/local/bin/uv /usr/local/bin/uvx

# Backend source (app + alembic). ``backend/app/ansible`` is excluded
# from the in-repo copy via .dockerignore so the build-time clone
# (next COPY) is the only source for the bundled pack.
COPY --chown=labdog:labdog backend/app/ app/
COPY --chown=labdog:labdog backend/alembic/ alembic/
COPY --chown=labdog:labdog backend/alembic.ini alembic.ini

# Bundled action pack: cloned from labdog-playbooks at build time at
# the LABDOG_PLAYBOOKS_REF pinned in the repo (see Stage 2b above).
COPY --from=bundled-pack-fetcher --chown=labdog:labdog /bundle/ app/ansible/

# Frontend static files
COPY --from=frontend-builder --chown=labdog:labdog /app/out/ /usr/lib/labdog/frontend/out/

# Build metadata. CI passes both via --build-arg in the build-image and
# build-test-image jobs; a local `docker build` without them yields empty
# values and /api/version reports a "dev build".
ARG GIT_SHA=""
ARG BUILD_DATE=""
ENV LABDOG_COMMIT_SHA=$GIT_SHA \
    LABDOG_BUILD_DATE=$BUILD_DATE

USER labdog
EXPOSE 8000

# Lets container orchestrators (Docker, k8s, compose) detect a stuck
# process. /api/version is a no-auth endpoint that exercises the
# FastAPI app at a minimum. Python is used instead of curl to avoid
# adding an extra runtime dep -- python is already in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request, sys; r = urllib.request.urlopen('http://localhost:8000/api/version', timeout=3); sys.exit(0 if r.status == 200 else 1)" || exit 1

CMD ["python", "-m", "app"]
