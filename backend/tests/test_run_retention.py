"""BUG-73: the run tables need a retention job and an index to be scanned by.

``audit_log`` and ``ssh_session_transcripts`` have been pruned daily
since they were added. ``action_runs`` and ``sync_jobs`` never were, and
they are the ones that grow fastest: ``ActionHostRun.output`` holds up to
a mebibyte of transcript per host per run — roughly 7 GB a year for a
nightly twenty-host action — in a table ``check_host_busy`` scans on
every sync, action and drift check.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_host

pytestmark = pytest.mark.integration


def _ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def _run(db, *, status: str, age_days: int, host_id=None, output=None) -> ActionRun:
    run = ActionRun(
        action_key="pkg.upgrade",
        action_version="1.0",
        host_id=host_id,
        parameters={},
        parallelism=1,
        status=status,
        target_kind="host" if host_id else "fleet",
        target_label="target",
        created_at=_ago(age_days),
    )
    db.add(run)
    await db.flush()
    if output is not None:
        db.add(
            ActionHostRun(
                action_run_id=run.id,
                host_id=host_id,
                hostname="node-1",
                status=status,
                output=output,
            )
        )
        await db.flush()
    return run


async def _job(db, *, status: JobStatus, age_days: int, host_id: int) -> SyncJob:
    job = SyncJob(
        host_id=host_id,
        status=status,
        module_type="firewall",
        created_at=_ago(age_days),
    )
    db.add(job)
    await db.flush()
    return job


@pytest.fixture
def retention(db, monkeypatch):
    """Point the pruners at the test's session and set the window.

    Every test in this suite runs inside one transaction that is rolled
    back at the end — that is what keeps them from seeing each other's
    rows. The pruner commits once per batch on purpose, so that it never
    holds a long transaction on the table every claim has to scan, and a
    real commit here would make the test's writes permanent and leak them
    into the tests that follow.

    So ``commit`` is replaced with a recorder rather than left alone. The
    deletes still happen and are still visible to the assertions; the
    commits are counted instead of performed, which is what
    ``test_it_commits_each_batch`` checks. Call the returned function to
    set the window; read ``.commits`` for the call count.
    """
    import contextlib

    from app.tasks import run_retention

    @contextlib.asynccontextmanager
    async def _fake_task_session():
        yield db

    commits: list[None] = []

    async def _record_commit():
        commits.append(None)

    monkeypatch.setattr("app.db.task_session", _fake_task_session)
    monkeypatch.setattr(db, "commit", _record_commit)

    def _set_days(days: int):
        async def _days(_db):
            return days

        monkeypatch.setattr(run_retention, "_get_retention_days", _days)

    _set_days.commits = commits
    return _set_days


class TestActionRunRetention:
    async def test_it_prunes_terminal_runs_past_the_window(self, db, retention):
        from app.tasks.run_retention import _prune_action_runs

        retention(90)
        old = await _run(db, status="succeeded", age_days=120)
        recent = await _run(db, status="succeeded", age_days=10)
        old_id, recent_id = old.id, recent.id

        result = await _prune_action_runs()

        assert result["deleted"] == 1
        assert await db.scalar(select(ActionRun.id).where(ActionRun.id == old_id)) is None
        assert await db.scalar(select(ActionRun.id).where(ActionRun.id == recent_id)) == recent_id

    async def test_the_transcript_goes_with_the_run(self, db, retention):
        """``output`` is the reason this table grows; deleting the parent has
        to take it, or the job prunes nothing that matters."""
        from app.tasks.run_retention import _prune_action_runs

        retention(90)
        ssh_host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        run = await _run(
            db, status="failed", age_days=200, host_id=ssh_host.id, output="PLAY [x] ***\n"
        )
        run_id = run.id

        await _prune_action_runs()

        rows = (
            (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
            .scalars()
            .all()
        )
        assert rows == [], "the host runs should have gone with the run (ON DELETE CASCADE)"

    @pytest.mark.parametrize("status", ["queued", "pending", "running"])
    async def test_live_runs_are_never_pruned(self, db, retention, status):
        """A run waiting behind a host lock can be old without being stale.
        Reaping those belongs to the sweepers, which understand deadlines."""
        from app.tasks.run_retention import _prune_action_runs

        retention(1)
        live = await _run(db, status=status, age_days=365)
        live_id = live.id

        result = await _prune_action_runs()

        assert result["deleted"] == 0
        assert await db.scalar(select(ActionRun.id).where(ActionRun.id == live_id)) == live_id

    async def test_zero_days_keeps_everything(self, db, retention):
        """The value meaning "never delete" must not perform the largest
        possible delete."""
        from app.tasks.run_retention import _prune_action_runs

        retention(0)
        ancient = await _run(db, status="succeeded", age_days=4000)
        ancient_id = ancient.id

        result = await _prune_action_runs()

        assert result["deleted"] == 0
        assert result["skipped"] == "retention disabled"
        assert await db.scalar(select(ActionRun.id).where(ActionRun.id == ancient_id)) is not None

    async def test_it_keeps_going_past_one_batch(self, db, retention, monkeypatch):
        from app.tasks import run_retention

        monkeypatch.setattr(run_retention, "_BATCH_SIZE", 3)
        retention(30)
        for _ in range(7):
            await _run(db, status="succeeded", age_days=100)

        result = await run_retention._prune_action_runs()

        assert result["deleted"] == 7

    async def test_it_commits_each_batch(self, db, retention, monkeypatch):
        """The commit inside the loop is the point of batching: one long
        transaction on ``action_runs`` blocks the claim protocol, which
        every sync and action run has to pass through first. Without this
        assertion, dropping the commit would leave the suite green — the
        deletes would still happen, just all in one transaction."""
        from app.tasks import run_retention

        monkeypatch.setattr(run_retention, "_BATCH_SIZE", 3)
        retention(30)
        for _ in range(7):
            await _run(db, status="succeeded", age_days=100)

        await run_retention._prune_action_runs()

        # 3 + 3 + 1 rows, and a commit after each.
        assert len(retention.commits) == 3

    async def test_it_does_not_commit_when_there_is_nothing_to_delete(self, db, retention):
        from app.tasks.run_retention import _prune_action_runs

        retention(90)
        await _run(db, status="succeeded", age_days=1)

        await _prune_action_runs()

        assert retention.commits == [], "an empty first batch should not open a write"


class TestSyncJobRetention:
    async def test_it_prunes_terminal_jobs_past_the_window(self, db, retention):
        from app.tasks.run_retention import _prune_sync_jobs

        retention(90)
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        old = await _job(db, status=JobStatus.success, age_days=120, host_id=host.id)
        recent = await _job(db, status=JobStatus.success, age_days=5, host_id=host.id)
        old_id, recent_id = old.id, recent.id

        result = await _prune_sync_jobs()

        assert result["deleted"] == 1
        assert await db.scalar(select(SyncJob.id).where(SyncJob.id == old_id)) is None
        assert await db.scalar(select(SyncJob.id).where(SyncJob.id == recent_id)) == recent_id

    @pytest.mark.parametrize("status", [JobStatus.running, JobStatus.pending])
    async def test_live_jobs_are_never_pruned(self, db, retention, status):
        from app.tasks.run_retention import _prune_sync_jobs

        retention(1)
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        job = await _job(db, status=status, age_days=365, host_id=host.id)
        job_id = job.id

        result = await _prune_sync_jobs()

        assert result["deleted"] == 0
        assert await db.scalar(select(SyncJob.id).where(SyncJob.id == job_id)) == job_id


class TestTheClaimProtocolHasIndexes:
    """``check_host_busy`` runs three queries per claim, and a claim happens
    before every sync, action run and drift check. Two of the three tables
    had nothing to use."""

    @pytest.mark.parametrize(
        "index_name",
        [
            "ix_action_runs_host_status",
            "ix_action_runs_status_created",
            "ix_action_host_runs_host_status",
            "ix_action_host_runs_status",
            "ix_sync_jobs_status_created",
        ],
    )
    async def test_the_index_exists(self, db, index_name):
        found = await db.scalar(
            text("SELECT indexname FROM pg_indexes WHERE indexname = :n"), {"n": index_name}
        )
        assert found == index_name

    async def test_every_index_is_valid(self, db):
        """``CREATE INDEX CONCURRENTLY`` leaves an invalid index behind when
        it fails rather than rolling back, and an invalid index is not used
        by the planner — it would look like the migration had worked."""
        invalid = (
            (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_index i ON i.indexrelid = c.oid "
                        "WHERE NOT i.indisvalid AND c.relname LIKE 'ix_%'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert invalid == []


class TestTheScheduleIsRegistered:
    def test_the_module_is_on_the_celery_include_list(self):
        from app.tasks import celery_app

        assert "app.tasks.run_retention" in celery_app.conf.include

    def test_it_exposes_a_registrar_for_beat(self):
        from app.tasks import run_retention
        from app.tasks.beat_registry import registrars_in

        assert registrars_in(run_retention), (
            "beat registration is discovered by convention; without a registrar "
            "the pruning would never be scheduled"
        )
