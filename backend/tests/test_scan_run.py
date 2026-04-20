"""Tests for app.tasks.scan_run (T4 + T6).

All DB interaction uses the real testcontainers PostgreSQL via the standard
``db`` fixture from conftest.  Network I/O (TCP scan, SSH verify) is mocked.

Because ``_async_run`` opens its own ``task_session()`` sessions (which bypass
the test's savepoint wrapper), we patch ``app.tasks.scan_run.task_session``
to return the test's already-open ``db`` session.  This keeps the data visible
to both the task and the test's SELECT assertions, and ensures rollback still
works correctly after each test.

Patch targets
-------------
``app.discovery.scanner.scan_network``   -- where the name is defined/used
``app.discovery.verify.verify_ssh``      -- same
``asyncssh.import_private_key``          -- library function; global patch is
                                            fine since tests do not run SSH

Test matrix
-----------
- auto_add=True  -- Host + HostGroupMembership rows created for verified IPs.
- auto_add=False -- PendingHost rows upserted; no duplicate on second run.
- Dedup          -- pre-existing Host IP skipped entirely (not SSH-verified).
- Error path     -- scanner raises; last_run_status ends as "error".
- Advisory lock  -- _advisory_lock_key produces a stable pg-bigint-compatible
                    value that buckets config_ids into exactly 4 slots.
- Disabled       -- early exit without touching scan logic.
- Missing        -- unknown config_id returns immediately.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from tests.conftest import create_group, create_ssh_key

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Canned test data
# ---------------------------------------------------------------------------

FAKE_HITS = [("10.0.1.1", "open"), ("10.0.1.2", "open")]


async def _mock_verify_mixed(ip, *, port, username, imported_key):
    """10.0.1.1 succeeds SSH; 10.0.1.2 fails.

    Returns the 4-tuple (success, hostname, source_ip, ssh_error) matching
    the signature of app.discovery.verify.verify_ssh.
    """
    if ip == "10.0.1.1":
        return (True, "host-a", None, None)
    return (False, None, None, "conn refused")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_scan_config(
    db,
    *,
    ssh_key_id: int,
    auto_add: bool = False,
    default_group_ids: list[int] | None = None,
    cidrs: list[str] | None = None,
    enabled: bool = True,
):
    """Insert a ScanConfig row through the ORM and flush."""
    from app.models.scan_config import ScanConfig

    cfg = ScanConfig(
        name=f"test-cfg-{uuid.uuid4().hex[:12]}",
        cidrs=cidrs or ["10.0.1.0/30"],
        ssh_key_id=ssh_key_id,
        ssh_port=22,
        ssh_user="root",
        default_group_ids=default_group_ids or [],
        interval_minutes=60,
        enabled=enabled,
        auto_add=auto_add,
    )
    db.add(cfg)
    await db.flush()
    return cfg


def _make_session_patcher(db):
    """Patch task_session to yield *db* instead of opening a new engine.

    This lets ``_async_run`` use the test's savepoint-wrapped session so all
    writes are visible to subsequent SELECT assertions in the same test and
    roll back automatically after the test ends.
    """

    @asynccontextmanager
    async def _fake_task_session():
        yield db

    return patch("app.tasks.scan_run.task_session", new=_fake_task_session)


async def _run_task(config_id: int, db) -> dict:
    """Await the async task body directly using the test's DB session."""
    from app.tasks.scan_run import _async_run

    with _make_session_patcher(db):
        return await _async_run(config_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def ssh_key(db):
    return await create_ssh_key(db)


@pytest.fixture
async def group(db):
    return await create_group(db)


# ---------------------------------------------------------------------------
# Tests: auto_add=True
# ---------------------------------------------------------------------------


class TestAutoAddTrue:
    """auto_add=True -> Host + HostGroupMembership rows for verified IPs."""

    async def test_creates_host_and_membership_for_verified_ip(self, db, ssh_key, group):
        config = await _create_scan_config(
            db,
            ssh_key_id=ssh_key.id,
            auto_add=True,
            default_group_ids=[group.id],
        )
        config_id = config.id

        with (
            patch(
                "app.discovery.scanner.scan_network",
                new=AsyncMock(return_value=FAKE_HITS),
            ),
            patch(
                "app.discovery.verify.verify_ssh",
                new=AsyncMock(side_effect=_mock_verify_mixed),
            ),
            patch("asyncssh.import_private_key", return_value=MagicMock()),
        ):
            result = await _run_task(config_id, db)

        assert result["hosts_added"] == 1   # 10.0.1.1 verified -> Host row
        assert result["hosts_pending"] == 1  # 10.0.1.2 SSH fail -> pending

        from app.models.host import Host, HostGroupMembership

        host_row = (
            await db.execute(select(Host).where(Host.ip_address == "10.0.1.1"))
        ).scalar_one_or_none()
        assert host_row is not None
        assert host_row.hostname == "host-a"

        mem = (
            await db.execute(
                select(HostGroupMembership).where(
                    HostGroupMembership.c.host_id == host_row.id,
                    HostGroupMembership.c.group_id == group.id,
                )
            )
        ).one_or_none()
        assert mem is not None

    async def test_last_run_status_ok_when_no_hits(self, db, ssh_key):
        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, auto_add=True
        )
        config_id = config.id

        with (
            patch(
                "app.discovery.scanner.scan_network",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.discovery.verify.verify_ssh",
                new=AsyncMock(return_value=(True, "h", None, None)),
            ),
            patch("asyncssh.import_private_key", return_value=MagicMock()),
        ):
            await _run_task(config_id, db)

        from app.models.scan_config import ScanConfig

        cfg = (
            await db.execute(select(ScanConfig).where(ScanConfig.id == config_id))
        ).scalar_one()
        assert cfg.last_run_status == "ok"
        assert cfg.last_run_error is None

    async def test_no_host_created_for_unverified_ip(self, db, ssh_key):
        """IP that fails SSH verify must NOT produce a Host row."""
        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, auto_add=True
        )

        with (
            patch(
                "app.discovery.scanner.scan_network",
                new=AsyncMock(return_value=[("10.0.1.99", "open")]),
            ),
            patch(
                "app.discovery.verify.verify_ssh",
                new=AsyncMock(return_value=(False, None, None, "conn refused")),
            ),
            patch("asyncssh.import_private_key", return_value=MagicMock()),
        ):
            result = await _run_task(config.id, db)

        assert result["hosts_added"] == 0

        from app.models.host import Host

        host_row = (
            await db.execute(select(Host).where(Host.ip_address == "10.0.1.99"))
        ).scalar_one_or_none()
        assert host_row is None


