"""BUG-135: a GitOps push applied only the firewall, and skipped busy hosts."""

from types import SimpleNamespace

from sqlalchemy import select

from app.models.sync_job import JobStatus, SyncJob
from app.tasks.gitops import _trigger_group_sync, changed_sync_modules
from tests.conftest import create_group, create_host


def test_changed_modules_map_to_orchestrator_modules():
    result = SimpleNamespace(
        modules=[
            SimpleNamespace(module="users", changed=True),
            SimpleNamespace(module="cron_jobs", changed=True),
            SimpleNamespace(module="firewall", changed=False),
            SimpleNamespace(module="drift", changed=True),
        ]
    )
    assert changed_sync_modules(result) == ["cron", "linux-users"]


async def test_jobs_carry_the_changed_modules(db):
    group = await create_group(db)
    host = await create_host(db, group_ids=[group.id])
    await db.commit()

    staged = await _trigger_group_sync(group.id, db, ["cron", "linux-users"])

    assert len(staged) == 1
    job_id, host_id, module_filter = staged[0]
    assert host_id == host.id and module_filter == ["cron", "linux-users"]
    job = await db.get(SyncJob, job_id)
    assert job.module_type == "bulk"
    assert job.module_filter == ["cron", "linux-users"]


async def test_a_busy_host_is_queued_not_skipped(db):
    group = await create_group(db)
    host = await create_host(db, group_ids=[group.id])
    db.add(SyncJob(host_id=host.id, status=JobStatus.running, module_type="firewall"))
    await db.commit()

    staged = await _trigger_group_sync(group.id, db, ["packages"])

    assert [s[1] for s in staged] == [host.id]


async def test_a_queued_bulk_job_is_widened(db):
    group = await create_group(db)
    host = await create_host(db, group_ids=[group.id])
    queued = SyncJob(
        host_id=host.id, status=JobStatus.pending, module_type="bulk", module_filter=["firewall"]
    )
    db.add(queued)
    await db.commit()

    staged = await _trigger_group_sync(group.id, db, ["cron"])

    assert staged == []
    await db.refresh(queued)
    assert queued.module_filter == ["cron", "firewall"]


async def test_a_running_bulk_job_is_followed_by_per_module_jobs(db):
    group = await create_group(db)
    host = await create_host(db, group_ids=[group.id])
    db.add(SyncJob(host_id=host.id, status=JobStatus.running, module_type="bulk"))
    await db.commit()

    staged = await _trigger_group_sync(group.id, db, ["cron", "packages"])

    assert sorted(s[2][0] for s in staged) == ["cron", "packages"]
    rows = (
        (await db.execute(select(SyncJob).where(SyncJob.status == JobStatus.pending)))
        .scalars()
        .all()
    )
    assert {r.module_type for r in rows} == {"cron", "package"}
