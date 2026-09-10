"""``release_host_queue`` and the single-transaction claim it pairs with.

Five tasks used to hand-write the ``finally``-block release —
``action_host``, ``action_group``, ``builtin_dispatchers`` and
``host_sync_orchestrator`` twice. Each copy carried the same two
properties, and neither is visible from reading any one of them:

* it must never raise, or a broker hiccup in the release masks the real
  outcome of the task whose ``finally`` block is running;
* one host's failure must not skip the rest, or a single bad member
  wedges the queue of every other member of a group.

The second is the one a rewrite loses: moving the try/except from inside
the loop to around it looks like a tidy-up and silently turns a one-host
problem into a whole-group one.
"""

from contextlib import asynccontextmanager

import pytest

from app.tasks.host_lock import release_host_queue


class _StubSession:
    async def commit(self):
        return None


def _fake_sessions(counter: list[int]):
    @asynccontextmanager
    async def _session():
        counter.append(1)
        yield _StubSession()

    return _session


@pytest.fixture
def dispatched(monkeypatch):
    """Record which hosts were released; optionally make one of them fail."""
    seen: list[int] = []
    sessions: list[int] = []
    fail_on: set[int] = set()

    async def _fake_dispatch(_db, host_id, **_kwargs):
        seen.append(host_id)
        if host_id in fail_on:
            raise ConnectionError(f"redis gone while releasing {host_id}")
        return None

    monkeypatch.setattr("app.tasks.host_lock.dispatch_next_pending_for_host", _fake_dispatch)
    monkeypatch.setattr("app.db.task_session", _fake_sessions(sessions))
    return {"seen": seen, "sessions": sessions, "fail_on": fail_on}


class TestItReleasesEveryHostItWasGiven:
    async def test_a_single_host_id_is_accepted(self, dispatched):
        await release_host_queue(7, after="action_run_id=1")
        assert dispatched["seen"] == [7]

    async def test_a_list_releases_each_member_in_order(self, dispatched):
        await release_host_queue([3, 1, 2], after="action_run_id=1")
        assert dispatched["seen"] == [3, 1, 2]

    async def test_each_host_gets_its_own_session(self, dispatched):
        """One transaction per host: ``dispatch_next_pending_for_host`` takes
        that host's advisory lock, and holding several at once across one
        transaction is how the group path deadlocks against an overlapping
        group op."""
        await release_host_queue([1, 2, 3], after="action_run_id=1")
        assert len(dispatched["sessions"]) == 3

    async def test_an_empty_list_is_a_no_op(self, dispatched):
        await release_host_queue([], after="action_run_id=1")
        assert dispatched["seen"] == []
        assert dispatched["sessions"] == []


class TestOneFailureDoesNotCostTheOthers:
    async def test_a_failing_host_does_not_stop_the_rest(self, dispatched):
        dispatched["fail_on"].add(2)

        await release_host_queue([1, 2, 3], after="action_run_id=1")

        assert dispatched["seen"] == [1, 2, 3], (
            "host 2 failed; hosts 1 and 3 must still have been released — "
            "a try/except around the loop instead of inside it would have "
            "stopped at 2 and left host 3's queue stuck"
        )

    async def test_it_never_raises(self, dispatched):
        dispatched["fail_on"].update({1, 2, 3})
        # No pytest.raises: the point is that nothing escapes. This runs in
        # a finally block, where an exception would replace the task's real
        # outcome with this one.
        await release_host_queue([1, 2, 3], after="action_run_id=1")

    async def test_the_first_host_failing_still_releases_the_others(self, dispatched):
        dispatched["fail_on"].add(1)
        await release_host_queue([1, 2], after="action_run_id=1")
        assert dispatched["seen"] == [1, 2]


class TestTheClaimStillCommitsInOneTransaction:
    """BUG-62, from the other end.

    The gate (advisory lock + busy check) and the status flip have to
    commit together. When they did not, the lock was released with nothing
    yet marking the host busy, and a sync entering its own gate in that
    window claimed the same host — an action and a sync running against
    one host at once, which is the race the lock exists to prevent.

    Counting sessions is a blunt instrument, but it fails loudly for the
    specific edit that would reintroduce the bug: splitting the claim
    across two ``task_session()`` blocks.
    """

    async def test_action_host_claims_within_a_single_session(self, monkeypatch):
        from types import SimpleNamespace

        from app.tasks import action_host

        sessions: list[int] = []
        row = SimpleNamespace(
            id=1, host_id=9, status="queued", started_at=None, pending_reason=None
        )

        class _Result:
            def scalar_one_or_none(self):
                return row

        class _Session:
            async def execute(self, _stmt):
                return _Result()

            async def commit(self):
                return None

        @asynccontextmanager
        async def _session():
            sessions.append(1)
            yield _Session()

        async def _noop(*_a, **_k):
            return None

        monkeypatch.setattr("app.db.task_session", _session)
        monkeypatch.setattr("app.tasks.host_lock.acquire_host_lock", _noop)
        monkeypatch.setattr("app.tasks.host_lock.check_host_busy", _noop)

        ctx = action_host._RunCtx(
            action_run_id=1,
            host_run_id=1,
            channel="actions.run.1",
            r=None,
            private_data_dir="/tmp/none",
        )
        claimed = await action_host._claim_or_defer(ctx)

        assert claimed is True
        assert row.status == "running", "the flip must happen in the claim itself"
        assert row.started_at is not None
        assert ctx.claimed_host_id == 9
        assert len(sessions) == 1, (
            "the busy check and the status flip must share one transaction — see BUG-62"
        )


