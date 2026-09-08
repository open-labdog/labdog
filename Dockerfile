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
COPY backend/pyproject.toml backend/uv.lock ./
COPY VERSION .
# Install the exact locked dependency versions (reproducible builds). The app
# package itself isn't installed — the runtime stage runs it from the copied
# source (WORKDIR /app, `python -m app`), so only the deps need to be present.
#
# --extra agent pulls the Claude Agent SDK, which is what drives an
# agentic session against a Claude *subscription* rather than metered API
# credit. It is optional in pyproject because its wheel is
# platform-specific and carries a ~130 MB Claude Code binary that has no
# business in the .deb/.rpm artefacts — but the container image is
# exactly where it belongs.
RUN uv export --frozen --no-emit-project --extra agent --format requirements-txt -o /tmp/req.txt \
    && uv pip install --no-cache-dir --system -r /tmp/req.txt

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

# Install the runtime tools we need, then fully upgrade every base
# package so Debian security fixes are applied (e.g. krb5 libs pulled in
# transitively by git/openssh-client). A blanket upgrade avoids the
# whack-a-mole of naming each CVE'd package as Trivy/Grype flag them.
#
# BUILD_DATE is declared here (and re-declared later for the version
# stamp) solely to bust this layer's BuildKit cache. CI builds with
# `cache-from/to: type=gha`, so without a per-build input the cached apt
# layer keeps serving packages from whenever the cache was populated —
# security fixes published afterwards (e.g. libssh2 +deb13u1) never land
# and Trivy gates the stale image. Referencing the per-build BUILD_DATE
# forces apt to refresh on every CI build. Local builds (no BUILD_DATE)
# keep the cached layer, which is fine — they aren't security-gated.
ARG BUILD_DATE=""
RUN echo "apt security refresh @ ${BUILD_DATE}" \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssh-client git tini \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 labdog \
    && mkdir -p /var/lib/labdog/packs /var/lib/labdog/claude-cli \
    && chown -R labdog:labdog /var/lib/labdog

# Python packages from builder
COPY --from=backend-builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=backend-builder /usr/local/bin /usr/local/bin
# uv / uvx are install-time only — drop them from the runtime image.
# Their bundled rustls-webpki has produced HIGH-severity advisories
# (e.g. GHSA-82j2-j2ch-gfr8) that we'd otherwise need to track in
# .trivyignore for a binary that's never actually invoked at runtime.
RUN rm -f /usr/local/bin/uv /usr/local/bin/uvx

# Claude Code CLI for the `claude_cli` AI provider.
#
# The image used to fetch this separately from Anthropic's apt repo. It no
# longer does: the Claude Agent SDK installed above ships its own copy at
# a version it was built against and pins in _cli_version.py, and the SDK
# prefers that bundled binary over anything on PATH. Fetching a second one
# would put two ~130 MB binaries in the image and reintroduce exactly the
# drift the SDK removes — the apt copy floats while the SDK's is pinned,
# so the two backends could end up on different Claude Code versions.
#
# A symlink lets the single-shot `claude_cli` provider keep resolving
# `claude` from PATH with no code change, while both backends stay on one
# binary that uv.lock pins transitively.
#
# LabDog points the CLI at /var/lib/labdog/claude-cli via
# CLAUDE_CONFIG_DIR rather than letting it use $HOME, so a stored login
# can never shadow the token configured in the UI — see
# app/ai/providers/claude_cli.py and app/ai/agent_sdk/environment.py.
#
# Placed *after* the backend-builder COPY, which lands its whole
# /usr/local/bin here: COPY merges rather than replaces, so creating this
# earlier would survive only for as long as that stage never ships a file
# by this name.
RUN set -eu; \
    bundled="$(python -c 'import claude_agent_sdk,pathlib,sys; sys.stdout.write(str(pathlib.Path(claude_agent_sdk.__file__).parent/"_bundled"/"claude"))')"; \
    test -x "$bundled" || { echo "Agent SDK shipped no bundled claude at $bundled" >&2; exit 1; }; \
    ln -sf "$bundled" /usr/local/bin/claude; \
    /usr/local/bin/claude --version

# Backend source (app + alembic). ``backend/app/ansible`` is excluded
# from the in-repo copy via .dockerignore so the build-time clone
# (next COPY) is the only source for the bundled pack.
COPY --chown=labdog:labdog backend/app/ app/
COPY --chown=labdog:labdog backend/alembic/ alembic/
COPY --chown=labdog:labdog backend/alembic.ini alembic.ini
# The image runs from source with deps installed via --no-emit-project, so the
# labdog-backend .dist-info is absent and importlib.metadata can't report the
# version. Ship the VERSION file at the app root so /api/version (and thus the
# healthcheck) resolves it — see app/api/version.py:_resolve_version().
COPY --chown=labdog:labdog VERSION VERSION

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
# /health/ready, not /api/version: the old probe only proved that uvicorn
# was answering. It never touched the database, Redis or the Celery
# children, so a container whose worker had died stayed "healthy" forever
# while nothing executed a single task (BUG-72). start-period covers
# startup migrations and the worker boot; a 503 names the failing
# component in its body.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request, sys; r = urllib.request.urlopen('http://localhost:8000/health/ready', timeout=4); sys.exit(0 if r.status == 200 else 1)" || exit 1

# BUG-55: an init as PID 1, so orphaned grandchildren get reaped.
#
# The app shells out to git; git spawns ssh for SSH remotes and exits
# first; the orphaned ssh re-parents to PID 1. When PID 1 was `python -m
# app` — which never wait()s on children it did not spawn — each one
# stayed a zombie holding a task slot for the life of the container. One
# instance reached 115 of them (114 ssh, 1 git) at roughly 26 a day, and
# the count only ever grows: the end state is a host that cannot fork(),
# which takes a reboot to clear.
#
# Deployments could set `init: true` themselves, and the one that found
# this did. That is the wrong place for it — the published image is run
# by people who will not know to. tini forwards signals to the app, so
# SIGTERM shutdown is unchanged.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app"]
