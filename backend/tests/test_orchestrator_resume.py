"""BUG-101: a run outlives its orchestrator.

On lin-manager, 2026-10-01 03:14, the nightly ``linux-upgrade`` (group
"Default allow", 17 hosts, one at a time) reached lin-manager itself. Apt
upgraded docker-ce, dockerd restarted, and the LabDog container — with the
orchestrator driving that run — went down mid-run. Nothing resumed the
run. Its last two hosts stayed ``queued`` and it stayed ``running`` for
eight and a half hours, until the whole-run deadline came within reach.

The orchestrator now works from the database and writes a heartbeat while
it does. A stale heartbeat is how the sweeper tells a dead orchestrator
from a slow run, and it hands the run to ``resume_run``, which carries on
from the rows. These tests cover both halves and the fencing between
them: an orchestrator presumed dead that turns out to be alive stops, and
a per-host task sent twice runs once.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from app.tasks.action_orchestrator import (
    _resume_batches,
    _resume_run_async,
    _run_action_async,
)
from app.tasks.action_sweeper import _sweep_stale_action_runs_async
from app.tasks.action_timeouts import ORCHESTRATOR_STALE_SECONDS
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration

ACTION = "_builtin.drift_check"
PER_HOST_TASK = "app.tasks.builtin_dispatchers.run_builtin_drift_check"
RESUME_TASK = "app.tasks.action_orchestrator.resume_run"


class _FakeRedis:
    def __init__(self):
        self.tokens: set[str] = set()

    def exists(self, key):
        return 1 if key in self.tokens else 0

    def publish(self, *_args, **_kwargs):
        return None


class _Workers:
    """Stands in for Celery and the work pool.

    Records what the orchestrator sends, and on each of its polls finishes
    whatever per-host task it has sent so far — so a batch takes exactly
    one poll. ``on_poll`` runs first, to change the world under the
    orchestrator between two of its polls.
    """

    def __init__(self, db):
        self.db = db
        self.sent: list[tuple[str, list, dict]] = []
        self.sent_at_each_poll: list[int] = []
        self.on_poll = None

    def send_task(self, name, args=None, **kwargs):
        self.sent.append((name, list(args or []), kwargs))

    @property
    def host_runs_sent(self) -> list[int]:
        return [args[1] for name, args, _ in self.sent if name == PER_HOST_TASK]

    async def poll(self):
        self.sent_at_each_poll.append(len(self.host_runs_sent))
        if self.on_poll is not None:
            await self.on_poll(self)
        rows = (
            (
                await self.db.execute(
                    select(ActionHostRun).where(
                        ActionHostRun.id.in_(self.host_runs_sent),
                        ActionHostRun.status.in_(("queued", "running")),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "succeeded"
            row.finished_at = datetime.now(UTC)
        await self.db.commit()


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with (
        patch("app.db.task_session", new=_fake),
        patch("app.tasks.action_sweeper.task_session", new=_fake),
    ):
        yield


@pytest.fixture(autouse=True)
def fixed_setting():
    """Deadlines from a pinned ``ansible.playbook_timeout`` (1800s): 3000s
    per host, so a run of three hosts one at a time has 3 × 3000 + 3600."""
    with patch("app.settings_service.get_setting_cached_typed", return_value=1800):
        yield


@pytest.fixture
def redis():
    fake = _FakeRedis()
    with patch("redis.from_url", return_value=fake):
        yield fake


@pytest.fixture
def workers(db, redis):  # noqa: ARG001 — the orchestrator reaches redis too
    w = _Workers(db)
    with (
        patch("app.tasks.action_orchestrator.celery_app.send_task", side_effect=w.send_task),
        patch("app.tasks.action_orchestrator._pause", new=w.poll),
        patch("app.tasks.action_orchestrator.run_action.delay", MagicMock()),
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", MagicMock()),
    ):
        yield w


def _ago(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


async def _group_of(db, n: int):
    key = await create_ssh_key(db)
    group = await create_group(db)
    hosts = [await create_host(db, ssh_key_id=key.id, group_ids=[group.id]) for _ in range(n)]
    return group, hosts


async def _queued_run(db, group_id: int, *, parallelism: int = 1) -> int:
    run = ActionRun(
        action_key=ACTION,
        action_version="1.0",
        group_id=group_id,
        parameters={},
        parallelism=parallelism,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id


async def _run_left_by_a_dead_orchestrator(
    db,
    hosts,
    group_id: int,
    statuses: list[str],
    *,
    owner: str = "dead-orchestrator",
    heartbeat_seconds_ago: int = ORCHESTRATOR_STALE_SECONDS + 300,
    parallelism: int = 1,
) -> tuple[int, list[int]]:
    """A run as its orchestrator left it when it died: owned, its heartbeat
    stopped, one row per host in the given statuses."""
    run = ActionRun(
        action_key=ACTION,
        action_version="1.0",
        group_id=group_id,
        parameters={},
        parallelism=parallelism,
        status="running",
        started_at=_ago(900),
        orchestrator_id=owner,
        heartbeat_at=_ago(heartbeat_seconds_ago),
    )
    db.add(run)
    await db.flush()
    ids = []
    for host, status in zip(hosts, statuses, strict=True):
        hr = ActionHostRun(
            action_run_id=run.id,
            host_id=host.id,
            hostname=host.hostname,
            status=status,
            started_at=_ago(600) if status != "queued" else None,
            finished_at=_ago(500) if status in ("succeeded", "failed") else None,
        )
        db.add(hr)
        await db.flush()
        ids.append(hr.id)
    await db.commit()
    return run.id, ids


async def _run(db, run_id: int) -> ActionRun:
    db.expire_all()
    return (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()


async def _statuses(db, ids: list[int]) -> list[str]:
    db.expire_all()
    rows = (await db.execute(select(ActionHostRun).where(ActionHostRun.id.in_(ids)))).scalars()
    by_id = {r.id: r.status for r in rows}
    return [by_id[i] for i in ids]


# ---------------------------------------------------------------------------
# The orchestrator says it is alive, and lets go when it is done
# ---------------------------------------------------------------------------


class TestTheOrchestratorShowsItIsAlive:
    async def test_it_owns_the_run_while_it_drives_it_and_lets_go_after(self, db, workers):
        group, _ = await _group_of(db, 3)
        run_id = await _queued_run(db, group.id)
        seen: list[tuple[str | None, datetime | None]] = []

        async def _look(w):
            run = await _run(db, run_id)
            seen.append((run.orchestrator_id, run.heartbeat_at))

        workers.on_poll = _look
        await _run_action_async(run_id, orchestrator_id="orch-1")

        assert seen and all(owner == "orch-1" for owner, _ in seen)
        assert all(beat is not None for _, beat in seen)
        run = await _run(db, run_id)
        assert run.status == "succeeded"
        assert (run.orchestrator_id, run.heartbeat_at) == (None, None)

    @pytest.mark.parametrize(
        ("hosts", "parallelism", "sent_at_each_poll"),
        [(3, 1, [1, 2, 3]), (5, 2, [2, 4, 5]), (4, 0, [4])],
    )
    async def test_it_still_runs_one_batch_at_a_time(
        self, db, workers, hosts, parallelism, sent_at_each_poll
    ):
        group, _ = await _group_of(db, hosts)
        run_id = await _queued_run(db, group.id, parallelism=parallelism)

        await _run_action_async(run_id)

        assert workers.sent_at_each_poll == sent_at_each_poll
        assert len(set(workers.host_runs_sent)) == hosts

    @pytest.mark.parametrize("status", ["cancelled", "failed", "running", "succeeded"])
    async def test_it_only_starts_a_queued_run(self, db, workers, status):
        """Cancelled while it waited for a slot, failed by the sweeper's
        never-picked-up rule, or a second delivery of a run already started:
        starting any of these would resurrect or double-drive it."""
        group, _ = await _group_of(db, 2)
        run_id = await _queued_run(db, group.id)
        run = await _run(db, run_id)
        run.status = status
        await db.commit()

        await _run_action_async(run_id)

        assert workers.sent == []
        assert (await _run(db, run_id)).status == status
        children = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id))
        ).all()
        assert children == []


# ---------------------------------------------------------------------------
# An orchestrator that is not the owner, or whose run was cancelled, stops
# ---------------------------------------------------------------------------


class TestTheOrchestratorStopsWhenItShould:
    async def test_it_stops_once_another_orchestrator_owns_the_run(self, db, workers):
        """Taken for dead — a database outage longer than the stale
        threshold, say — and handed on. Two orchestrators driving one run
        would break its parallelism."""
        group, _ = await _group_of(db, 3)
        run_id = await _queued_run(db, group.id)

        async def _hand_on(w):
            run = await _run(db, run_id)
            run.orchestrator_id = "replacement"
            await db.commit()

        workers.on_poll = _hand_on
        await _run_action_async(run_id, orchestrator_id="orch-1")

        assert len(workers.host_runs_sent) == 1
        run = await _run(db, run_id)
        assert run.status == "running"
        assert run.orchestrator_id == "replacement"

    async def test_a_cancel_is_noticed_from_the_status_alone(self, db, workers):
        """The cancel endpoint's Redis token expires after an hour; a group
        run one host at a time can take longer than that, and used to go on
        dispatching hosts after a cancel whose token had lapsed."""
        group, _ = await _group_of(db, 3)
        run_id = await _queued_run(db, group.id)

        async def _cancel(w):
            run = await _run(db, run_id)
            run.status = "cancelled"
            await db.commit()

        workers.on_poll = _cancel
        await _run_action_async(run_id)

        assert len(workers.host_runs_sent) == 1
        run = await _run(db, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None
        rows = (
            (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
            .scalars()
            .all()
        )
        never_sent = [r for r in rows if r.id not in workers.host_runs_sent]
        assert [r.status for r in never_sent] == ["cancelled", "cancelled"]
        assert all(r.finished_at is not None for r in never_sent)


# ---------------------------------------------------------------------------
# resume_run carries on from the rows
# ---------------------------------------------------------------------------


class TestAResumedRunCarriesOn:
    async def test_it_sends_only_what_is_left_one_batch_at_a_time(self, db, workers):
        """The 2026-10-01 shape: some hosts done, one in flight when the
        container went down, the rest never reached."""
        group, hosts = await _group_of(db, 5)
        run_id, ids = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "succeeded", "running", "queued", "queued"]
        )
        in_flight, left = ids[2], ids[3:]

        async def _reap(w):
            # What Pass 1 does to the row whose worker died with the
            # container, once it is past its per-host deadline.
            if len(w.sent_at_each_poll) == 1:
                row = (
                    await db.execute(select(ActionHostRun).where(ActionHostRun.id == in_flight))
                ).scalar_one()
                row.status = "failed"
                await db.commit()

        workers.on_poll = _reap
        await _resume_run_async(run_id, "dead-orchestrator")

        # The host in flight is waited for, never sent again; the others go
        # one at a time, after it, as the run's parallelism says.
        assert workers.host_runs_sent == left
        assert workers.sent_at_each_poll == [0, 1, 2]
        assert await _statuses(db, ids) == [
            "succeeded",
            "succeeded",
            "failed",
            "succeeded",
            "succeeded",
        ]
        run = await _run(db, run_id)
        assert run.status == "partial"
        assert (run.orchestrator_id, run.heartbeat_at) == (None, None)

    async def test_it_leaves_deferred_hosts_to_the_host_queue(self, db, workers):
        group, hosts = await _group_of(db, 3)
        run_id, ids = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "pending", "queued"]
        )

        await _resume_run_async(run_id, "dead-orchestrator")

        assert workers.host_runs_sent == [ids[2]]
        run = await _run(db, run_id)
        # Still open: the deferred host has not run. Its own task closes
        # the run when it does — and nothing is left owning it meanwhile,
        # so the sweeper does not keep resuming it.
        assert run.status == "running"
        assert (run.orchestrator_id, run.heartbeat_at) == (None, None)

    async def test_it_does_nothing_with_a_run_it_does_not_own(self, db, workers):
        group, hosts = await _group_of(db, 2)
        run_id, ids = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["queued", "queued"], owner="someone-else"
        )

        await _resume_run_async(run_id, "stale-copy")

        assert workers.sent == []
        assert await _statuses(db, ids) == ["queued", "queued"]
        assert (await _run(db, run_id)).orchestrator_id == "someone-else"

    async def test_it_does_nothing_with_a_run_that_has_ended(self, db, workers):
        group, hosts = await _group_of(db, 2)
        run_id, _ = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "queued"]
        )
        run = await _run(db, run_id)
        run.status = "cancelled"
        await db.commit()

        await _resume_run_async(run_id, "dead-orchestrator")

        assert workers.sent == []

    @pytest.mark.parametrize(
        ("in_flight", "to_run", "parallelism", "expected"),
        [
            ([3], [4, 5], 1, [[3], [4], [5]]),
            ([3], [4, 5, 6, 7], 3, [[3, 4, 5], [6, 7]]),
            ([3, 4], [5], 1, [[3, 4], [5]]),
            ([], [4, 5], 1, [[4], [5]]),
            ([3], [4, 5], 0, [[3, 4, 5]]),
            ([], [], 2, []),
        ],
    )
    def test_hosts_in_flight_count_against_the_first_batch(
        self, in_flight, to_run, parallelism, expected
    ):
        assert _resume_batches(in_flight, to_run, parallelism) == expected


# ---------------------------------------------------------------------------
# The sweeper hands a run with a stale heartbeat on
# ---------------------------------------------------------------------------


class TestTheSweeperHandsOnADeadOrchestrator:
    async def test_a_stale_heartbeat_gets_the_run_a_new_orchestrator(self, db, workers):
        group, hosts = await _group_of(db, 3)
        run_id, _ = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "running", "queued"]
        )

        result = await _sweep_stale_action_runs_async()

        assert result["runs_resumed"] == [run_id]
        resumes = [(args, kw) for name, args, kw in workers.sent if name == RESUME_TASK]
        assert len(resumes) == 1
        args, kwargs = resumes[0]
        assert args == [run_id]
        run = await _run(db, run_id)
        # The task it sent is the run's owner now, and the heartbeat is
        # fresh, so the next sweep leaves it to that task.
        assert run.orchestrator_id == kwargs["task_id"] != "dead-orchestrator"
        assert run.heartbeat_at > _ago(60)
        assert run.status == "running"

        again = await _sweep_stale_action_runs_async()
        assert again["runs_resumed"] == []

    async def test_end_to_end_the_run_finishes(self, db, workers):
        """Sweeper, then the resume it sent, then the hosts it never got to."""
        group, hosts = await _group_of(db, 4)
        run_id, ids = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "succeeded", "queued", "queued"]
        )

        await _sweep_stale_action_runs_async()
        ((_, _, kwargs),) = [s for s in workers.sent if s[0] == RESUME_TASK]
        await _resume_run_async(run_id, kwargs["task_id"])

        assert await _statuses(db, ids) == ["succeeded"] * 4
        assert (await _run(db, run_id)).status == "succeeded"

    @pytest.mark.parametrize(
        "heartbeat_seconds_ago",
        [10, ORCHESTRATOR_STALE_SECONDS - 30],
        ids=["just-now", "nearly-stale"],
    )
    async def test_a_live_orchestrator_keeps_its_run(self, db, workers, heartbeat_seconds_ago):
        group, hosts = await _group_of(db, 2)
        run_id, _ = await _run_left_by_a_dead_orchestrator(
            db,
            hosts,
            group.id,
            ["running", "queued"],
            owner="alive",
            heartbeat_seconds_ago=heartbeat_seconds_ago,
        )

        result = await _sweep_stale_action_runs_async()

        assert result["runs_resumed"] == []
        assert [s for s in workers.sent if s[0] == RESUME_TASK] == []
        assert (await _run(db, run_id)).orchestrator_id == "alive"

    async def test_a_run_nothing_is_driving_is_not_resumed(self, db, workers):
        """No heartbeat at all: the orchestrator finished its batches and
        let go, and a deferred host is all that keeps the run open. That is
        the host queue's to finish, not a dead orchestrator's."""
        group, hosts = await _group_of(db, 2)
        run_id, _ = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "pending"]
        )
        run = await _run(db, run_id)
        run.orchestrator_id = None
        run.heartbeat_at = None
        await db.commit()

        result = await _sweep_stale_action_runs_async()

        assert result["runs_resumed"] == []
        assert workers.sent == []

    async def test_a_run_past_its_deadline_is_failed_not_resumed(self, db, workers):
        """Resuming a run from a day ago would start upgrading hosts at a
        time nobody chose."""
        group, hosts = await _group_of(db, 2)
        run_id, _ = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["succeeded", "queued"]
        )
        run = await _run(db, run_id)
        run.started_at = _ago(2 * 3000 + 3600 + 600)
        await db.commit()

        result = await _sweep_stale_action_runs_async()

        assert result["runs_failed"] == [run_id]
        assert result["runs_resumed"] == []
        assert (await _run(db, run_id)).status == "failed"


# ---------------------------------------------------------------------------
# Failing a run hands its hosts to whatever waits on them
# ---------------------------------------------------------------------------


class TestSweepingARunReleasesItsHosts:
    async def test_the_host_of_a_swept_run_goes_to_the_op_waiting_on_it(self, db, workers):
        """A host-targeted run holds its host for as long as it is
        ``running``. Failing it frees the host, but the op deferred behind
        it only moves when something re-dispatches it."""
        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        swept = ActionRun(
            action_key=ACTION,
            action_version="1.0",
            host_id=host.id,
            parameters={},
            parallelism=1,
            status="running",
            started_at=_ago(3000 + 3600 + 600),
            created_at=_ago(3000 + 3600 + 600),
        )
        waiting = ActionRun(
            action_key=ACTION,
            action_version="1.0",
            host_id=host.id,
            parameters={},
            parallelism=1,
            status="pending",
            created_at=_ago(300),
        )
        db.add_all([swept, waiting])
        await db.flush()
        db.add(ActionHostRun(action_run_id=swept.id, host_id=host.id, status="queued"))
        await db.commit()

        with patch("app.tasks.action_orchestrator.run_action.delay") as delay:
            result = await _sweep_stale_action_runs_async()

        assert result["runs_failed"] == [swept.id]
        assert result["dispatched"] == [waiting.id]
        delay.assert_called_once_with(waiting.id)

    async def test_a_group_run_releases_the_members_it_was_running_on(self, db, workers):
        group, hosts = await _group_of(db, 2)
        busy_host = hosts[0].id
        run_id, _ = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["running", "queued"]
        )
        run = await _run(db, run_id)
        run.started_at = _ago(2 * 3000 + 3600 + 600)
        waiting = ActionRun(
            action_key=ACTION,
            action_version="1.0",
            host_id=busy_host,
            parameters={},
            parallelism=1,
            status="pending",
            created_at=_ago(300),
        )
        db.add(waiting)
        await db.commit()

        with patch("app.tasks.action_orchestrator.run_action.delay") as delay:
            result = await _sweep_stale_action_runs_async()

        assert result["runs_failed"] == [run_id]
        delay.assert_called_once_with(waiting.id)


# ---------------------------------------------------------------------------
# A per-host task sent twice runs once
# ---------------------------------------------------------------------------


class TestOnlyAQueuedRowIsClaimed:
    """A resumed run re-sends every row still ``queued``; the dead
    orchestrator may have sent some of them already. The second copy of a
    host's task must find the row taken and leave it alone."""

    @pytest.mark.parametrize("status", ["running", "succeeded", "cancelled", "pending"])
    async def test_the_pack_runner_leaves_a_row_that_is_not_queued(self, db, redis, status):
        from app.tasks.action_host import _run_action_host_async

        group, hosts = await _group_of(db, 1)
        run_id, (hr_id,) = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, [status], heartbeat_seconds_ago=5
        )
        before = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))
        ).scalar_one()
        started_before = before.started_at

        with patch("app.tasks.action_orchestrator.run_action.delay") as delay:
            await _run_action_host_async(run_id, hr_id)

        db.expire_all()
        after = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))
        ).scalar_one()
        assert (after.status, after.started_at) == (status, started_before)
        delay.assert_not_called()

    @pytest.mark.parametrize("with_lock", [True, False], ids=["locked", "sync"])
    @pytest.mark.parametrize("status", ["running", "succeeded"])
    async def test_a_built_in_leaves_a_row_that_is_not_queued(self, db, status, with_lock):
        from app.tasks.builtin_dispatchers import _begin_host_run

        group, hosts = await _group_of(db, 1)
        _, (hr_id,) = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, [status], heartbeat_seconds_ago=5
        )

        assert await _begin_host_run(hr_id, with_lock=with_lock) is None

        assert await _statuses(db, [hr_id]) == [status]

    async def test_a_queued_row_is_still_claimed(self, db):
        from app.tasks.builtin_dispatchers import _begin_host_run

        group, hosts = await _group_of(db, 1)
        _, (hr_id,) = await _run_left_by_a_dead_orchestrator(
            db, hosts, group.id, ["queued"], heartbeat_seconds_ago=5
        )

        assert await _begin_host_run(hr_id) == hosts[0].id
        assert await _statuses(db, [hr_id]) == ["running"]