# ---------------------------------------------------------------------------
# Tests: auto_add=False
# ---------------------------------------------------------------------------


class TestAutoAddFalse:
    """auto_add=False -> PendingHost rows; idempotent on second run."""

    async def test_creates_pending_rows_for_all_hits(self, db, ssh_key):
        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, auto_add=False
        )
        config_id = config.id

        with (
            patch(
                "app.discovery.scanner.scan_network",
                new=AsyncMock(return_value=FAKE_HITS),
            ),
            patch(
                "app.discovery.verify.verify_ssh",
                new=AsyncMock(side_effect=_mock_verify_mixed),
            ),
            patch("asyncssh.import_private_key", return_value=MagicMock()),
        ):
            result = await _run_task(config_id, db)

        assert result["hosts_pending"] == 2
        assert result["hosts_added"] == 0

        from app.models.scan_config import PendingHost

        rows = (
            await db.execute(
                select(PendingHost).where(PendingHost.scan_config_id == config_id)
            )
        ).scalars().all()
        by_ip = {r.ip_address: r for r in rows}
        assert set(by_ip) == {"10.0.1.1", "10.0.1.2"}
        assert by_ip["10.0.1.1"].ssh_verified is True
        assert by_ip["10.0.1.2"].ssh_verified is False
        assert "conn refused" in (by_ip["10.0.1.2"].ssh_error or "")

    async def test_second_run_does_not_create_duplicates(self, db, ssh_key):
        """Running the task twice must not create duplicate PendingHost rows."""
        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, auto_add=False
        )
        config_id = config.id

        scan_mock = AsyncMock(return_value=FAKE_HITS)
        verify_mock = AsyncMock(side_effect=_mock_verify_mixed)

        for _ in range(2):
            with (
                patch("app.discovery.scanner.scan_network", new=scan_mock),
                patch("app.discovery.verify.verify_ssh", new=verify_mock),
                patch("asyncssh.import_private_key", return_value=MagicMock()),
            ):
                await _run_task(config_id, db)

        from app.models.scan_config import PendingHost

        rows = (
            await db.execute(
                select(PendingHost).where(PendingHost.scan_config_id == config_id)
            )
        ).scalars().all()
        assert len(rows) == 2  # exactly 2 -- upsert, not insert


