from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_db
from app.metrics import service
from app.metrics.schemas import DriftTrendSeries, SyncRateSeries
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/sync-success-rate", response_model=SyncRateSeries)
async def get_sync_success_rate(
    days: int = Query(7, ge=1, le=90),
    granularity: Literal["day", "hour"] = Query("day"),
    module: str | None = Query(None),
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> SyncRateSeries:
    """Sync success-rate chart data: bucketed totals + success rate over time."""
    since = datetime.now(UTC) - timedelta(days=days)
    return await service.get_sync_success_rate(
        db, since=since, granularity=granularity, module=module
    )


@router.get("/drift-trend", response_model=DriftTrendSeries)
async def get_drift_trend(
    days: int = Query(7, ge=1, le=90),
    granularity: Literal["day", "hour"] = Query("day"),
    module: str | None = Query(None),
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> DriftTrendSeries:
    """Drift trend chart data: bucketed drift-check counts + drift volume over time."""
    since = datetime.now(UTC) - timedelta(days=days)
    return await service.get_drift_trend(db, since=since, granularity=granularity, module=module)
