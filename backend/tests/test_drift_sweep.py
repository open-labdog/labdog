"""BUG-67: the periodic drift sweeps must not run on a claimed host.

All seven sweeps used to walk their hosts in one unlocked serial loop
inside a single transaction. Two failures came out of that:

* A sweep landing mid-sync read a half-applied host and wrote
  ``out_of_sync`` over the status the sync was maintaining, so a host
  showed as drifted seconds after a clean sync with nothing to say the
  verdict was stale.
* One transaction across the whole sweep meant a worker restart threw
  away every host's result, and one host's failure took the rest with it.

``app.tasks.drift_sweep.sweep_module`` is the shared driver that fixes
both. These tests drive it with a stub ``check_one`` so they assert the
driver's decisions — skip, discard, commit — rather than any one
module's collect-and-diff.

The sweep's own ``commit``/``rollback`` are recorded rather than
performed: the test session is savepoint-wrapped, so a real rollback
would take the fixtures with it. What matters here is which of the two
the driver chose for each host, and whether ``check_one`` ran at all.
"""

import contextlib

import pytest

from app.models.host_module_status import HostModuleStatus
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration

MODULE = "cron"


@pytest.fixture
def sweep_on(db, monkeypatch):
    """Point ``task_session()`` at the test session; record its outcomes.

    Yields the list of per-transaction outcomes in the order the driver
    produced them, e.g. ``["rollback", "commit"]``.
    """
    outcomes: list[str] = []

    @contextlib.asynccontextmanager
    async def _fake_task_session():
        yield db

    async def _commit():
        outcomes.append("commit")

    async def _rollback():
        outcomes.append("rollback")

    monkeypatch.setattr("app.db.task_session", _fake_task_session)
    monkeypatch.setattr(db, "commit", _commit)
    monkeypatch.setattr(db, "rollback", _rollback)
    return outcomes


async def _managed_host(db, *, status="in_sync", enabled=True):
    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)
    hms = HostModuleStatus(
        host_id=host.id,
        module_type=MODULE,
        sync_status=status,
        drift_check_enabled=enabled,
    )
    db.add(hms)
    await db.flush()
    return host, hms


async def _claim_with_sync(db, host_id):
    """A running SyncJob — what ``check_host_busy`` sees as a claim."""
    job = SyncJob(host_id=host_id, status=JobStatus.running, module_type="bulk")
    db.add(job)
    await db.flush()
    return job


class TestABusyHostIsLeftAlone:
    async def test_a_running_sync_means_the_check_never_runs(self, db, sweep_on):
        from app.tasks.drift_sweep import sweep_module

        host, hms = await _managed_host(db, status="in_sync")
        await _claim_with_sync(db, host.id)

        seen = []

        async def check_one(h, m, session):
            seen.append(h.id)
            m.sync_status = "out_of_sync"
            return True

        result = await sweep_module(MODULE, check_one)

        assert seen == [], "the sweep collected state from a host a sync was mid-way through"
        assert result["deferred"] == 1
        assert result["checked"] == 0
        assert hms.sync_status == "in_sync"

    async def test_a_free_host_is_checked_and_the_verdict_committed(self, db, sweep_on):
        from app.tasks.drift_sweep import sweep_module

        host, hms = await _managed_host(db, status="in_sync")

        async def check_one(h, m, session):
            m.sync_status = "out_of_sync"
            return True

        result = await sweep_module(MODULE, check_one)

        assert result["checked"] == 1
        assert result["deferred"] == 0
        assert hms.sync_status == "out_of_sync"
        assert sweep_on[-1] == "commit"


class TestAClaimDuringTheCheck:
    async def test_the_verdict_is_discarded_rather_than_written(self, db, sweep_on):
        """The window the lock alone cannot close.

        The host is free when the sweep starts, and a sync claims it
        while the SSH round trip is in flight. Writing the verdict now
        would overwrite the status the sync is maintaining, so the
        driver throws the whole per-host transaction away instead.
        """
        from app.tasks.drift_sweep import sweep_module

        host, hms = await _managed_host(db, status="in_sync")

        async def check_one(h, m, session):
            m.sync_status = "out_of_sync"
            await _claim_with_sync(session, h.id)
            return True

        result = await sweep_module(MODULE, check_one)

        assert result["deferred"] == 1
        assert result["checked"] == 0
        assert sweep_on[-1] == "rollback", (
            "a verdict collected before the claim was committed over the running sync"
        )


class TestPerHostIsolation:
    async def test_one_host_raising_does_not_stop_the_rest(self, db, sweep_on):
        """Six of the seven sweeps shared one transaction across every
        host, so a failed statement left it unusable for everyone after
        the failure. Each host now gets its own."""
        from app.tasks.drift_sweep import sweep_module

        first, _ = await _managed_host(db)
        second, second_hms = await _managed_host(db)
        order = sorted([first.id, second.id])

        async def check_one(h, m, session):
            if h.id == order[0]:
                raise RuntimeError("collector blew up")
            m.sync_status = "out_of_sync"
            return True

        result = await sweep_module(MODULE, check_one)

        assert result["failed"] == 1
        assert result["checked"] == 1, "a raising host took the rest of the sweep with it"
        assert result["deferred"] == 0

    async def test_a_module_declining_a_host_counts_as_skipped(self, db, sweep_on):
        from app.tasks.drift_sweep import sweep_module

        await _managed_host(db)

        async def check_one(h, m, session):
            return False

        result = await sweep_module(MODULE, check_one)

        assert result == {"checked": 0, "skipped": 1, "deferred": 0, "failed": 0}


class TestCandidateSelection:
    async def test_a_disabled_module_is_not_visited(self, db, sweep_on):
        from app.tasks.drift_sweep import sweep_module

        await _managed_host(db, enabled=False)

        seen = []

        async def check_one(h, m, session):
            seen.append(h.id)
            return True

        result = await sweep_module(MODULE, check_one)

        assert seen == []
        assert result["checked"] == 0

    async def test_the_firewall_sweep_keys_off_the_host_level_flag(self, db, sweep_on):
        """``host_gated=True``: firewall predates per-module toggles and
        still reads ``Host.drift_check_enabled``."""
        from app.tasks.drift_sweep import sweep_module

        ssh = await create_ssh_key(db)
        on = await create_host(db, ssh_key_id=ssh.id)
        off = await create_host(db, ssh_key_id=ssh.id)
        on.drift_check_enabled = True
        off.drift_check_enabled = False
        await db.flush()

        seen = []

        async def check_one(h, m, session):
            seen.append(h.id)
            return True

        await sweep_module("firewall", check_one, host_gated=True)

        assert seen == [on.id]
