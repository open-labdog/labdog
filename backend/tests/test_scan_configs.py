"""
Tests for T2 (ScanConfig API) and the schema-level parts of T6 (rate-limit validation).

All tests that touch the DB are marked as integration tests.
Schema-validation tests are pure unit tests (no DB, no async).
"""

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.scan_config import PendingHost
from app.schemas.scans import ScanConfigCreate, ScanConfigUpdate
from tests.conftest import create_ssh_key

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _bypass_login_rate_limit():
    """Allow unlimited login attempts so tests don't hit Redis 429s.

    Patches ``MovingWindowRateLimiter.hit`` at the ``limits`` library level
    because the per-login limiter is built at ``create_app()`` import time
    and holds a closure reference that cannot be replaced after the fact.
    """
    with patch("limits.strategies.MovingWindowRateLimiter.hit", return_value=True):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_PAYLOAD = {
    "name": "test-scan",
    "cidrs": ["192.168.1.0/24"],
    "ssh_key_id": 1,  # overridden per-test that needs DB
    "interval_minutes": 60,
}


def _payload(**overrides):
    return {**BASE_PAYLOAD, **overrides}


# ---------------------------------------------------------------------------
# Pure schema validation (no DB, no async)
# ---------------------------------------------------------------------------


class TestScanConfigSchemaValidation:
    """Pure unit tests — no database required."""

    def _base(self, **overrides) -> dict:
        return {
            "name": "my-scan",
            "cidrs": ["10.0.0.0/24"],
            "ssh_key_id": 1,
            "interval_minutes": 60,
            **overrides,
        }

    # -- Schedule XOR --

    def test_valid_interval(self):
        m = ScanConfigCreate(**self._base(interval_minutes=60))
        assert m.interval_minutes == 60
        assert m.cron_expression is None

    def test_valid_cron(self):
        m = ScanConfigCreate(**self._base(interval_minutes=None, cron_expression="0 * * * *"))
        assert m.cron_expression == "0 * * * *"
        assert m.interval_minutes is None

    def test_both_schedule_fields_rejected(self):
        with pytest.raises(ValidationError, match="Exactly one"):
            ScanConfigCreate(**self._base(interval_minutes=60, cron_expression="0 * * * *"))

    def test_neither_schedule_field_rejected(self):
        with pytest.raises(ValidationError, match="Exactly one"):
            ScanConfigCreate(**self._base(interval_minutes=None, cron_expression=None))

    # -- CIDR validation --

    def test_invalid_cidr_rejected(self):
        with pytest.raises(ValidationError, match="Invalid CIDR"):
            ScanConfigCreate(**self._base(cidrs=["not-a-cidr"]))

    def test_empty_cidrs_rejected(self):
        with pytest.raises(ValidationError, match="At least one CIDR"):
            ScanConfigCreate(**self._base(cidrs=[]))

    def test_host_bits_cidr_accepted(self):
        # strict=False means 192.168.1.5/24 is accepted (normalised)
        m = ScanConfigCreate(**self._base(cidrs=["192.168.1.5/24"]))
        assert m.cidrs == ["192.168.1.5/24"]

    # -- Cron validation --

    def test_invalid_cron_rejected(self):
        with pytest.raises(ValidationError, match="Invalid cron expression"):
            ScanConfigCreate(**self._base(interval_minutes=None, cron_expression="not-a-cron"))

    def test_five_field_cron_accepted(self):
        m = ScanConfigCreate(**self._base(interval_minutes=None, cron_expression="*/15 * * * *"))
        assert m.cron_expression == "*/15 * * * *"

    # -- interval_minutes bounds --

    def test_interval_below_minimum_rejected(self):
        with pytest.raises(ValidationError, match="1 and 10080"):
            ScanConfigCreate(**self._base(interval_minutes=0))

    def test_interval_above_maximum_rejected(self):
        with pytest.raises(ValidationError, match="1 and 10080"):
            ScanConfigCreate(**self._base(interval_minutes=10_081))

    def test_interval_minimum_boundary_accepted(self):
        m = ScanConfigCreate(**self._base(interval_minutes=1, cidrs=["10.0.0.0/31"]))
        assert m.interval_minutes == 1

    def test_interval_maximum_boundary_accepted(self):
        m = ScanConfigCreate(**self._base(interval_minutes=10_080, cidrs=["10.0.0.0/24"]))
        assert m.interval_minutes == 10_080

    # -- T6 rate-limit validation --

    def test_rate_limit_exceeded_with_interval(self):
        # /8 = 16,777,216 addresses / 60 min = 279,620 IPs/min — way over 100k
        with pytest.raises(ValidationError, match="Scan footprint too large"):
            ScanConfigCreate(**self._base(cidrs=["10.0.0.0/8"], interval_minutes=60))

    def test_rate_limit_exceeded_with_cron(self):
        # /8 = 16,777,216 / 60 (cron proxy) = 279,620 IPs/min
        with pytest.raises(ValidationError, match="Scan footprint too large"):
            ScanConfigCreate(
                **self._base(
                    cidrs=["10.0.0.0/8"],
                    interval_minutes=None,
                    cron_expression="0 * * * *",
                )
            )

    def test_rate_limit_just_under_threshold_accepted(self):
        # 100,000 IPs/min exactly is the cap; /15 = 131,072 / 2 min = 65,536 IPs/min
        m = ScanConfigCreate(**self._base(cidrs=["10.0.0.0/15"], interval_minutes=2))
        assert m.cidrs

    def test_rate_limit_multiple_cidrs_summed(self):
        # Three /24s = 3*256 = 768 at interval 1 — well under 100k
        m = ScanConfigCreate(
            **self._base(
                cidrs=["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"],
                interval_minutes=1,
            )
        )
        assert len(m.cidrs) == 3

    def test_rate_limit_multiple_large_cidrs_rejected(self):
        # Two /8s at interval_minutes=1 — definitely over 100k/min
        with pytest.raises(ValidationError, match="Scan footprint too large"):
            ScanConfigCreate(
                **self._base(
                    cidrs=["10.0.0.0/8", "172.16.0.0/8"],
                    interval_minutes=1,
                )
            )

    # -- Update schema --

    def test_update_both_schedule_fields_simultaneously_rejected(self):
        with pytest.raises(ValidationError, match="Exactly one"):
            ScanConfigUpdate(interval_minutes=30, cron_expression="0 * * * *")

    def test_update_partial_fields_accepted(self):
        u = ScanConfigUpdate(name="renamed")
        assert u.name == "renamed"
        assert u.interval_minutes is None

    def test_update_invalid_cidr_rejected(self):
        with pytest.raises(ValidationError, match="Invalid CIDR"):
            ScanConfigUpdate(cidrs=["bad"])


