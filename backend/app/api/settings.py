"""API endpoints for managing application settings (superuser only)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_superuser
from app.db import get_db
from app.models.user import User
from app.settings_service import (
    SETTING_DEFINITIONS,
    get_all_settings,
    update_setting,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    value: str


@router.get("")
async def list_settings(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """List all application settings with current values and metadata."""
    return await get_all_settings(db)


@router.patch("/{key:path}")
async def patch_setting(
    key: str,
    body: SettingUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Update a single setting. Validates type and constraints."""
    if key not in SETTING_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")

    try:
        normalized = await update_setting(key, body.value, user.id, db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Handle side effects
    await _apply_side_effects(key, normalized)

    return {"key": key, "value": normalized}


async def _apply_side_effects(key: str, value: str):
    """Apply runtime side effects when certain settings change."""
    if key == "drift.check_interval_minutes":
        _reregister_drift_schedules(int(value))
    elif key == "logging.level":
        logging.getLogger().setLevel(value.upper())
        logger.info("Log level changed to %s", value)


def _reregister_drift_schedules(interval_minutes: int):
    """Re-register all RedBeat drift schedules with a new interval."""
    try:
        from celery.schedules import schedule
        from redbeat import RedBeatSchedulerEntry

        from app.tasks import celery_app

        interval = schedule(run_every=interval_minutes * 60)
        task_names = [
            ("check-drift-periodic", "app.tasks.drift.check_all_drift"),
            ("check-service-drift-periodic", "app.tasks.service_drift.check_all_service_drift"),
            ("check-hosts-drift-periodic", "app.tasks.hosts_drift.check_all_hosts_drift"),
            ("check-package-drift-periodic", "app.tasks.package_drift.check_all_package_drift"),
            ("check-resolver-drift-periodic", "app.tasks.resolver_drift.check_all_resolver_drift"),
            ("check-user-drift-periodic", "app.tasks.user_drift.check_all_user_drift"),
            ("check-cron-drift-periodic", "app.tasks.cron_drift.check_all_cron_drift"),
        ]
        for name, task in task_names:
            entry = RedBeatSchedulerEntry(
                name=name,
                task=task,
                schedule=interval,
                app=celery_app,
            )
            entry.save()
        logger.info("Drift check schedules updated to every %d minutes", interval_minutes)
    except Exception as e:
        logger.warning("Failed to update drift schedules: %s", e)