class _GroupResult:
    """One ``db.execute`` result: a row, or a scalar list."""

    def __init__(self, one=None, many=()):
        self._one = one
        self._many = list(many)

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        from types import SimpleNamespace

        return SimpleNamespace(all=lambda: list(self._many))


def _group_session(sessions: list[int], results: list[_GroupResult]):
    """A ``task_session`` stand-in handing back *results* in order."""
    queue = list(results)

    class _Session:
        async def execute(self, _stmt):
            # Past the end, keep handing back the first result — the run
            # row. That is deliberate: an edit that splits the claim into a
            # second session would re-read the run and flip it there, and
            # every assertion about the row would still pass. Only the
            # session count catches it, which is the point of the count.
            if queue:
                return queue.pop(0)
            return results[0] if results else _GroupResult()

        async def commit(self):
            return None

    @asynccontextmanager
    async def _factory():
        sessions.append(1)
        yield _Session()

    return _factory


class TestTheGroupClaimAlsoCommitsInOneTransaction:
    """BUG-62 on the path where it was *also* found.

    ``action_host._claim_or_defer`` has had this guard since the claim was
    a function. The group's claim was inline in ``_run_action_group_async``,
    so the same assertion could not be written for it — and the group is
    the path that holds *every* member's advisory lock, so splitting its
    gate from its flip opens the window on N hosts at once rather than one.

    The extraction exists for these tests. Nothing reuses the function.
    """

    @staticmethod
    def _patch(monkeypatch, sessions, results, *, blocker=None):
        from app.tasks import action_group

        async def _noop(*_a, **_k):
            return None

        async def _busy(*_a, **_k):
            return blocker

        async def _reason(*_a, **_k):
            return "sync in progress on web-01"

        monkeypatch.setattr("app.db.task_session", _group_session(sessions, results))
        monkeypatch.setattr("app.tasks.host_lock.acquire_host_locks", _noop)
        monkeypatch.setattr("app.tasks.host_lock.check_hosts_busy", _busy)
        monkeypatch.setattr("app.tasks.host_lock.format_pending_reason", _reason)
        return action_group

    async def test_it_claims_every_member_within_a_single_session(self, monkeypatch):
        from types import SimpleNamespace

        run = SimpleNamespace(
            id=7, group_id=3, status="queued", started_at=None, pending_reason=None
        )
        sessions: list[int] = []
        action_group = self._patch(
            monkeypatch,
            sessions,
            [_GroupResult(one=run), _GroupResult(many=[11, 12, 13])],
        )

        claimed = await action_group._claim_or_defer_group(7)

        assert claimed == [11, 12, 13]
        assert run.status == "running", "the flip must happen inside the claim itself"
        assert run.started_at is not None
        assert len(sessions) == 1, (
            "the busy check and the status flip must share one transaction — see BUG-62. "
            "The advisory locks are transaction-scoped: commit between them and every "
            "member is unlocked with nothing yet marking it busy."
        )

    async def test_a_busy_member_defers_the_whole_run(self, monkeypatch):
        from types import SimpleNamespace

        run = SimpleNamespace(
            id=7, group_id=3, status="queued", started_at=None, pending_reason=None
        )
        # One already-dispatched child row, and one that has finished. Only
        # the live one is parked; a finished row must not be resurrected.
        live = SimpleNamespace(status="queued", pending_reason=None)
        done = SimpleNamespace(status="succeeded", pending_reason=None)
        sessions: list[int] = []
        action_group = self._patch(
            monkeypatch,
            sessions,
            [
                _GroupResult(one=run),
                _GroupResult(many=[11, 12]),
                _GroupResult(many=[live, done]),
            ],
            blocker=SimpleNamespace(host_id=12),
        )

        claimed = await action_group._claim_or_defer_group(7)

        assert claimed is None, "a busy member must stop the caller, not claim nothing"
        assert run.status == "pending"
        assert run.pending_reason == "sync in progress on web-01"
        assert (live.status, live.pending_reason) == ("pending", "sync in progress on web-01")
        assert (done.status, done.pending_reason) == ("succeeded", None)
        assert run.started_at is None, "a deferred run has not started"
        assert len(sessions) == 1

    async def test_a_vanished_run_stops_the_caller(self, monkeypatch):
        sessions: list[int] = []
        action_group = self._patch(monkeypatch, sessions, [_GroupResult(one=None)])

        assert await action_group._claim_or_defer_group(7) is None

    async def test_a_run_with_no_group_proceeds_claiming_nothing(self, monkeypatch):
        """``[]`` is not a stop.

        The main loader has its own branch that fails a group run with no
        ``group_id`` and reports why. Returning ``None`` here would return
        from the task silently instead, leaving the run stuck as ``queued``.
        """
        from types import SimpleNamespace

        run = SimpleNamespace(id=7, group_id=None, status="queued", started_at=None)
        sessions: list[int] = []
        action_group = self._patch(monkeypatch, sessions, [_GroupResult(one=run)])

        assert await action_group._claim_or_defer_group(7) == []
        assert run.status == "queued", "nothing to claim means nothing to flip"

    async def test_an_empty_group_proceeds_claiming_nothing(self, monkeypatch):
        from types import SimpleNamespace

        run = SimpleNamespace(id=7, group_id=3, status="queued", started_at=None)
        sessions: list[int] = []
        action_group = self._patch(
            monkeypatch, sessions, [_GroupResult(one=run), _GroupResult(many=[])]
        )

        assert await action_group._claim_or_defer_group(7) == []
        assert run.status == "queued"
