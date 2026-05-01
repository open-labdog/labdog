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
    db: AsyncSession,
    host_id: int,
    triggered_by_user_id: int | None = None,
    module_type: str = "firewall",
) -> int:
    from app.models.sync_job import SyncJob

    job = SyncJob(
        host_id=host_id,
        status="pending",
        triggered_by_user_id=triggered_by_user_id,
        module_type=module_type,
    )
    db.add(job)
    await db.flush()
    return job.id


async def _create_running_job(db: AsyncSession, host_id: int, module_type: str = "firewall") -> int:
    """Insert a SyncJob already in ``running`` state for blocking-tests."""
    from datetime import UTC, datetime

    from app.models.sync_job import SyncJob

    job = SyncJob(
        host_id=host_id,
        status="running",
        module_type=module_type,
        started_at=datetime.now(UTC),
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
# Queue mechanism (commit C-2)
# ---------------------------------------------------------------------------


async def test_defer_when_another_sync_running_on_host(db: AsyncSession, tmp_path):
    """If another SyncJob is already running on this host, the task defers.

    No orchestration call, no pre-run write, no audit row. The pending
    SyncJob stays in ``pending`` so the in-flight task picks it up via
    ``_dispatch_next_pending_for_host`` when it finishes.
    """
    from app.models.audit_log import AuditLog
    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")
    # Pretend a different SyncJob is already in flight for this host.
    # The DB carries a partial unique index on (host_id, module_type)
    # for active rows, so the queued sibling uses a different
    # ``module_type`` — exactly the situation the queue mechanism is
    # designed to coalesce (e.g. firewall in flight, service queued).
    running_job_id = await _create_running_job(db, host_id, module_type="firewall")
    pending_job_id = await _create_pending_job(db, host_id, module_type="service")

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    orchestrator_calls: list[dict] = []
    delay_calls: list[tuple] = []

    async def _never_orchestrate(*args, **kwargs):  # pragma: no cover - guard
        orchestrator_calls.append({"args": args, "kwargs": kwargs})
        return {}, "", ""

    def _record_delay(*args, **kwargs):
        delay_calls.append((args, kwargs))

    with (
        _make_session_patcher(db),
        patch(
            "app.tasks.host_sync_orchestrator.orchestrate_host_sync",
            new=_never_orchestrate,
        ),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(side_effect=_record_delay),
        ),
    ):
        from app.tasks.host_sync_orchestrator import _async_run

        result = await _async_run(
            job_id=pending_job_id,
            host_id=host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    assert result == {
        "job_id": pending_job_id,
        "status": "deferred",
        "module_outcomes": {},
    }
    # Orchestrator was never invoked.
    assert orchestrator_calls == []
    # No re-dispatch from a deferred task — the running task owns the queue.
    assert delay_calls == []

    # Pending job stays pending; nothing else mutated.
    pending = (await db.execute(select(SyncJob).where(SyncJob.id == pending_job_id))).scalar_one()
    assert pending.status == "pending"
    assert pending.started_at is None
    assert pending.completed_at is None

    running = (await db.execute(select(SyncJob).where(SyncJob.id == running_job_id))).scalar_one()
    assert running.status == "running"

    # No audit row was emitted (deferral is silent).
    audit_count = len(
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "host", AuditLog.entity_id == host_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert audit_count == 0


async def test_no_defer_when_no_other_running(db: AsyncSession, tmp_path):
    """Single SyncJob for host runs normally (no other running → claim wins)."""
    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    with (
        _patch_orchestrator(_all_in_sync_outcomes()),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(),
        ),
    ):
        result = await _run_task(
            db,
            job_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    assert result["status"] == "success"
    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    assert job.status == "success"


async def test_dispatch_next_pending_on_success(db: AsyncSession, tmp_path):
    """When a job finishes successfully, the oldest queued job is dispatched."""
    from datetime import UTC, datetime, timedelta

    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")

    # Create the older pending job (the one we'll run) and a younger
    # queued sibling. Manual ``created_at`` so ordering is deterministic
    # — the default lambda runs at insert time which can be too close.
    older_id = await _create_pending_job(db, host_id, module_type="firewall")
    older = (await db.execute(select(SyncJob).where(SyncJob.id == older_id))).scalar_one()
    older.created_at = datetime.now(UTC) - timedelta(seconds=30)

    younger_id = await _create_pending_job(db, host_id, module_type="service")
    younger = (await db.execute(select(SyncJob).where(SyncJob.id == younger_id))).scalar_one()
    younger.created_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.flush()

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    delay_mock = MagicMock()

    with (
        _patch_orchestrator(_all_in_sync_outcomes()),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=delay_mock,
        ),
    ):
        await _run_task(
            db,
            older_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    delay_mock.assert_called_once()
    kwargs = delay_mock.call_args.kwargs
    assert kwargs["job_id"] == younger_id
    assert kwargs["host_id"] == host_id
    # ``module_type="service"`` → canonical filter ``["services"]``.
    assert kwargs["module_filter"] == ["services"]


async def test_dispatch_next_pending_on_failure(db: AsyncSession, tmp_path):
    """Even when the orchestrator raises, the dispatch step still runs (finally)."""
    from datetime import UTC, datetime, timedelta

    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")

    older_id = await _create_pending_job(db, host_id, module_type="firewall")
    older = (await db.execute(select(SyncJob).where(SyncJob.id == older_id))).scalar_one()
    older.created_at = datetime.now(UTC) - timedelta(seconds=30)

    younger_id = await _create_pending_job(db, host_id, module_type="cron")
    younger = (await db.execute(select(SyncJob).where(SyncJob.id == younger_id))).scalar_one()
    younger.created_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.flush()

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    delay_mock = MagicMock()

    with (
        _patch_orchestrator_raising(LookupError("synthetic boom")),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=delay_mock,
        ),
    ):
        with pytest.raises(RuntimeError, match="synthetic boom"):
            await _run_task(
                db,
                older_id,
                host_id,
                module_filter=None,
                private_data_dir=private_data_dir,
                ssh_key_path=ssh_key_path,
            )

    # Dispatch ran exactly once despite the orchestrator failure.
    delay_mock.assert_called_once()
    assert delay_mock.call_args.kwargs["job_id"] == younger_id
    assert delay_mock.call_args.kwargs["module_filter"] == ["cron"]


async def test_no_dispatch_when_no_pending(db: AsyncSession, tmp_path):
    """Single SyncJob runs to success; no pending sibling → no .delay() call."""
    host_id = await _setup_host_with_backend(db, "nftables")
    job_id = await _create_pending_job(db, host_id)

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    delay_mock = MagicMock()

    with (
        _patch_orchestrator(_all_in_sync_outcomes()),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=delay_mock,
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

    delay_mock.assert_not_called()


async def test_running_on_other_host_does_not_block(db: AsyncSession, tmp_path):
    """A running SyncJob on host A must not block host B from syncing."""
    from app.models.sync_job import SyncJob

    host_a_id = await _setup_host_with_backend(db, "nftables")
    host_b_id = await _setup_host_with_backend(db, "nftables")

    # Stuck-running job on host A — the gate must scope its check by host_id.
    running_a_id = await _create_running_job(db, host_a_id)

    pending_b_id = await _create_pending_job(db, host_b_id)

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    delay_mock = MagicMock()

    with (
        _patch_orchestrator(_all_in_sync_outcomes()),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=delay_mock,
        ),
    ):
        result = await _run_task(
            db,
            pending_b_id,
            host_b_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    assert result["status"] == "success"
    job_b = (await db.execute(select(SyncJob).where(SyncJob.id == pending_b_id))).scalar_one()
    assert job_b.status == "success"

    # Host A's running row is irrelevant to host B and stays put.
    job_a = (await db.execute(select(SyncJob).where(SyncJob.id == running_a_id))).scalar_one()
    assert job_a.status == "running"


def test_filter_from_module_type_canonical():
    """Unit-test the helper across all canonical mappings + bulk + unknown."""
    from app.tasks.host_sync_orchestrator import (
        _MODULE_TYPE_MAPPING,
        _filter_from_module_type,
    )

    # Every canonical → DB value should round-trip back to a single-element filter.
    for canonical, db_value in _MODULE_TYPE_MAPPING.items():
        assert _filter_from_module_type(db_value) == [canonical]

    # The seven persisted DB values explicitly:
    assert _filter_from_module_type("firewall") == ["firewall"]
    assert _filter_from_module_type("service") == ["services"]
    assert _filter_from_module_type("package") == ["packages"]
    assert _filter_from_module_type("cron") == ["cron"]
    assert _filter_from_module_type("linux_user") == ["linux-users"]
    assert _filter_from_module_type("hosts_file") == ["hosts-file"]
    assert _filter_from_module_type("resolver") == ["resolver"]

    # ``bulk`` is the C-3 sentinel for "all modules".
    assert _filter_from_module_type("bulk") is None

    # Unknown values fall back to "all" rather than getting stuck.
    assert _filter_from_module_type("not-a-module") is None


async def test_dispatch_uses_correct_filter_for_bulk_type(db: AsyncSession, tmp_path):
    """A queued job with ``module_type="bulk"`` is re-dispatched with filter=None."""
    from datetime import UTC, datetime, timedelta

    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")

    older_id = await _create_pending_job(db, host_id, module_type="firewall")
    older = (await db.execute(select(SyncJob).where(SyncJob.id == older_id))).scalar_one()
    older.created_at = datetime.now(UTC) - timedelta(seconds=30)

    bulk_id = await _create_pending_job(db, host_id, module_type="bulk")
    bulk = (await db.execute(select(SyncJob).where(SyncJob.id == bulk_id))).scalar_one()
    bulk.created_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.flush()

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    delay_mock = MagicMock()

    with (
        _patch_orchestrator(_all_in_sync_outcomes()),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=delay_mock,
        ),
    ):
        await _run_task(
            db,
            older_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    delay_mock.assert_called_once()
    kwargs = delay_mock.call_args.kwargs
    assert kwargs["job_id"] == bulk_id
    assert kwargs["module_filter"] is None  # "bulk" → all modules


async def test_claim_or_defer_serializes_concurrent_workers(pg_url):
    """BUG-38: two coroutines that both call ``_claim_or_defer`` + ``_prepare_run``
    against the same host must produce exactly one claim winner, not two.

    Without the advisory lock the gate is a plain SELECT and both
    coroutines see "no other running job", both proceed to set
    ``status=running``, both commit. With the lock acquired in the
    same transaction as the ``running`` flip, the second worker blocks
    until the first commits, then observes the running row and defers.
    """
    import asyncio
    import uuid as _uuid

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.crypto.encryption import encrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.models.host import FirewallBackend, Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.models.sync_job import SyncJob
    from app.tasks.host_sync_orchestrator import _claim_or_defer, _prepare_run

    engine = create_async_engine(pg_url, pool_size=4, max_overflow=4)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)

    # --- Set up host + ssh-key + two pending jobs in committed state ---
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode()
    )
    encrypted = encrypt_ssh_key(pem, get_master_key())

    async with SessionMaker() as setup:
        ssh = SSHKey(
            name=f"bug38-key-{_uuid.uuid4().hex[:8]}",
            public_key=pub,
            encrypted_private_key=encrypted,
        )
        setup.add(ssh)
        await setup.flush()
        host = Host(
            hostname=f"bug38-host-{_uuid.uuid4().hex[:8]}.test",
            ip_address="10.99.99.1",
            ssh_key_id=ssh.id,
            firewall_backend=FirewallBackend.nftables,
        )
        setup.add(host)
        await setup.flush()
        host_id = host.id
        ssh_id = ssh.id

        job_a = SyncJob(host_id=host_id, status="pending", module_type="firewall")
        job_b = SyncJob(host_id=host_id, status="pending", module_type="service")
        setup.add(job_a)
        setup.add(job_b)
        await setup.flush()
        job_a_id = job_a.id
        job_b_id = job_b.id
        await setup.commit()

    async def _worker(job_id: int) -> bool:
        """Race one worker through gate + prepare on a fresh connection.
        Returns True iff this worker claimed (i.e. its prepare ran).

        The ``await asyncio.sleep(0.05)`` widens the race window: if the
        gate-and-flip aren't serialized by an advisory lock, the second
        worker reaches its commit *after* the first worker has flipped
        ``status=running`` but before it commits, so the post-fix
        behaviour (second worker observes lock held → waits → reads
        running row → defers) only manifests under the lock.
        """
        async with SessionMaker() as db:
            claimed = await _claim_or_defer(db, job_id, host_id)
            # Yield control to widen the race window between the
            # gate check and the status flip.
            await asyncio.sleep(0.05)
            if claimed:
                await _prepare_run(db, job_id, host_id, module_filter=["firewall"])
            return claimed

    try:
        results = await asyncio.gather(_worker(job_a_id), _worker(job_b_id))

        # Exactly one worker claimed. The other observed the lock-held
        # commit and saw the racer's running row → deferred.
        assert sum(1 for r in results if r) == 1, (
            f"BUG-38: both workers claimed concurrently — got {results}"
        )

        # Database state: exactly one job in ``running``.
        async with SessionMaker() as check:
            running = (
                (
                    await check.execute(
                        select(SyncJob).where(
                            SyncJob.host_id == host_id, SyncJob.status == "running"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(running) == 1, (
                f"expected exactly one running SyncJob, got {[(j.id, j.status) for j in running]}"
            )
    finally:
        # Clean up — these rows live outside the test's savepoint.
        async with SessionMaker() as cleanup:
            await cleanup.execute(
                delete(HostModuleStatus).where(HostModuleStatus.host_id == host_id)
            )
            await cleanup.execute(delete(SyncJob).where(SyncJob.host_id == host_id))
            await cleanup.execute(delete(Host).where(Host.id == host_id))
            await cleanup.execute(delete(SSHKey).where(SSHKey.id == ssh_id))
            await cleanup.commit()
        await engine.dispose()


async def test_dispatch_uses_correct_filter_for_per_tab_type(db: AsyncSession, tmp_path):
    """A per-tab queued job is re-dispatched with its single-element canonical filter."""
    from datetime import UTC, datetime, timedelta

    from app.models.sync_job import SyncJob

    host_id = await _setup_host_with_backend(db, "nftables")

    older_id = await _create_pending_job(db, host_id, module_type="package")
    older = (await db.execute(select(SyncJob).where(SyncJob.id == older_id))).scalar_one()
    older.created_at = datetime.now(UTC) - timedelta(seconds=30)

    queued_id = await _create_pending_job(db, host_id, module_type="firewall")
    queued = (await db.execute(select(SyncJob).where(SyncJob.id == queued_id))).scalar_one()
    queued.created_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.flush()

    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir)
    ssh_key_path = str(tmp_path / "id_ssh")

    delay_mock = MagicMock()

    with (
        _patch_orchestrator(_all_in_sync_outcomes()),
        patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=delay_mock,
        ),
    ):
        await _run_task(
            db,
            older_id,
            host_id,
            module_filter=None,
            private_data_dir=private_data_dir,
            ssh_key_path=ssh_key_path,
        )

    delay_mock.assert_called_once()
    kwargs = delay_mock.call_args.kwargs
    assert kwargs["job_id"] == queued_id
    assert kwargs["module_filter"] == ["firewall"]


# ---------------------------------------------------------------------------
# Misc — keep `MagicMock`/`AsyncMock` imports referenced for ruff parity
# even though we use plain async functions for the orchestrator stubs.
# (Removing them is fine; they're harmless and consistent with sibling
# test files.)
# ---------------------------------------------------------------------------

_ = (MagicMock, AsyncMock)
