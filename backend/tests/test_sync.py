"""
Integration tests for sync endpoints.

Tests cover:
- Plan endpoint: POST /api/sync/hosts/{id}/plan → diff with has_changes
- Trigger sync: POST /api/sync/hosts/{id}/sync → 201, pending job, Celery called
- Get job status: GET /api/sync/jobs/{id} → 200
- Conflict detection: POST sync when running job exists → 409
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_group, create_host, create_rule, create_ssh_key

pytestmark = pytest.mark.integration


class TestSync:
    """Test suite for sync endpoints."""

    async def test_plan_host(self, superuser_client, db: AsyncSession):
        """
        Setup host with SSH key + group + rule via factories,
        POST /api/sync/hosts/{id}/plan → 200, response has has_changes field.

        fetch_current_state_stub returns [], so any rule in desired state = has_changes=True.
        """
        # Setup: SSH key → group → rule → host in group
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=80,
        )
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        # Capture id as plain int before session expires the object
        host_id = host.id
        hostname = host.hostname

        resp = await superuser_client.post(f"/api/sync/hosts/{host_id}/plan")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "has_changes" in data, "Response must have has_changes field"
        assert data["host_id"] == host_id
        assert data["hostname"] == hostname
        # fetch_current_state_stub returns [] so desired rules → has_changes=True
        assert data["has_changes"] is True
        assert isinstance(data["rules_to_add"], list)
        assert len(data["rules_to_add"]) >= 1

    async def test_trigger_sync_creates_job(
        self, superuser_client, db: AsyncSession, mock_celery_tasks
    ):
        """
        POST /api/sync/hosts/{id}/sync → 201, response has status == "pending",
        verify mock_celery_tasks was called (Celery dispatched).
        """
        from sqlalchemy import select as _select

        from app.models.audit_log import AuditLog

        # Setup: SSH key → group → rule → host in group
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=22,
        )
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host_id = host.id  # capture before session expires

        resp = await superuser_client.post(f"/api/sync/hosts/{host_id}/sync")

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "status" in data, "Response must have status field"
        assert data["status"] == "pending", f"Expected pending, got {data['status']}"
        assert "id" in data, "Response must have id field"
        assert data["host_id"] == host_id

        # Verify Celery task was dispatched
        assert mock_celery_tasks.call_count >= 1, (
            "Expected run_sync_playbook.delay to be called, "
            f"got {mock_celery_tasks.call_count} calls"
        )

        # SEC-05: trigger-time audit row at API layer for the per-host
        # firewall-sync endpoint.
        audit_rows = (
            (
                await db.execute(
                    _select(AuditLog).where(
                        AuditLog.entity_type == "host",
                        AuditLog.entity_id == host_id,
                        AuditLog.action == "sync_triggered",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1
        after = audit_rows[0].after_state
        assert after["sync_job_id"] == data["id"]
        assert after["module_filter"] == ["firewall"]
        assert after["trigger_kind"] == "per_host"

    async def test_trigger_group_sync_audits_at_trigger(
        self, superuser_client, db: AsyncSession, mock_celery_tasks
    ):
        """SEC-05: per-group firewall-sync emits an audit row scoped to
        the group entity, with affected host IDs in ``after_state.hosts``.
        """
        from sqlalchemy import select as _select

        from app.models.audit_log import AuditLog

        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=2222,
        )
        host_a = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host_b = await create_host(db, ssh_key_id=ssh_key.id, ip="10.0.0.2", group_ids=[group.id])
        host_a_id = host_a.id
        host_b_id = host_b.id
        group_id = group.id

        resp = await superuser_client.post(f"/api/sync/groups/{group_id}/sync")

        assert resp.status_code == 201, resp.text

        audit_rows = (
            (
                await db.execute(
                    _select(AuditLog).where(
                        AuditLog.entity_type == "host_group",
                        AuditLog.entity_id == group_id,
                        AuditLog.action == "sync_triggered",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1
        after = audit_rows[0].after_state
        assert after["trigger_kind"] == "per_group"
        assert after["module_filter"] == ["firewall"]
        assert sorted(after["hosts"]) == sorted([host_a_id, host_b_id])
        assert len(after["sync_job_ids"]) == 2

    async def test_get_job_status(self, superuser_client, db: AsyncSession, mock_celery_tasks):
        """
        Trigger sync to create a job, then GET /api/sync/jobs/{id} → 200.
        """
        # Setup: SSH key → group → rule → host in group
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=443,
        )
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host_id = host.id  # capture before session expires

        # Trigger sync to create a job
        sync_resp = await superuser_client.post(f"/api/sync/hosts/{host_id}/sync")
        assert sync_resp.status_code == 201, f"Sync trigger failed: {sync_resp.text}"
        job_id = sync_resp.json()["id"]

        # Get job status
        resp = await superuser_client.get(f"/api/sync/jobs/{job_id}")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["id"] == job_id
        assert "status" in data
        assert data["status"] in ("pending", "running", "success", "failed")
        assert data["host_id"] == host_id

    async def test_preview_firewall_only(self, superuser_client, db: AsyncSession):
        """POST /api/sync/hosts/{id}/preview with module_filter=["firewall"]
        returns a single normalized ModuleDiff with adds (host backend is
        'unknown' so current state is empty → desired rule is an add).
        """
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        await create_rule(
            db, group_id=group.id, action="allow", protocol="tcp", direction="input", port_start=80
        )
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host_id = host.id

        resp = await superuser_client.post(
            f"/api/sync/hosts/{host_id}/preview", json={"module_filter": ["firewall"]}
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list) and len(data) == 1
        fw = data[0]
        assert fw["module"] == "firewall"
        assert fw["has_changes"] is True
        assert fw["error"] is None
        assert any(c["op"] == "add" for c in fw["changes"])

    async def test_preview_all_modules_canonical_order(
        self, superuser_client, db: AsyncSession, monkeypatch
    ):
        """module_filter=None previews every module in canonical order.

        All SSH collectors are stubbed empty so no real connection is
        attempted; the endpoint returns one normalized ModuleDiff per
        module, in canonical order, with no per-module errors.
        """
        from app.ansible_runtime.composer import CANONICAL_ORDER

        async def _empty(*args, **kwargs):
            return []

        for target in (
            "app.services.collector.collect_service_states",
            "app.packages.collector.collect_package_states",
            "app.cron.collector.collect_cron_jobs",
            "app.hosts_mgmt.collector.collect_hosts_file",
            "app.user_mgmt.collector.collect_user_states",
            "app.user_mgmt.collector.collect_group_states",
        ):
            monkeypatch.setattr(target, _empty)

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        host_id = host.id

        resp = await superuser_client.post(f"/api/sync/hosts/{host_id}/preview", json={})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # One ModuleDiff per module, emitted in canonical order.
        assert [m["module"] for m in data] == CANONICAL_ORDER
        assert all(m["error"] is None for m in data), data
        # Firewall always seeds an SSH anti-lockout rule, so against an
        # empty host it reports an add — confirming the normalized shape
        # carries through.
        fw = next(m for m in data if m["module"] == "firewall")
        assert any(c["op"] == "add" for c in fw["changes"])

    async def test_preview_collector_error_is_per_module(
        self, superuser_client, db: AsyncSession, monkeypatch
    ):
        """A collector raising surfaces as ModuleDiff.error, not a 5xx."""

        async def _boom(*args, **kwargs):
            raise RuntimeError("ssh exploded")

        monkeypatch.setattr("app.services.collector.collect_service_states", _boom)

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        host_id = host.id

        resp = await superuser_client.post(
            f"/api/sync/hosts/{host_id}/preview", json={"module_filter": ["services"]}
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 1
        assert data[0]["module"] == "services"
        assert data[0]["has_changes"] is False
        assert "ssh exploded" in (data[0]["error"] or "")

    async def test_preview_unknown_module_400(self, superuser_client, db: AsyncSession):
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        host_id = host.id

        resp = await superuser_client.post(
            f"/api/sync/hosts/{host_id}/preview", json={"module_filter": ["bogus"]}
        )
        assert resp.status_code == 400, resp.text

    async def test_preview_host_not_found_404(self, superuser_client, db: AsyncSession):
        resp = await superuser_client.post("/api/sync/hosts/999999/preview", json={})
        assert resp.status_code == 404, resp.text

    async def test_trigger_sync_zero_rules_now_allowed(
        self, superuser_client, db: AsyncSession, mock_celery_tasks
    ):
        """Guard dropped: a host in a group with zero user firewall rules
        now syncs (201) instead of 400. Firewall desired-state still
        carries the auto SSH anti-lockout rule, and the preview is the
        safety check.
        """
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)  # no rules created
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host_id = host.id

        resp = await superuser_client.post(f"/api/sync/hosts/{host_id}/sync")

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        assert resp.json()["status"] == "pending"
        assert mock_celery_tasks.call_count >= 1

    async def test_trigger_sync_when_running_409(self, superuser_client, db: AsyncSession):
        """
        Insert a SyncJob with status="running" via db,
        POST /api/sync/hosts/{id}/sync → 409 (sync already in progress).
        """
        from app.models.sync_job import SyncJob

        # Setup: SSH key → group → rule → host in group
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=8080,
        )
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host_id = host.id  # capture before session expires
        group_id = group.id  # capture before session expires

        # Insert a running SyncJob directly
        job = SyncJob(host_id=host_id, group_id=group_id, status="running")
        db.add(job)
        await db.flush()

        # Attempt to trigger another sync → should get 409
        resp = await superuser_client.post(f"/api/sync/hosts/{host_id}/sync")

        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data