# ---------------------------------------------------------------------------
# Tests: dedup
# ---------------------------------------------------------------------------


class TestDedup:
    """IP already in the hosts table must be skipped before SSH verify."""

    async def test_existing_host_ip_skipped(self, db, ssh_key):
        from app.models.host import Host

        # Pre-insert one of the IPs the scanner would discover.
        existing = Host(
            hostname="pre-existing.local",
            ip_address="10.0.1.1",
            ssh_key_id=ssh_key.id,
        )
        db.add(existing)
        await db.flush()

        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, auto_add=False
        )
        config_id = config.id

        verify_mock = AsyncMock(side_effect=_mock_verify_mixed)

        with (
            patch(
                "app.discovery.scanner.scan_network",
                new=AsyncMock(return_value=FAKE_HITS),
            ),
            patch("app.discovery.verify.verify_ssh", new=verify_mock),
            patch("asyncssh.import_private_key", return_value=MagicMock()),
        ):
            await _run_task(config_id, db)

        # verify_ssh must only have been called for 10.0.1.2.
        called_ips = [call.args[0] for call in verify_mock.call_args_list]
        assert "10.0.1.1" not in called_ips
        assert "10.0.1.2" in called_ips


# ---------------------------------------------------------------------------
# Tests: error path
# ---------------------------------------------------------------------------


class TestErrorPath:
    """Scanner raises -> last_run_status ends as "error"."""

    async def test_scanner_exception_marks_config_error(self, db, ssh_key):
        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, auto_add=False
        )
        config_id = config.id

        with (
            patch(
                "app.discovery.scanner.scan_network",
                new=AsyncMock(side_effect=RuntimeError("network unreachable")),
            ),
            patch("asyncssh.import_private_key", return_value=MagicMock()),
            pytest.raises(RuntimeError, match="network unreachable"),
        ):
            await _run_task(config_id, db)

        from app.models.scan_config import ScanConfig

        cfg = (
            await db.execute(select(ScanConfig).where(ScanConfig.id == config_id))
        ).scalar_one()
        assert cfg.last_run_status == "error"
        assert "network unreachable" in (cfg.last_run_error or "")


# ---------------------------------------------------------------------------
# Tests: disabled / missing config
# ---------------------------------------------------------------------------


class TestEarlyExit:
    async def test_disabled_config_returns_skipped(self, db, ssh_key):
        config = await _create_scan_config(
            db, ssh_key_id=ssh_key.id, enabled=False
        )
        scan_mock = AsyncMock(return_value=[])

        with patch("app.discovery.scanner.scan_network", new=scan_mock):
            result = await _run_task(config.id, db)

        assert result.get("skipped") is True
        scan_mock.assert_not_called()

    async def test_missing_config_returns_skipped(self, db, ssh_key):
        result = await _run_task(999_999_999, db)
        assert result.get("skipped") is True


# ---------------------------------------------------------------------------
# Tests: advisory lock key
# ---------------------------------------------------------------------------


class TestAdvisoryLock:
    """_advisory_lock_key: stable, within pg bigint range, 4-bucket grouping."""

    def test_key_is_deterministic(self):
        from app.tasks.scan_run import _advisory_lock_key

        assert _advisory_lock_key(1) == _advisory_lock_key(1)
        assert _advisory_lock_key(42) == _advisory_lock_key(42)

    def test_key_fits_pg_bigint(self):
        from app.tasks.scan_run import _advisory_lock_key

        for cid in range(20):
            key = _advisory_lock_key(cid)
            assert 0 <= key < 2**63, f"Key {key} OOB for config_id={cid}"

    def test_same_bucket_for_config_id_offset_by_4(self):
        """config_ids that differ by exactly 4 must share the same lock slot."""
        from app.tasks.scan_run import _advisory_lock_key

        for base in range(8):
            assert _advisory_lock_key(base) == _advisory_lock_key(base + 4), (
                f"Expected same key for ids {base} and {base + 4}"
            )

    def test_different_buckets_for_adjacent_ids(self):
        """Adjacent config_ids must map to different lock slots."""
        from app.tasks.scan_run import _advisory_lock_key

        for base in range(4):
            assert _advisory_lock_key(base) != _advisory_lock_key(base + 1), (
                f"Expected different keys for ids {base} and {base + 1}"
            )
