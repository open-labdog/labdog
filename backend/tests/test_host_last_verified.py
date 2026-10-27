"""BUG-99: a host is stale when LabDog has not verified it, not when it
has not been synced.

The stale-hosts panel read ``hosts.last_sync_at``, which only a sync run
that applied something writes. A host already in sync never gets one, so
every such host read "never" for good, even though LabDog collected its
state and checked it for drift that same day.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.host import SyncStatus
from app.models.host_module_status import HostModuleStatus
from tests.conftest import create_host

NOW = datetime.now(UTC)
DAYS = lambda n: NOW - timedelta(days=n)  # noqa: E731

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _row(db, host, module_type, **fields):
    db.add(HostModuleStatus(host_id=host.id, module_type=module_type, **fields))
    await db.flush()


async def _verified(db, host):
    await db.refresh(host)
    return host.last_verified_at


def _close(a, b):
    return a is not None and abs((a - b).total_seconds()) < 1


class TestLastVerifiedAt:
    async def test_a_host_nothing_has_checked_is_unverified(self, db):
        host = await create_host(db)
        assert await _verified(db, host) is None

    async def test_a_collection_that_found_it_in_sync_counts(self, db):
        """The lin-manager case: collected today, never synced."""
        host = await create_host(db)
        await _row(db, host, "services", sync_status="in_sync", collected_at=DAYS(0))
        assert _close(await _verified(db, host), DAYS(0))

    async def test_the_newest_of_collection_drift_check_and_sync_wins(self, db):
        host = await create_host(db)
        await _row(
            db,
            host,
            "services",
            sync_status="out_of_sync",
            collected_at=DAYS(40),
            last_drift_check_at=DAYS(3),
            last_sync_at=DAYS(90),
        )
        await _row(db, host, "cron", sync_status="in_sync", last_sync_at=DAYS(10))
        assert _close(await _verified(db, host), DAYS(3))

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"sync_status": "error"}, id="error-status"),
            pytest.param({"sync_status": "unknown"}, id="unreachable"),
            pytest.param({"sync_status": "collected", "error_message": "boom"}, id="error-message"),
        ],
    )
    async def test_a_failed_attempt_does_not_count(self, db, fields):
        host = await create_host(db)
        await _row(db, host, "services", collected_at=DAYS(0), **fields)
        await _row(db, host, "cron", sync_status="in_sync", collected_at=DAYS(50))
        assert _close(await _verified(db, host), DAYS(50))

    async def test_a_firewall_drift_check_counts_unless_the_host_is_in_error(self, db):
        host = await create_host(db)
        host.last_drift_check_at = DAYS(2)
        host.sync_status = SyncStatus.in_sync
        await db.flush()
        assert _close(await _verified(db, host), DAYS(2))

        host.sync_status = SyncStatus.error
        await db.flush()
        assert await _verified(db, host) is None

    async def test_an_os_facts_collection_counts(self, db):
        host = await create_host(db)
        host.os_facts_collected_at = DAYS(1)
        await db.flush()
        assert _close(await _verified(db, host), DAYS(1))


class TestTheApiReportsIt:
    async def test_list_and_summary_carry_it(self, db, superuser_client):
        host = await create_host(db)
        await _row(db, host, "services", sync_status="in_sync", collected_at=DAYS(0))

        for path in ("/api/hosts", "/api/hosts/summary"):
            resp = await superuser_client.get(path)
            assert resp.status_code == 200, resp.text
            [row] = [h for h in resp.json() if h["id"] == host.id]
            assert row["last_verified_at"] is not None, path


class TestTheBackfill:
    BACKFILL = _migration("0041_backfill_host_last_sync.py").BACKFILL

    async def test_each_host_gets_its_newest_module_sync(self, db):
        from sqlalchemy import text

        never = await create_host(db, ip="10.0.9.1")
        await _row(db, never, "services", last_sync_at=DAYS(30))
        await _row(db, never, "cron", last_sync_at=DAYS(2))
        newer = await create_host(db, ip="10.0.9.2")
        newer.last_sync_at = DAYS(1)
        await _row(db, newer, "services", last_sync_at=DAYS(5))
        untouched = await create_host(db, ip="10.0.9.3")

        await db.execute(text(self.BACKFILL))
        for h in (never, newer, untouched):
            await db.refresh(h)

        assert _close(never.last_sync_at, DAYS(2))
        assert _close(newer.last_sync_at, DAYS(1)), "the backfill must only move forward"
        assert untouched.last_sync_at is None
