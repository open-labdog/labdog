"""Public Prometheus ``/metrics`` exporter + authed status endpoint.

Two routers, deliberately different shapes — see ``app.main`` for how
they're registered:

* ``router`` — prefix-less, owns the literal root path ``/metrics``
  (mirrors ``app.api.version``'s public-endpoint pattern: no
  ``Depends(current_active_user)`` at all). Registered *unconditionally*;
  it 404s internally when disabled rather than being conditionally
  registered, because conditional registration would let ``/metrics``
  fall through to the SPA catch-all in ``app.main`` and answer with
  ``index.html`` + HTTP 200 — silently wrong, not an error a scraper or
  operator would ever notice.
* ``status_router`` — ``prefix="/metrics"`` (mounted under ``/api`` by
  ``app.main``), authed, describes the exporter's configuration for the
  frontend's Grafana page card. No DB access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.config import settings
from app.db import get_db
from app.metrics.collector import get_snapshot, with_meta
from app.metrics.exposition import CONTENT_TYPE, render
from app.models.user import User

router = APIRouter(tags=["metrics"])
status_router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.api_route("/metrics", methods=["GET", "HEAD"], summary="Prometheus scrape endpoint")
async def get_metrics(db: AsyncSession = Depends(get_db)) -> Response:
    """Prometheus scrape endpoint. Unauthenticated by design — see
    ``app.config.MetricsConfig`` for why.

    ``settings.metrics.enabled`` is read here, **at call time**, not
    captured as a module-level constant — the pytest ``app`` fixture is
    session-scoped, so tests monkeypatch the live ``settings.metrics``
    object on an already-constructed app; a value captured at import time
    would never see that change. Explicitly declares both GET and HEAD —
    unlike plain Starlette, FastAPI's ``APIRoute`` does NOT automatically
    add HEAD support for a GET-only route, so an undeclared HEAD request
    would otherwise fall through past this route entirely to the SPA
    catch-all in ``app.main`` (which *does* declare both methods) and come
    back 200 + ``index.html`` instead of matching here at all.
    """
    if not settings.metrics.enabled:
        raise HTTPException(status_code=404, detail="Metrics exporter is disabled")
    snapshot = await get_snapshot(db)
    families = with_meta(snapshot)
    body = render(families)
    return Response(content=body, media_type=CONTENT_TYPE)


class MetricsStatus(BaseModel):
    enabled: bool
    path: str
    scrape_url: str
    cache_ttl_seconds: float
    authenticated: bool
    toml_snippet: str
    env_snippet: str


@status_router.get("/status", response_model=MetricsStatus, summary="Exporter configuration")
async def get_metrics_status(
    request: Request,
    _: User = Depends(current_active_user),
) -> MetricsStatus:
    """Describe the exporter's current configuration for the Grafana page's
    outbound-scrape card. Deliberately does not touch the database."""
    scrape_url = str(request.base_url).rstrip("/") + "/metrics"
    return MetricsStatus(
        enabled=settings.metrics.enabled,
        path="/metrics",
        scrape_url=scrape_url,
        cache_ttl_seconds=settings.metrics.cache_ttl_seconds,
        authenticated=False,
        toml_snippet="[metrics]\nenabled = true\n",
        env_snippet="LABDOG_METRICS__ENABLED=true",
    )
