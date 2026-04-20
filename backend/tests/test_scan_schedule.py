"""Unit tests for app.tasks.scan_schedule.

Tests cover _is_due() logic directly (no DB, no Redis, no Celery broker
required) and verify that check_scheduled_scans dispatches exactly one
celery_app.send_task call per due config.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config(
    *,
    interval_minutes: int | None = None,
    cron_expression: str | None = None,
    last_run_at: datetime | None = None,
    enabled: bool = True,
    config_id: int = 1,
) -> SimpleNamespace:
    """Build a lightweight stand-in for a ScanConfig ORM object."""
    return SimpleNamespace(
        id=config_id,
        enabled=enabled,
        interval_minutes=interval_minutes,
        cron_expression=cron_expression,
        last_run_at=last_run_at,
    )


# Import under test — must come after env vars are set (conftest handles that)
from app.tasks.scan_schedule import _is_due  # noqa: E402

# ---------------------------------------------------------------------------
# _is_due parametrized tests
# ---------------------------------------------------------------------------


NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "config, now, expected, description",
    [
        # --- interval_minutes branch ---
        (
            make_config(interval_minutes=30, last_run_at=None),
            NOW,
            True,
            "interval: never run → due immediately",
        ),
        (
            make_config(interval_minutes=30, last_run_at=NOW - timedelta(minutes=31)),
            NOW,
            True,
            "interval: last run 31 min ago, interval 30 min → due",
        ),
        (
            make_config(interval_minutes=30, last_run_at=NOW - timedelta(minutes=30)),
            NOW,
            True,
            "interval: last run exactly 30 min ago → due (boundary inclusive)",
        ),
        (
            make_config(interval_minutes=30, last_run_at=NOW - timedelta(minutes=29)),
            NOW,
            False,
            "interval: last run 29 min ago, interval 30 min → not due",
        ),
        (
            make_config(interval_minutes=30, last_run_at=NOW - timedelta(seconds=1)),
            NOW,
            False,
            "interval: just ran one second ago → not due",
        ),
        # --- cron_expression branch ---
        (
            # Cron fires every minute; last_run_at is 2 min in the past → due
            make_config(
                cron_expression="* * * * *",
                last_run_at=NOW - timedelta(minutes=2),
            ),
            NOW,
            True,
            "cron: every-minute cron, last run 2 min ago → due",
        ),
        (
            # Cron fires at noon daily; last_run_at yesterday → next fire = today noon = NOW → due
            make_config(
                cron_expression="0 12 * * *",
                last_run_at=NOW - timedelta(days=1),
            ),
            NOW,
            True,
            "cron: daily-noon cron, last run yesterday, now = noon → due",
        ),
        (
            # Cron fires at noon daily; last_run_at = NOW (just ran) → next fire tomorrow → not due
            make_config(
                cron_expression="0 12 * * *",
                last_run_at=NOW,
            ),
            NOW,
            False,
            "cron: daily-noon cron, just ran → not due",
        ),
        (
            # Never run before → base = now - 1 min; next fire within a minute → due
            make_config(
                cron_expression="* * * * *",
                last_run_at=None,
            ),
            NOW,
            True,
            "cron: every-minute, never run → due on first tick",
        ),
        # --- edge cases: both/neither set ---
        (
            make_config(interval_minutes=None, cron_expression=None),
            NOW,
            False,
            "neither schedule field set → False (defensive)",
        ),
        (
            make_config(interval_minutes=30, cron_expression="* * * * *"),
            NOW,
            False,
            "both schedule fields set → False (defensive)",
        ),
    ],
)
def test_is_due(config, now, expected, description):
    result = _is_due(config, now)
    assert result is expected, f"FAILED: {description!r} — got {result!r}, want {expected!r}"


# ---------------------------------------------------------------------------
# check_scheduled_scans dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_dispatches_due_configs():
    """_check() dispatches send_task once per due config and skips non-due ones."""
    from app.tasks.scan_schedule import _check

    due_config = make_config(interval_minutes=30, last_run_at=None, config_id=10)
    not_due_config = make_config(
        interval_minutes=30,
        last_run_at=NOW - timedelta(minutes=5),
        config_id=11,
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [due_config, not_due_config]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_task_session():
        yield mock_db

    with (
        patch("app.db.task_session", fake_task_session),
        patch("app.tasks.scan_schedule.celery_app") as mock_celery,
        patch("app.tasks.scan_schedule.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = NOW

        count = await _check()

    assert count == 1
    mock_celery.send_task.assert_called_once_with("scans.run_config", args=[10])


@pytest.mark.asyncio
async def test_check_dispatches_multiple_due_configs():
    """_check() dispatches for every due config in the result set."""
    from app.tasks.scan_schedule import _check

    configs = [
        make_config(interval_minutes=10, last_run_at=NOW - timedelta(minutes=20), config_id=i)
        for i in range(3)
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = configs

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_task_session():
        yield mock_db

    with (
        patch("app.db.task_session", fake_task_session),
        patch("app.tasks.scan_schedule.celery_app") as mock_celery,
        patch("app.tasks.scan_schedule.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = NOW

        count = await _check()

    assert count == 3
    assert mock_celery.send_task.call_count == 3


@pytest.mark.asyncio
async def test_check_no_due_configs():
    """_check() dispatches nothing when no configs are due."""
    from app.tasks.scan_schedule import _check

    not_due = make_config(interval_minutes=60, last_run_at=NOW - timedelta(minutes=5), config_id=99)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [not_due]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_task_session():
        yield mock_db

    with (
        patch("app.db.task_session", fake_task_session),
        patch("app.tasks.scan_schedule.celery_app") as mock_celery,
        patch("app.tasks.scan_schedule.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = NOW

        count = await _check()

    assert count == 0
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
async def test_check_skips_bad_config_without_crashing():
    """A config that raises inside the loop should not abort remaining configs."""
    from app.tasks.scan_schedule import _check

    good_config = make_config(interval_minutes=10, last_run_at=None, config_id=1)
    # bad_config has a cron_expression that will cause croniter to raise on parse
    bad_config = make_config(cron_expression="not a valid cron !!!", last_run_at=None, config_id=2)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [bad_config, good_config]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_task_session():
        yield mock_db

    with (
        patch("app.db.task_session", fake_task_session),
        patch("app.tasks.scan_schedule.celery_app") as mock_celery,
        patch("app.tasks.scan_schedule.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = NOW

        count = await _check()

    # good_config is still dispatched; bad_config is swallowed
    assert count == 1
    mock_celery.send_task.assert_called_once_with("scans.run_config", args=[1])


# ---------------------------------------------------------------------------
# Import-level smoke test
# ---------------------------------------------------------------------------


def test_check_scheduled_scans_is_importable():
    """The Celery task should be importable without errors."""
    from app.tasks.scan_schedule import check_scheduled_scans

    assert callable(check_scheduled_scans)
    # Verify the task was registered with the expected name
    assert check_scheduled_scans.name == "scans.check_scheduled"
