"""
Tests for T5 — pending-host approve and dismiss endpoints.

Coverage:
- Approve 2 of 3 pending rows: 2 new Host rows, group memberships, 2 pending deleted, 1 remains.
- Approve when an IP already exists as a Host: skipped gracefully, pending still deleted.
- Approve with IDs belonging to a different scan_config: 0 approved (scope guard).
- Dismiss: deletes specified rows only; other configs' pending rows untouched.
- Audit log has one entry per approved host with action="discovery.approve".
- Dismiss writes a single "discovery.dismiss" audit entry with the correct count.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.host import Host, HostGroupMembership
from app.models.scan_config import PendingHost, ScanConfig
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _bypass_login_rate_limit():
    """Match test_scan_configs.py — patch the per-login rate limiter so repeated
    superuser logins across this file (and across file boundaries that share
    Redis state) don't trip the 429 threshold.
    """
    with patch("limits.strategies.MovingWindowRateLimiter.hit", return_value=True):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _create_scan_config(
    db: AsyncSession, ssh_key_id: int, group_ids: list[int]
) -> ScanConfig:
    config = ScanConfig(
        name="test-scan",
        cidrs=["10.0.0.0/24"],
        ssh_key_id=ssh_key_id,
        ssh_port=22,
        ssh_user="root",
        default_group_ids=group_ids,
        interval_minutes=60,
    )
    db.add(config)
    await db.flush()
    return config


async def _create_pending_host(
    db: AsyncSession,
    scan_config_id: int,
    ip: str,
    hostname: str | None = None,
) -> PendingHost:
    pending = PendingHost(
        scan_config_id=scan_config_id,
        ip_address=ip,
        hostname=hostname,
        ssh_verified=True,
    )
    db.add(pending)
    await db.flush()
    return pending


# ---------------------------------------------------------------------------
# Approve tests
# ---------------------------------------------------------------------------


class TestApprove:
    @pytest.mark.asyncio
    async def test_approve_subset_inserts_hosts_and_memberships(
        self, superuser_client, db: AsyncSession
    ):
        """Approve 2 of 3 pending rows: 2 Hosts, 2× memberships, 2 deleted, 1 remains."""
        ssh_key = await create_ssh_key(db)
        group1 = await create_group(db)
        group2 = await create_group(db)
        config = await _create_scan_config(db, ssh_key.id, [group1.id, group2.id])

        p1 = await _create_pending_host(db, config.id, "10.0.0.1", hostname="alpha")
        p2 = await _create_pending_host(db, config.id, "10.0.0.2", hostname="beta")
        p3 = await _create_pending_host(db, config.id, "10.0.0.3", hostname="gamma")

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/approve",
            json={"ids": [p1.id, p2.id]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["approved"] == 2
        assert data["skipped"] == 0
        assert data["skipped_ips"] == []

        # Two Host rows were created.
        hosts_result = await db.execute(
            select(Host).where(Host.ip_address.in_(["10.0.0.1", "10.0.0.2"]))
        )
        hosts = hosts_result.scalars().all()
        assert len(hosts) == 2

        # Group memberships: 2 hosts × 2 groups = 4 rows.
        host_ids = [h.id for h in hosts]
        memberships_result = await db.execute(
            select(HostGroupMembership).where(HostGroupMembership.c.host_id.in_(host_ids))
        )
        memberships = memberships_result.fetchall()
        assert len(memberships) == 4

        # Both approved pending rows were deleted; p3 remains.
        remaining_result = await db.execute(
            select(PendingHost).where(PendingHost.scan_config_id == config.id)
        )
        remaining = remaining_result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == p3.id

    @pytest.mark.asyncio
    async def test_approve_uses_hostname_from_pending(self, superuser_client, db: AsyncSession):
        """Hostname from PendingHost is propagated to the new Host row."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])

        await _create_pending_host(db, config.id, "10.1.1.1", hostname="my-router")

        pending_result = await db.execute(
            select(PendingHost).where(PendingHost.scan_config_id == config.id)
        )
        p = pending_result.scalar_one()

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/approve",
            json={"ids": [p.id]},
        )
        assert resp.status_code == 200

        host_result = await db.execute(select(Host).where(Host.ip_address == "10.1.1.1"))
        host = host_result.scalar_one()
        assert host.hostname == "my-router"

    @pytest.mark.asyncio
    async def test_approve_falls_back_to_ip_hostname(self, superuser_client, db: AsyncSession):
        """When PendingHost.hostname is None, Host.hostname becomes 'host-{ip}'."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])
        p = await _create_pending_host(db, config.id, "10.2.2.2", hostname=None)

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/approve",
            json={"ids": [p.id]},
        )
        assert resp.status_code == 200

        host_result = await db.execute(select(Host).where(Host.ip_address == "10.2.2.2"))
        host = host_result.scalar_one()
        assert host.hostname == "host-10.2.2.2"

    @pytest.mark.asyncio
    async def test_approve_skips_existing_ip_no_crash(self, superuser_client, db: AsyncSession):
        """If the IP already exists as a Host, skip it — no 409, no crash.
        The pending row is still deleted. skipped_ips reports the IP."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])

        # Pre-create a Host at 10.0.0.5
        await create_host(db, ip="10.0.0.5")
        p = await _create_pending_host(db, config.id, "10.0.0.5")

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/approve",
            json={"ids": [p.id]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["approved"] == 0
        assert data["skipped"] == 1
        assert "10.0.0.5" in data["skipped_ips"]

        # Pending row was deleted even though it was skipped.
        remaining = await db.execute(select(PendingHost).where(PendingHost.id == p.id))
        assert remaining.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_approve_cross_config_injection_returns_zero(
        self, superuser_client, db: AsyncSession
    ):
        """IDs belonging to a different scan config must be silently excluded."""
        ssh_key = await create_ssh_key(db)
        config_a = await _create_scan_config(db, ssh_key.id, [])

        # Create a second config with different name.
        config_b = ScanConfig(
            name="other-scan",
            cidrs=["172.16.0.0/24"],
            ssh_key_id=ssh_key.id,
            ssh_port=22,
            ssh_user="root",
            default_group_ids=[],
            interval_minutes=60,
        )
        db.add(config_b)
        await db.flush()

        # Pending row belongs to config_b.
        p_b = await _create_pending_host(db, config_b.id, "172.16.0.1")

        # Approve request targets config_a but supplies config_b's pending ID.
        resp = await superuser_client.post(
            f"/api/scans/{config_a.id}/pending/approve",
            json={"ids": [p_b.id]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["approved"] == 0
        assert data["skipped"] == 0

        # config_b's pending row must still exist — we didn't touch it.
        still_there = await db.execute(select(PendingHost).where(PendingHost.id == p_b.id))
        assert still_there.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_approve_writes_audit_entries(self, superuser_client, db: AsyncSession):
        """One audit entry per approved host with action='discovery.approve'."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])
        p1 = await _create_pending_host(db, config.id, "10.9.0.1", hostname="box1")
        p2 = await _create_pending_host(db, config.id, "10.9.0.2", hostname="box2")

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/approve",
            json={"ids": [p1.id, p2.id]},
        )
        assert resp.status_code == 200

        audit_result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "discovery.approve",
                AuditLog.entity_type == "scan_config",
                AuditLog.entity_id == config.id,
            )
        )
        entries = audit_result.scalars().all()
        assert len(entries) == 2

        ips_logged = {e.after_state["ip"] for e in entries}
        assert ips_logged == {"10.9.0.1", "10.9.0.2"}

    @pytest.mark.asyncio
    async def test_approve_404_on_missing_config(self, superuser_client, db: AsyncSession):
        resp = await superuser_client.post(
            "/api/scans/99999/pending/approve",
            json={"ids": [1]},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_no_group_ids_creates_host_no_memberships(
        self, superuser_client, db: AsyncSession
    ):
        """Configs with empty default_group_ids don't crash — host is created, no memberships."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])
        p = await _create_pending_host(db, config.id, "10.3.3.3")

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/approve",
            json={"ids": [p.id]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] == 1

        host_result = await db.execute(select(Host).where(Host.ip_address == "10.3.3.3"))
        host = host_result.scalar_one()

        memberships_result = await db.execute(
            select(HostGroupMembership).where(HostGroupMembership.c.host_id == host.id)
        )
        assert memberships_result.fetchall() == []


# ---------------------------------------------------------------------------
# Dismiss tests
# ---------------------------------------------------------------------------


class TestDismiss:
    @pytest.mark.asyncio
    async def test_dismiss_deletes_specified_rows(self, superuser_client, db: AsyncSession):
        """Dismiss 2 of 3 rows — 2 deleted, 1 remains."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])
        p1 = await _create_pending_host(db, config.id, "10.0.1.1")
        p2 = await _create_pending_host(db, config.id, "10.0.1.2")
        p3 = await _create_pending_host(db, config.id, "10.0.1.3")

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/dismiss",
            json={"ids": [p1.id, p2.id]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dismissed"] == 2

        remaining_result = await db.execute(
            select(PendingHost).where(PendingHost.scan_config_id == config.id)
        )
        remaining = remaining_result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == p3.id

    @pytest.mark.asyncio
    async def test_dismiss_does_not_affect_other_configs(self, superuser_client, db: AsyncSession):
        """Dismiss request scoped to config_a must not delete config_b's pending rows."""
        ssh_key = await create_ssh_key(db)
        config_a = await _create_scan_config(db, ssh_key.id, [])
        config_b = ScanConfig(
            name="other-scan-b",
            cidrs=["192.168.50.0/24"],
            ssh_key_id=ssh_key.id,
            ssh_port=22,
            ssh_user="root",
            default_group_ids=[],
            interval_minutes=60,
        )
        db.add(config_b)
        await db.flush()

        p_a = await _create_pending_host(db, config_a.id, "192.168.1.10")
        p_b = await _create_pending_host(db, config_b.id, "192.168.50.10")

        # Dismiss the config_b ID via config_a's endpoint — must be ignored.
        resp = await superuser_client.post(
            f"/api/scans/{config_a.id}/pending/dismiss",
            json={"ids": [p_a.id, p_b.id]},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only p_a is in scope for config_a; p_b should be untouched.
        assert data["dismissed"] == 1

        # config_b's row survives.
        still_there = await db.execute(select(PendingHost).where(PendingHost.id == p_b.id))
        assert still_there.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_dismiss_empty_ids_returns_zero(self, superuser_client, db: AsyncSession):
        """Dismissing an empty list is a no-op that returns 0."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/dismiss",
            json={"ids": []},
        )
        assert resp.status_code == 200
        assert resp.json()["dismissed"] == 0

    @pytest.mark.asyncio
    async def test_dismiss_writes_audit_entry(self, superuser_client, db: AsyncSession):
        """A single 'discovery.dismiss' audit entry is written with count."""
        ssh_key = await create_ssh_key(db)
        config = await _create_scan_config(db, ssh_key.id, [])
        p1 = await _create_pending_host(db, config.id, "10.5.5.1")
        p2 = await _create_pending_host(db, config.id, "10.5.5.2")

        resp = await superuser_client.post(
            f"/api/scans/{config.id}/pending/dismiss",
            json={"ids": [p1.id, p2.id]},
        )
        assert resp.status_code == 200

        audit_result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "discovery.dismiss",
                AuditLog.entity_type == "scan_config",
                AuditLog.entity_id == config.id,
            )
        )
        entries = audit_result.scalars().all()
        assert len(entries) == 1
        assert entries[0].after_state["count"] == 2

    @pytest.mark.asyncio
    async def test_dismiss_404_on_missing_config(self, superuser_client, db: AsyncSession):
        resp = await superuser_client.post(
            "/api/scans/99999/pending/dismiss",
            json={"ids": [1]},
        )
        assert resp.status_code == 404
