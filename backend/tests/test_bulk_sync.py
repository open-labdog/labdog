"""Bulk sync endpoint tests — POST /api/sync/hosts/{host_id}/bulk.

Covers fresh-insert dispatch, idempotent reuse on the partial-unique
index, validation of ``module_filter`` shape, missing-host 404, and
auth-required 401.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync_job import SyncJob
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


@pytest.fixture
def mock_run_host_sync():
    """Patch the orchestrator's ``.delay`` at the API call site."""
    mock = MagicMock()
    with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", mock):
        yield mock


async def test_bulk_sync_creates_pending_job_and_dispatches(
    superuser_client, db: AsyncSession, mock_run_host_sync
):
    from app.models.audit_log import AuditLog

    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host_id = host.id

    resp = await superuser_client.post(
        f"/api/sync/hosts/{host_id}/bulk",
        json={"module_filter": None},
    )

    assert resp.status_code == 201, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "pending"
    assert body["module_filter"] is None
    job_id = body["job_id"]

    # SyncJob row exists with module_type="bulk".
    row = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one_or_none()
    assert row is not None
    assert row.host_id == host_id
    assert row.module_type == "bulk"

    # Dispatched once, with the right kwargs.
    assert mock_run_host_sync.call_count == 1
    call = mock_run_host_sync.call_args
    assert call.kwargs.get("job_id") == job_id
    assert call.kwargs.get("host_id") == host_id
    assert call.kwargs.get("module_filter") is None

    # SEC-05: a trigger-time audit row was emitted at API layer.
    audit_rows = (
        (
            await db.execute(
                select(AuditLog).where(
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
    assert after["sync_job_id"] == job_id
    assert after["module_filter"] is None
    assert after["trigger_kind"] == "bulk"


async def test_bulk_sync_with_filter(superuser_client, db: AsyncSession, mock_run_host_sync):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host_id = host.id

    resp = await superuser_client.post(
        f"/api/sync/hosts/{host_id}/bulk",
        json={"module_filter": ["firewall", "services"]},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["module_filter"] == ["firewall", "services"]

    assert mock_run_host_sync.call_count == 1
    assert mock_run_host_sync.call_args.kwargs["module_filter"] == [
        "firewall",
        "services",
    ]


async def test_bulk_sync_idempotent_when_already_pending(
    superuser_client, db: AsyncSession, mock_run_host_sync
):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host_id = host.id

    # Pre-create a pending bulk SyncJob — this is the "in-flight" job.
    existing = SyncJob(host_id=host_id, status="pending", module_type="bulk")
    db.add(existing)
    await db.flush()
    existing_id = existing.id
    await db.commit()

    # BUG-41: fire the second POST with a DIFFERENT module_filter than
    # the existing job. The endpoint must not echo the caller's filter
    # back as if it were authoritative — since SyncJob doesn't persist
    # the original filter list, the honest answer is ``None``.
    resp = await superuser_client.post(
        f"/api/sync/hosts/{host_id}/bulk",
        json={"module_filter": ["firewall"]},
    )

    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["job_id"] == existing_id
    assert body["status"] == "pending"
    # The second request's filter MUST NOT leak through. The queued job
    # won't honour ``["firewall"]`` — it runs every module — so claiming
    # the response says ``["firewall"]`` would mislead audit / monitoring
    # consumers. ``None`` is the truthful "we don't know what the
    # in-flight job's original filter was".
    assert body["module_filter"] is None

    # No second SyncJob row inserted.
    rows = (
        (
            await db.execute(
                select(SyncJob).where(SyncJob.host_id == host_id, SyncJob.module_type == "bulk")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1

    # No dispatch on the idempotent re-entry.
    assert mock_run_host_sync.call_count == 0

    # SEC-05: the idempotent-200 path still represents an operator's
    # intent — record it with the existing job's ID.
    from app.models.audit_log import AuditLog

    audit_rows = (
        (
            await db.execute(
                select(AuditLog).where(
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
    assert after["sync_job_id"] == existing_id
    assert after["trigger_kind"] == "bulk"
    # The audit captures *this* request's filter (so the trail records
    # what the operator asked for at trigger time, even though the
    # endpoint response surfaces ``None`` per BUG-41).
    assert after["module_filter"] == ["firewall"]


async def test_bulk_sync_rejects_empty_filter(
    superuser_client, db: AsyncSession, mock_run_host_sync
):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host_id = host.id

    resp = await superuser_client.post(
        f"/api/sync/hosts/{host_id}/bulk",
        json={"module_filter": []},
    )

    assert resp.status_code == 400, resp.text
    assert "module_filter must be null or non-empty" in resp.text
    assert mock_run_host_sync.call_count == 0


async def test_bulk_sync_rejects_unknown_module(
    superuser_client, db: AsyncSession, mock_run_host_sync
):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host_id = host.id

    resp = await superuser_client.post(
        f"/api/sync/hosts/{host_id}/bulk",
        json={"module_filter": ["firewall", "nonsense", "bogus"]},
    )

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    # Both offenders must be named in the response so the operator can fix
    # their request without guessing.
    assert "nonsense" in detail
    assert "bogus" in detail
    assert mock_run_host_sync.call_count == 0


async def test_bulk_sync_404_for_missing_host(
    superuser_client, db: AsyncSession, mock_run_host_sync
):
    resp = await superuser_client.post(
        "/api/sync/hosts/999999/bulk",
        json={"module_filter": None},
    )

    assert resp.status_code == 404, resp.text
    assert mock_run_host_sync.call_count == 0


async def test_bulk_sync_requires_auth(client, db: AsyncSession, mock_run_host_sync):
    """Unauthenticated POST → 401 (no auth cookie/token)."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host_id = host.id
    await db.commit()

    resp = await client.post(
        f"/api/sync/hosts/{host_id}/bulk",
        json={"module_filter": None},
    )

    assert resp.status_code == 401, resp.text
    assert mock_run_host_sync.call_count == 0
