"""Tests for the Celery task wrapper around ``orchestrate_host_sync``.

These tests exercise the task wrapper end-to-end (pre-run writes,
orchestrator dispatch, post-run atomic writes, audit emission, tmpfs
cleanup). The orchestrator itself is mocked at the wrapper's import
site so no real ansible-runner / SSH is invoked. ``task_session`` is
patched to yield the test's savepoint-wrapped ``db`` session so all
writes are visible to the assertions and roll back at test end.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_patcher(db):
    """Patch ``task_session`` to yield the test's savepoint-wrapped session.

    Without this, the task wrapper opens its own engine via
    ``task_session()`` and writes vanish from the test's view.
    """

    @asynccontextmanager
    async def _fake_task_session():
        yield db

    return patch("app.tasks.host_sync_orchestrator.task_session", new=_fake_task_session)


async def _create_pending_job(
    db: AsyncSession, host_id: int, triggered_by_user_id: int | None = None
) -> int:
    from app.models.sync_job import SyncJob

    job = SyncJob(
        host_id=host_id,
        status="pending",
        triggered_by_user_id=triggered_by_user_id,
    )
    db.add(job)
    await db.flush()
    return job.id


async def _setup_host_with_backend(db: AsyncSession, backend: str = "nftables") -> int:
    from app.models.host import FirewallBackend

    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    host.firewall_backend = (
        FirewallBackend(backend) if backend != "unknown" else FirewallBackend.unknown
    )
    await db.flush()
    return host.id


def _all_in_sync_outcomes() -> dict[str, str]:
    """Map every canonical module to ``in_sync``."""
    from app.ansible_runtime.composer import CANONICAL_ORDER

    return {m: "in_sync" for m in CANONICAL_ORDER}


def _patch_orchestrator(return_outcomes: dict[str, str], calls: list[dict] | None = None):
    """Replace ``orchestrate_host_sync`` (as imported by the wrapper).

    The mock returns a 3-tuple matching the orchestrator's contract.
    Recorded kwargs land in ``calls`` for argument assertions.
    """

    async def _fake_orchestrate(*args, **kwargs):
        if calls is not None:
            recorded = dict(kwargs)
            # Positional args present in the wrapper's call: (host_id,
            # module_filter, db). Stash them under stable names too.
            if len(args) >= 1:
                recorded["_pos_host_id"] = args[0]
            if len(args) >= 2:
                recorded["_pos_module_filter"] = args[1]
            calls.append(recorded)
        return return_outcomes, "playbook-yaml-stub", "{}"

    return patch(
        "app.tasks.host_sync_orchestrator.orchestrate_host_sync",
        new=_fake_orchestrate,
    )


def _patch_orchestrator_raising(exc: Exception, calls: list[dict] | None = None):
    async def _fake_orchestrate(*args, **kwargs):
        if calls is not None:
            recorded = dict(kwargs)
            if len(args) >= 1:
                recorded["_pos_host_id"] = args[0]
            if len(args) >= 2:
                recorded["_pos_module_filter"] = args[1]
            calls.append(recorded)
        raise exc

    return patch(
        "app.tasks.host_sync_orchestrator.orchestrate_host_sync",
        new=_fake_orchestrate,
    )


async def _run_task(
    db: AsyncSession,
    job_id: int,
    host_id: int,
    module_filter: list[str] | None,
    *,
    private_data_dir: str,
    ssh_key_path: str,
) -> dict:
    """Invoke the task's async body using the test's DB session."""
    from app.tasks.host_sync_orchestrator import _async_run

    with _make_session_patcher(db):
        return await _async_run(
            job_id=job_id,
            host_id=host_id,
            module_filter=module_filter,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )


async def _audit_rows_for(db: AsyncSession, host_id: int, action: str | None = None):
    from app.models.audit_log import AuditLog

    stmt = select(AuditLog).where(AuditLog.entity_type == "host", AuditLog.entity_id == host_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    return (await db.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_happy_path_all_modules_in_sync(db: AsyncSession, tmp_path):
    """All modules return in_sync; SyncJob success, audit row sync_completed."""
    from app.ansible_runtime.composer import CANONICAL_ORDER
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob
    from app.tasks.host_sync_orchestrator import _MODULE_TYPE_MAPPING

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id, triggered_by_user_id=None)

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with _patch_orchestrator(_all_in_sync_outcomes()):
        result = await _run_task(
            db,
            job_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    assert job.status == "success"
    assert job.error_message is None
    assert job.completed_at is not None
    assert job.started_at is not None

    rows = (
        (await db.execute(select(HostModuleStatus).where(HostModuleStatus.host_id == host_id)))
        .scalars()
        .all()
    )
    assert len(rows) == len(CANONICAL_ORDER)
    for row in rows:
        assert row.sync_status == "in_sync"
        assert row.last_sync_at is not None

    audit_rows = await _audit_rows_for(db, host_id, action="sync_completed")
    assert len(audit_rows) == 1
    after = audit_rows[0].after_state
    assert after["sync_job_id"] == job_id
    assert after["module_filter"] is None
    assert set(after["module_outcomes"].keys()) == set(CANONICAL_ORDER)

    assert result["job_id"] == job_id
    assert result["status"] == "success"
    # The result dict uses canonical names, mirroring the audit payload.
    assert set(result["module_outcomes"].keys()) == set(CANONICAL_ORDER)
    # Sanity: the mapping covers every canonical module.
    assert set(_MODULE_TYPE_MAPPING.keys()) == set(CANONICAL_ORDER)


async def test_failed_module_marks_job_failed(db: AsyncSession, tmp_path):
    """One ``error`` outcome → SyncJob ``failed`` and matching audit action."""
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    outcomes = _all_in_sync_outcomes()
    outcomes["services"] = "error"

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with _patch_orchestrator(outcomes):
        result = await _run_task(
            db,
            job_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    assert job.status == "failed"
    assert job.error_message  # truthy

    services_row = (
        await db.execute(
            select(HostModuleStatus).where(
                HostModuleStatus.host_id == host_id,
                HostModuleStatus.module_type == "service",
            )
        )
    ).scalar_one()
    assert services_row.sync_status == "error"

    audit_rows = await _audit_rows_for(db, host_id, action="sync_failed")
    assert len(audit_rows) == 1
    assert audit_rows[0].after_state["module_outcomes"]["services"] == "error"

    assert result["status"] == "failed"


async def test_no_tasks_outcome_treated_as_in_sync(db: AsyncSession, tmp_path):
    """``no_tasks`` collapses to ``in_sync`` on HostModuleStatus."""
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with _patch_orchestrator({"firewall": "no_tasks"}):
        await _run_task(
            db,
            job_id,
            host_id,
            module_filter=["firewall"],
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    fw_row = (
        await db.execute(
            select(HostModuleStatus).where(
                HostModuleStatus.host_id == host_id,
                HostModuleStatus.module_type == "firewall",
            )
        )
    ).scalar_one()
    assert fw_row.sync_status == "in_sync"

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    assert job.status == "success"


async def test_orchestrator_raises_marks_all_modules_error(db: AsyncSession, tmp_path):
    """Orchestrator raises → SyncJob failed, every seeded module error, audit row exists."""
    from app.ansible_runtime.composer import CANONICAL_ORDER
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with _patch_orchestrator_raising(LookupError("synthetic boom")):
        with pytest.raises(RuntimeError, match="synthetic boom"):
            await _run_task(
                db,
                job_id,
                host_id,
                module_filter=None,
                private_data_dir=private_data_dir,
                ssh_key_path=ssh_key_path,
            )

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    assert job.status == "failed"
    assert "synthetic boom" in (job.error_message or "")

    rows = (
        (await db.execute(select(HostModuleStatus).where(HostModuleStatus.host_id == host_id)))
        .scalars()
        .all()
    )
    assert len(rows) == len(CANONICAL_ORDER)
    for row in rows:
        assert row.sync_status == "error"

    audit_rows = await _audit_rows_for(db, host_id, action="sync_failed")
    assert len(audit_rows) == 1


async def test_firewall_backend_unknown_skips_firewall_records_error(db: AsyncSession, tmp_path):
    """Unknown backend → orchestrator never sees firewall, status row pre-errored."""
    from app.ansible_runtime.composer import CANONICAL_ORDER
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "unknown")
    job_id = await _create_pending_job(db, host_id)

    # Orchestrator returns in_sync for every module *except* firewall —
    # which it should never be asked about.
    outcomes = {m: "in_sync" for m in CANONICAL_ORDER if m != "firewall"}

    calls: list[dict] = []
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with _patch_orchestrator(outcomes, calls):
        await _run_task(
            db,
            job_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    # Orchestrator was called exactly once, with an explicit list that
    # omits firewall.
    assert len(calls) == 1
    passed_filter = calls[0].get("_pos_module_filter")
    assert passed_filter is not None
    assert "firewall" not in passed_filter

    fw_row = (
        await db.execute(
            select(HostModuleStatus).where(
                HostModuleStatus.host_id == host_id,
                HostModuleStatus.module_type == "firewall",
            )
        )
    ).scalar_one()
    assert fw_row.sync_status == "error"
    assert "backend not detected" in (fw_row.error_message or "")

    # Other modules untouched by the firewall guard.
    services_row = (
        await db.execute(
            select(HostModuleStatus).where(
                HostModuleStatus.host_id == host_id,
                HostModuleStatus.module_type == "service",
            )
        )
    ).scalar_one()
    assert services_row.sync_status == "in_sync"

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    assert job.status == "failed"  # firewall errored


async def test_timeout_computed_from_module_count(db: AsyncSession, tmp_path):
    """Timeout passed to orchestrator = base + per_module * module_count (or floor)."""
    from app.ansible_runtime.composer import CANONICAL_ORDER

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    calls: list[dict] = []
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    # Force the configured floor below the computed value so the
    # computed value wins the max(); this proves the formula.
    with (
        _patch_orchestrator(_all_in_sync_outcomes(), calls),
        patch(
            "app.settings_service.get_setting_sync_typed",
            return_value=1,
        ),
    ):
        await _run_task(
            db,
            job_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    assert len(calls) == 1
    expected = 60 + 120 * len(CANONICAL_ORDER)
    assert calls[0].get("timeout") == expected


async def test_tmpfs_cleanup_on_success(db: AsyncSession, tmp_path):
    """The tmpfs workspace is gone after a successful run."""
    from app.tasks.host_sync_orchestrator import _async_run, _make_tmpfs_workspace

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    # Use the same workspace creation helper the task does, then drive
    # _async_run directly (so we control the surrounding finally block
    # used to delete the dir).
    private_data_dir, ssh_key_path = _make_tmpfs_workspace()

    try:
        with (
            _patch_orchestrator(_all_in_sync_outcomes()),
            _make_session_patcher(db),
        ):
            await _async_run(
                job_id=job_id,
                host_id=host_id,
                module_filter=None,
                private_data_dir=private_data_dir,
                ssh_key_path=ssh_key_path,
            )
    finally:
        # Mirrors what run_host_sync's finally does.
        from app.tasks.host_sync_orchestrator import _cleanup_tmpfs

        _cleanup_tmpfs(private_data_dir)

    assert not os.path.exists(private_data_dir)


async def test_tmpfs_cleanup_on_orchestrator_failure(db: AsyncSession, tmp_path):
    """Orchestrator raising still leaves tmpfs cleaned up."""
    from app.tasks.host_sync_orchestrator import (
        _async_run,
        _cleanup_tmpfs,
        _make_tmpfs_workspace,
    )

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    private_data_dir, ssh_key_path = _make_tmpfs_workspace()

    raised = False
    try:
        with (
            _patch_orchestrator_raising(LookupError("nope")),
            _make_session_patcher(db),
        ):
            try:
                await _async_run(
                    job_id=job_id,
                    host_id=host_id,
                    module_filter=None,
                    private_data_dir=private_data_dir,
                    ssh_key_path=ssh_key_path,
                )
            except RuntimeError:
                raised = True
    finally:
        _cleanup_tmpfs(private_data_dir)

    assert raised, "the wrapper should re-raise on orchestrator failure"
    assert not os.path.exists(private_data_dir)


async def test_module_filter_passed_to_orchestrator(db: AsyncSession, tmp_path):
    """Caller-supplied filter passes through verbatim (no firewall guard tripped)."""
    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    calls: list[dict] = []
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with _patch_orchestrator({"firewall": "in_sync"}, calls):
        await _run_task(
            db,
            job_id,
            host_id,
            module_filter=["firewall"],
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    assert len(calls) == 1
    assert calls[0].get("_pos_module_filter") == ["firewall"]


# ---------------------------------------------------------------------------
# Misc — keep `MagicMock`/`AsyncMock` imports referenced for ruff parity
# even though we use plain async functions for the orchestrator stubs.
# (Removing them is fine; they're harmless and consistent with sibling
# test files.)
# ---------------------------------------------------------------------------

_ = (MagicMock, AsyncMock)