# ---------------------------------------------------------------------------
# API integration tests (async, real DB)
# ---------------------------------------------------------------------------


class TestScanConfigsAPI:
    async def test_list_empty(self, superuser_client):
        resp = await superuser_client.get("/api/scans")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_with_interval(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "lan-scan",
                "cidrs": ["192.168.1.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "lan-scan"
        assert data["interval_minutes"] == 60
        assert data["cron_expression"] is None
        assert data["cidrs"] == ["192.168.1.0/24"]

    async def test_create_with_cron(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "nightly-scan",
                "cidrs": ["10.0.0.0/24"],
                "ssh_key_id": key.id,
                "cron_expression": "0 2 * * *",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["cron_expression"] == "0 2 * * *"
        assert data["interval_minutes"] is None

    async def test_create_rejects_both_schedule_fields(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "bad-scan",
                "cidrs": ["10.0.0.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
                "cron_expression": "0 * * * *",
            },
        )
        assert resp.status_code == 422

    async def test_create_rejects_no_schedule_fields(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "no-schedule-scan",
                "cidrs": ["10.0.0.0/24"],
                "ssh_key_id": key.id,
            },
        )
        assert resp.status_code == 422

    async def test_create_rejects_invalid_cidr(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "bad-cidr-scan",
                "cidrs": ["not-a-cidr"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        assert resp.status_code == 422

    async def test_create_rejects_invalid_cron(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "bad-cron-scan",
                "cidrs": ["10.0.0.0/24"],
                "ssh_key_id": key.id,
                "cron_expression": "not-cron",
            },
        )
        assert resp.status_code == 422

    async def test_create_rejects_rate_limit_exceeded(self, superuser_client, db):
        key = await create_ssh_key(db)
        # /8 = 16.7M addresses / 1 min — way over 100k/min
        resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "huge-scan",
                "cidrs": ["10.0.0.0/8"],
                "ssh_key_id": key.id,
                "interval_minutes": 1,
            },
        )
        assert resp.status_code == 422
        assert "footprint" in resp.json()["detail"][0]["msg"].lower()

    async def test_get_detail(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "detail-scan",
                "cidrs": ["10.0.0.0/28"],
                "ssh_key_id": key.id,
                "interval_minutes": 120,
            },
        )
        assert create_resp.status_code == 201
        config_id = create_resp.json()["id"]

        resp = await superuser_client.get(f"/api/scans/{config_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == config_id
        assert data["pending_count"] == 0

    async def test_get_nonexistent_returns_404(self, superuser_client):
        resp = await superuser_client.get("/api/scans/99999")
        assert resp.status_code == 404

    async def test_update_fields(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "update-scan",
                "cidrs": ["10.0.1.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        config_id = create_resp.json()["id"]

        resp = await superuser_client.put(
            f"/api/scans/{config_id}",
            json={"name": "updated-scan", "enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated-scan"
        assert data["enabled"] is False

    async def test_update_switch_schedule_to_cron(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "switch-sched-scan",
                "cidrs": ["10.0.2.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        config_id = create_resp.json()["id"]

        resp = await superuser_client.put(
            f"/api/scans/{config_id}",
            json={"cron_expression": "0 3 * * *"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cron_expression"] == "0 3 * * *"
        assert data["interval_minutes"] is None

    async def test_delete_cascades_pending_hosts(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "cascade-scan",
                "cidrs": ["10.0.3.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 120,
            },
        )
        config_id = create_resp.json()["id"]

        # Add a pending host directly via ORM
        pending = PendingHost(
            scan_config_id=config_id,
            ip_address="10.0.3.5",
            ssh_verified=False,
        )
        db.add(pending)
        await db.flush()
        pending_id = pending.id

        # Delete the scan config — should cascade
        del_resp = await superuser_client.delete(f"/api/scans/{config_id}")
        assert del_resp.status_code == 204

        # Pending host should be gone
        result = await db.execute(select(PendingHost).where(PendingHost.id == pending_id))
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent_returns_404(self, superuser_client):
        resp = await superuser_client.delete("/api/scans/99999")
        assert resp.status_code == 404

    async def test_pending_summary_counts_correctly(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "summary-scan",
                "cidrs": ["10.0.4.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 240,
            },
        )
        config_id = create_resp.json()["id"]

        # Check baseline
        baseline = await superuser_client.get("/api/scans/pending-summary")
        assert baseline.status_code == 200
        baseline_total = baseline.json()["total"]

        # Add 2 pending hosts
        for ip in ("10.0.4.10", "10.0.4.11"):
            db.add(PendingHost(scan_config_id=config_id, ip_address=ip, ssh_verified=False))
        await db.flush()

        after = await superuser_client.get("/api/scans/pending-summary")
        assert after.status_code == 200
        assert after.json()["total"] == baseline_total + 2

    async def test_list_all_pending_includes_scan_config_name(self, superuser_client, db):
        """GET /api/scans/pending returns fleet-wide pending hosts with scan_config_name joined."""
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "fleet-pending-scan",
                "cidrs": ["10.0.4.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 120,
            },
        )
        assert create_resp.status_code == 201
        config_id = create_resp.json()["id"]

        # Baseline: record how many fleet-pending rows exist before adding ours
        baseline_resp = await superuser_client.get("/api/scans/pending")
        assert baseline_resp.status_code == 200
        baseline_count = len(baseline_resp.json())

        # Add a pending host with a known hostname
        db.add(
            PendingHost(
                scan_config_id=config_id,
                ip_address="10.0.4.99",
                hostname="discovered-host",
                ssh_verified=True,
            )
        )
        await db.flush()

        resp = await superuser_client.get("/api/scans/pending")
        assert resp.status_code == 200
        hosts = resp.json()
        assert len(hosts) == baseline_count + 1

        # The newest entry (first, ordered by discovered_at desc) is ours
        newest = hosts[0]
        assert newest["ip_address"] == "10.0.4.99"
        assert newest["hostname"] == "discovered-host"
        assert newest["scan_config_id"] == config_id
        assert newest["scan_config_name"] == "fleet-pending-scan"
        assert newest["ssh_verified"] is True

    async def test_list_pending_hosts_for_config(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "pending-list-scan",
                "cidrs": ["10.0.5.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        config_id = create_resp.json()["id"]

        db.add(PendingHost(scan_config_id=config_id, ip_address="10.0.5.20", ssh_verified=True))
        await db.flush()

        resp = await superuser_client.get(f"/api/scans/{config_id}/pending")
        assert resp.status_code == 200
        hosts = resp.json()
        assert len(hosts) == 1
        assert hosts[0]["ip_address"] == "10.0.5.20"
        assert hosts[0]["ssh_verified"] is True

    async def test_duplicate_name_returns_409(self, superuser_client, db):
        key = await create_ssh_key(db)
        payload = {
            "name": "dupe-scan",
            "cidrs": ["10.0.8.0/24"],
            "ssh_key_id": key.id,
            "interval_minutes": 60,
        }
        resp1 = await superuser_client.post("/api/scans", json=payload)
        assert resp1.status_code == 201
        resp2 = await superuser_client.post("/api/scans", json=payload)
        assert resp2.status_code == 409

    async def test_run_now_returns_202(self, superuser_client, db):
        from unittest.mock import patch

        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "runnow-scan",
                "cidrs": ["10.0.9.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        config_id = create_resp.json()["id"]

        with patch("app.tasks.celery_app.send_task") as mock_send:
            resp = await superuser_client.post(f"/api/scans/{config_id}/run")

        assert resp.status_code == 202
        mock_send.assert_called_once_with("scans.run_config", args=[config_id])


# ---------------------------------------------------------------------------
# T6 SSH key deletion guard
# ---------------------------------------------------------------------------


class TestSSHKeyDeletionGuard:
    async def test_cannot_delete_key_referenced_by_scan_config(self, superuser_client, db):
        key = await create_ssh_key(db)
        create_resp = await superuser_client.post(
            "/api/scans",
            json={
                "name": "key-guard-scan",
                "cidrs": ["10.1.0.0/24"],
                "ssh_key_id": key.id,
                "interval_minutes": 60,
            },
        )
        assert create_resp.status_code == 201

        del_resp = await superuser_client.delete(f"/api/ssh-keys/{key.id}")
        assert del_resp.status_code == 409
        assert "scan config" in del_resp.json()["detail"].lower()

    async def test_can_delete_key_not_referenced_by_scan_config(self, superuser_client, db):
        key = await create_ssh_key(db)
        del_resp = await superuser_client.delete(f"/api/ssh-keys/{key.id}")
        assert del_resp.status_code == 204
