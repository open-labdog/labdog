from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import require
from app.db import get_db
from app.metrics import overview, service
from app.metrics.schemas import (
    CACertExpiry,
    DriftCoverage,
    DriftTrendSeries,
    ModuleStatusCount,
    StuckOperations,
    SyncRateSeries,
)
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/sync-success-rate", response_model=SyncRateSeries)
async def get_sync_success_rate(
    days: int = Query(7, ge=1, le=90),
    granularity: Literal["day", "hour"] = Query("day"),
    module: str | None = Query(None),
    group_id: int | None = Query(None, description="only syncs of this group's members"),
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
) -> SyncRateSeries:
    """Sync success-rate chart data: bucketed totals + success rate over time."""
    since = datetime.now(UTC) - timedelta(days=days)
    return await service.get_sync_success_rate(
        db, since=since, granularity=granularity, module=module, group_id=group_id
    )


@router.get("/drift-trend", response_model=DriftTrendSeries)
async def get_drift_trend(
    days: int = Query(7, ge=1, le=90),
    granularity: Literal["day", "hour"] = Query("day"),
    module: str | None = Query(None),
    group_id: int | None = Query(None, description="only checks of this group's members"),
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
) -> DriftTrendSeries:
    """Drift trend chart data: bucketed drift-check counts + drift volume over time."""
    since = datetime.now(UTC) - timedelta(days=days)
    return await service.get_drift_trend(
        db, since=since, granularity=granularity, module=module, group_id=group_id
    )


@router.get("/drift-coverage", response_model=DriftCoverage)
async def get_drift_coverage(
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
) -> DriftCoverage:
    """How many hosts have any drift checking switched on.

    The dashboard uses this to say *why* a drift surface is empty. Without
    it "no drift found" and "nothing is being checked" render identically,
    which is the state a fresh install is in permanently: every flag
    defaults to off, and the periodic sweep runs on schedule finding no
    candidates.
    """
    return await service.get_drift_coverage(db)


@router.get("/stuck-operations", response_model=StuckOperations)
async def get_stuck_operations(
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
) -> StuckOperations:
    """Syncs and action runs still running past the stale threshold, each
    with the hosts it holds and how many operations are queued behind it."""
    return await overview.get_stuck_operations(db)


@router.get("/module-status", response_model=list[ModuleStatusCount])
async def get_module_status(
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
) -> list[ModuleStatusCount]:
    """Hosts per module and sync status, across the fleet."""
    return await overview.get_module_status_counts(db)


@router.get("/ca-cert-expiries", response_model=list[CACertExpiry])
async def get_ca_cert_expiries(
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
) -> list[CACertExpiry]:
    """Deployed CA certificates with a known expiry, soonest first."""
    return await overview.get_ca_cert_expiries(db)
