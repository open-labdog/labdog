from __future__ import annotations

import importlib.metadata
import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["version"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LICENSE = "AGPL-3.0-or-later"
_REPO_URL = "https://github.com/open-labdog/labdog"
_UNKNOWN_VERSION = "0.0.0+unknown"

# ---------------------------------------------------------------------------
# Build-metadata resolution (computed once at import time)
# ---------------------------------------------------------------------------


def _resolve_commit_sha() -> str | None:
    """Return the full commit SHA, or None when unavailable."""
    value = os.environ.get("LABDOG_COMMIT_SHA", "").strip()
    if value:
        return value
    try:
        from app._build_info import COMMIT_SHA  # type: ignore[import-not-found]

        value = (COMMIT_SHA or "").strip()
        if value:
            return value
    except ImportError:
        pass
    return None


def _resolve_build_date() -> str | None:
    """Return a validated ISO 8601 build-date string, or None when unavailable."""
    raw = os.environ.get("LABDOG_BUILD_DATE", "").strip()
    if not raw:
        try:
            from app._build_info import BUILD_DATE  # type: ignore[import-not-found]

            raw = (BUILD_DATE or "").strip()
        except ImportError:
            pass
    if not raw:
        return None
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return raw
    except ValueError:
        logger.warning("LABDOG_BUILD_DATE %r is not valid ISO 8601 — returning null", raw)
        return None


def _resolve_version() -> str:
    """Resolve the running version, tolerating images without dist metadata.

    Resolution order:
      1. installed package metadata — the canonical source for pip/uv
         installs (and the .deb/.rpm packages);
      2. ``LABDOG_VERSION`` env override;
      3. the ``VERSION`` file bundled at the app root — the container runs
         from source with deps installed via ``--no-emit-project``, so the
         ``labdog-backend`` ``.dist-info`` is absent and step 1 fails there;
      4. ``app._build_info.VERSION`` if a build stamped one;
      5. a last-resort sentinel.

    This must never raise: it feeds the public ``/api/version`` endpoint
    that the container healthcheck depends on, so a missing distribution
    must degrade to a sentinel, not a 500.
    """
    try:
        return importlib.metadata.version("labdog-backend")
    except importlib.metadata.PackageNotFoundError:
        pass

    env = os.environ.get("LABDOG_VERSION", "").strip()
    if env:
        return env

    # version.py lives at <root>/app/api/version.py; the image copies the
    # repo-root VERSION file to <root>/VERSION (WORKDIR) alongside `app/`.
    try:
        text = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass

    try:
        from app._build_info import VERSION as build_version  # type: ignore[import-not-found]

        text = (build_version or "").strip()
        if text:
            return text
    except ImportError:
        pass

    logger.warning(
        "labdog-backend version unresolved (no dist metadata, LABDOG_VERSION, "
        "VERSION file, or _build_info) — reporting %r",
        _UNKNOWN_VERSION,
    )
    return _UNKNOWN_VERSION


_COMMIT_SHA: str | None = _resolve_commit_sha()
_BUILD_DATE: str | None = _resolve_build_date()
_VERSION: str = _resolve_version()

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class VersionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    commit_sha: str | None
    commit_sha_short: str | None
    build_date: str | None
    license: str
    repo_url: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/version", response_model=VersionResponse, summary="Runtime build metadata")
async def get_version() -> VersionResponse:
    """Return runtime build metadata for the About section.

    This endpoint is intentionally public — no authentication required.
    """
    short = _COMMIT_SHA[:7] if _COMMIT_SHA else None
    return VersionResponse(
        version=_VERSION,
        commit_sha=_COMMIT_SHA,
        commit_sha_short=short,
        build_date=_BUILD_DATE,
        license=_LICENSE,
        repo_url=_REPO_URL,
    )
