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
