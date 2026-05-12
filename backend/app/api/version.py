from __future__ import annotations

import importlib.metadata
import logging
import os
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["version"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LICENSE = "AGPL-3.0-or-later"
_REPO_URL = "https://github.com/open-labdog/labdog"

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


_COMMIT_SHA: str | None = _resolve_commit_sha()
_BUILD_DATE: str | None = _resolve_build_date()

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
    version = importlib.metadata.version("labdog-backend")
    short = _COMMIT_SHA[:7] if _COMMIT_SHA else None
    return VersionResponse(
        version=version,
        commit_sha=_COMMIT_SHA,
        commit_sha_short=short,
        build_date=_BUILD_DATE,
        license=_LICENSE,
        repo_url=_REPO_URL,
    )
