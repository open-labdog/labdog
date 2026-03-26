"""Database-backed application settings with validation and caching."""

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

# Setting definitions: type, default, constraints, description
SETTING_DEFINITIONS: dict[str, dict[str, Any]] = {
    "drift.check_interval_minutes": {
        "type": "int", "default": 30, "min": 1, "max": 1440,
        "description": "Minutes between automatic drift checks",
    },
    "ssh.connect_timeout": {
        "type": "int", "default": 10, "min": 1, "max": 120,
        "description": "SSH connection timeout in seconds",
    },
    "ansible.playbook_timeout": {
        "type": "int", "default": 300, "min": 30, "max": 3600,
        "description": "Ansible playbook execution timeout in seconds",
    },
    "discovery.scan_timeout": {
        "type": "float", "default": 1.0, "min": 0.1, "max": 30.0,
        "description": "Per-host TCP scan timeout during discovery (seconds)",
    },
    "discovery.max_concurrent": {
        "type": "int", "default": 100, "min": 1, "max": 1000,
        "description": "Maximum concurrent connections during network scan",
    },
    "ssh.idle_timeout_seconds": {
        "type": "int", "default": 1800, "min": 60, "max": 86400,
        "description": "SSH terminal idle timeout before auto-disconnect (seconds)",
    },
    "logging.audit_retention_days": {
        "type": "int", "default": 90, "min": 1, "max": 3650,
        "description": "Days to retain audit log entries (0 = keep forever)",
    },
    "logging.level": {
        "type": "string", "default": "info",
        "choices": ["debug", "info", "warning", "error", "critical"],
        "description": "Application log level",
    },
    "workflow.schedule_check_interval_seconds": {
        "type": "int", "default": 60, "min": 10, "max": 300,
        "description": "How often to check for scheduled workflows (seconds)",
    },
    "workflow.snapshot_max_age_hours": {
        "type": "int", "default": 24, "min": 1, "max": 168,
        "description": "Max age in hours before orphaned snapshots are cleaned up",
    },
}

# In-process cache: {key: (value, timestamp)}
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 60  # seconds


def _cast_value(key: str, raw: str) -> int | float | str:
    defn = SETTING_DEFINITIONS.get(key)
    if not defn:
        return raw
    vtype = defn["type"]
    if vtype == "int":
        return int(raw)
    if vtype == "float":
        return float(raw)
    return raw


def _validate(key: str, value: str) -> str:
    """Validate and return the normalized value string. Raises ValueError on invalid."""
    defn = SETTING_DEFINITIONS.get(key)
    if not defn:
        raise ValueError(f"Unknown setting: {key}")

    vtype = defn["type"]
    if vtype == "int":
        try:
            v = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"{key}: expected integer, got {value!r}")
        if "min" in defn and v < defn["min"]:
            raise ValueError(f"{key}: minimum is {defn['min']}")
        if "max" in defn and v > defn["max"]:
            raise ValueError(f"{key}: maximum is {defn['max']}")
        return str(v)

    if vtype == "float":
        try:
            v = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"{key}: expected number, got {value!r}")
        if "min" in defn and v < defn["min"]:
            raise ValueError(f"{key}: minimum is {defn['min']}")
        if "max" in defn and v > defn["max"]:
            raise ValueError(f"{key}: maximum is {defn['max']}")
        return str(v)

    if vtype == "string":
        if "choices" in defn and value not in defn["choices"]:
            raise ValueError(f"{key}: must be one of {defn['choices']}")
        return value

    return value


def get_default(key: str) -> str:
    """Return the default value for a setting as a string."""
    defn = SETTING_DEFINITIONS.get(key)
    if not defn:
        raise KeyError(f"Unknown setting: {key}")
    return str(defn["default"])


async def get_setting(key: str, db: AsyncSession) -> str:
    """Get a setting value from DB, falling back to default."""
    # Check cache
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val

    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == key)
    )
    row = result.scalar_one_or_none()
    value = row if row is not None else get_default(key)
    _cache[key] = (value, time.time())
    return value


async def get_setting_typed(key: str, db: AsyncSession) -> int | float | str:
    """Get a setting value with proper type casting."""
    raw = await get_setting(key, db)
    return _cast_value(key, raw)


async def get_all_settings(db: AsyncSession) -> list[dict]:
    """Return all settings with their current values and metadata."""
    result = await db.execute(select(AppSetting))
    db_settings = {s.key: s for s in result.scalars().all()}

    settings = []
    for key, defn in SETTING_DEFINITIONS.items():
        db_row = db_settings.get(key)
        settings.append({
            "key": key,
            "value": db_row.value if db_row else str(defn["default"]),
            "value_type": defn["type"],
            "description": defn["description"],
            "default": str(defn["default"]),
            "min": defn.get("min"),
            "max": defn.get("max"),
            "choices": defn.get("choices"),
            "updated_at": db_row.updated_at.isoformat() if db_row and db_row.updated_at else None,
        })
    return settings


async def update_setting(key: str, value: str, user_id: int, db: AsyncSession) -> str:
    """Validate and update a setting. Returns the normalized value."""
    normalized = _validate(key, value)

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = normalized
        setting.updated_by = user_id
    else:
        defn = SETTING_DEFINITIONS[key]
        setting = AppSetting(
            key=key,
            value=normalized,
            value_type=defn["type"],
            description=defn["description"],
            updated_by=user_id,
        )
        db.add(setting)

    await db.commit()

    # Invalidate cache
    _cache.pop(key, None)

    return normalized


def get_setting_sync(key: str) -> str:
    """Synchronous getter for Celery tasks. Uses a one-off DB connection."""
    from app.config import settings as app_config

    # Check cache first
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val

    try:
        from sqlalchemy import create_engine
        sync_url = app_config.database.url.replace("+asyncpg", "+psycopg2").replace("postgresql+psycopg2", "postgresql")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_settings WHERE key = :key"),
                {"key": key},
            ).fetchone()
        engine.dispose()
        value = row[0] if row else get_default(key)
    except Exception:
        value = get_default(key)

    _cache[key] = (value, time.time())
    return value


def get_setting_sync_typed(key: str) -> int | float | str:
    """Synchronous typed getter for Celery tasks."""
    return _cast_value(key, get_setting_sync(key))


def invalidate_cache(key: str | None = None):
    """Clear cached settings. Pass key for specific, None for all."""
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()
