"""BUG-121 / BUG-137: dispatches wait for the commit that makes their row.

``enqueue_ca_cert_action_for_host`` flushed its ``SyncJob`` and called
``.delay`` at once, leaving the commit to its five callers, and the
packages uninstall branch did the same inside its loop. A worker that
started first found no row. Both now register the dispatch with
``run_after_commit``.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.ca_certs.actions import enqueue_ca_cert_action_for_host
from app.db_after_commit import run_after_commit
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


async def test_a_callback_runs_on_commit_not_before(db):
    calls: list[str] = []
    run_after_commit(db, lambda: calls.append("ran"))
    await db.flush()
    assert calls == []

    await db.commit()

    assert calls == ["ran"]


async def test_a_rollback_drops_the_callback(db):
    calls: list[str] = []
    await db.execute(text("SELECT 1"))  # a transaction is open, as after a flush
    run_after_commit(db, lambda: calls.append("ran"))

    await db.rollback()
    await db.commit()

    assert calls == []


async def test_a_failing_callback_does_not_fail_the_commit(db):
    calls: list[str] = []

    def boom():
        raise RuntimeError("broker down")

    run_after_commit(db, boom)
    run_after_commit(db, lambda: calls.append("second"))

    await db.commit()

    assert calls == ["second"]


async def test_a_ca_cert_deploy_is_dispatched_after_the_callers_commit(db):
    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)

    with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as delay:
        job = await enqueue_ca_cert_action_for_host(host.id, db)
        assert job is not None
        delay.assert_not_called()

        await db.commit()

    delay.assert_called_once_with(job_id=job.id, host_id=host.id)
