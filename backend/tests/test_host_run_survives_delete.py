"""BUG-77: deleting a host must not destroy its action transcripts.

BUG-65 stopped ``DELETE /api/hosts/{id}`` from failing and taught
``action_runs`` to describe its own target, so the parent run survives a
delete. It left the child table alone, and the child table is where the
evidence is: ``action_host_runs.output`` is the transcript of what
actually ran. ``host_id`` was ``ON DELETE CASCADE``, so every one of
those rows went with the host — the surviving run said *what* was
targeted and *that* it failed, and no longer said what happened.

The fix mirrors BUG-65 one level down: ``host_id`` nullable and
``SET NULL``, plus a ``hostname`` snapshot written at dispatch so the
row still has a label to show once there is nothing left to join to.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration

TRANSCRIPT = "PLAY [labdog] ***\nTASK [upgrade] ***\nfatal: [node-1]: FAILED! => rc=100\n"


async def _run_with_output(db, host, *, output=TRANSCRIPT, status="failed"):
    run = ActionRun(
        action_key="pkg.upgrade",
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status=status,
        target_kind="host",
        target_label=host.hostname,
    )
    db.add(run)
    await db.flush()
    hr = ActionHostRun(
        action_run_id=run.id,
        host_id=host.id,
        hostname=host.hostname,
        status=status,
        output=output,
        exit_code=100,
    )
    db.add(hr)
    await db.flush()
    await db.commit()
    return run.id, hr.id


class TestTheTranscriptSurvivesTheDelete:
    async def test_the_row_is_kept_with_its_output(self, superuser_client, db):
        """The case this exists for: a host is being removed *because*
        something went wrong, and the output is the record of what."""
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        _, hr_id = await _run_with_output(db, host)

        assert (await superuser_client.delete(f"/api/hosts/{host.id}")).status_code == 204

        db.expire_all()
        hr = await db.scalar(select(ActionHostRun).where(ActionHostRun.id == hr_id))
        assert hr is not None, "the host run was cascaded away with the host"
        assert hr.host_id is None, "the FK should have been nulled, not cascaded"
        assert hr.output == TRANSCRIPT
        assert hr.exit_code == 100

    async def test_the_hostname_snapshot_outlives_the_host(self, superuser_client, db):
        hostname = f"h-{uuid.uuid4().hex[:8]}"
        host = await create_host(db, hostname=hostname)
        _, hr_id = await _run_with_output(db, host)

        assert (await superuser_client.delete(f"/api/hosts/{host.id}")).status_code == 204

        db.expire_all()
        hr = await db.scalar(select(ActionHostRun).where(ActionHostRun.id == hr_id))
        assert hr.hostname == hostname, "nothing is left to join to; this is the only label"

    async def test_two_deleted_hosts_on_one_run_do_not_collide(self, superuser_client, db):
        """``uq_action_host_run`` is (action_run_id, host_id) and both rows
        end up NULL. PostgreSQL compares NULLs as distinct in a unique
        constraint, so this is legal — but only by that rule, so assert it."""
        a = await create_host(db, hostname=f"a-{uuid.uuid4().hex[:8]}", ip="10.77.0.1")
        b = await create_host(db, hostname=f"b-{uuid.uuid4().hex[:8]}", ip="10.77.0.2")
        # Read the names out now: the deletes below expire these rows, and
        # touching them afterwards is a lazy load in a sync context.
        names = {a.hostname, b.hostname}
        run = ActionRun(
            action_key="pkg.upgrade",
            action_version="1.0",
            group_id=None,
            parameters={},
            parallelism=2,
            status="failed",
            target_kind="fleet",
            target_label="All hosts",
        )
        db.add(run)
        await db.flush()
        for h in (a, b):
            db.add(
                ActionHostRun(
                    action_run_id=run.id,
                    host_id=h.id,
                    hostname=h.hostname,
                    status="failed",
                    output=f"log for {h.hostname}",
                )
            )
        await db.flush()
        run_id = run.id
        await db.commit()

        assert (await superuser_client.delete(f"/api/hosts/{a.id}")).status_code == 204
        assert (await superuser_client.delete(f"/api/hosts/{b.id}")).status_code == 204

        db.expire_all()
        rows = (
            (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert {r.host_id for r in rows} == {None}
        assert {r.hostname for r in rows} == names


class TestTheApiCanStillReachIt:
    async def test_the_run_detail_lists_the_orphaned_host_run(self, superuser_client, db):
        hostname = f"h-{uuid.uuid4().hex[:8]}"
        host = await create_host(db, hostname=hostname)
        run_id, hr_id = await _run_with_output(db, host)

        assert (await superuser_client.delete(f"/api/hosts/{host.id}")).status_code == 204

        body = (await superuser_client.get(f"/api/actions/runs/{run_id}")).json()
        assert len(body["host_runs"]) == 1
        row = body["host_runs"][0]
        assert row["host_id"] is None
        assert row["hostname"] == hostname, "the snapshot should fill in for the outer join"

    async def test_the_output_is_readable_by_host_run_id(self, superuser_client, db):
        """A transcript you cannot open is not preserved in any useful
        sense. The by-host-id route cannot reach these rows, so the UI
        addresses them by the row's own id."""
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        run_id, hr_id = await _run_with_output(db, host)

        assert (await superuser_client.delete(f"/api/hosts/{host.id}")).status_code == 204

        resp = await superuser_client.get(f"/api/actions/runs/{run_id}/host-runs/{hr_id}/output")
        assert resp.status_code == 200, resp.text
        assert resp.text == TRANSCRIPT

    async def test_the_by_row_route_will_not_cross_runs(self, superuser_client, db):
        """The row id alone would let any run id read any transcript."""
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        other = await create_host(db, hostname=f"o-{uuid.uuid4().hex[:8]}", ip="10.77.1.1")
        _, hr_id = await _run_with_output(db, host)
        other_run_id, _ = await _run_with_output(db, other)

        resp = await superuser_client.get(
            f"/api/actions/runs/{other_run_id}/host-runs/{hr_id}/output"
        )
        assert resp.status_code == 404

    async def test_a_live_host_still_reads_by_host_id(self, superuser_client, db):
        """The original route keeps working while the host exists."""
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        run_id, _ = await _run_with_output(db, host)

        resp = await superuser_client.get(f"/api/actions/runs/{run_id}/hosts/{host.id}/output")
        assert resp.status_code == 200
        assert resp.text == TRANSCRIPT


class TestTheClaimProtocolIgnoresOrphans:
    async def test_a_nulled_running_row_does_not_hold_a_host(self, db):
        """``check_host_busy`` matches on ``host_id``, and NULL matches
        nothing — which is the answer we want: a host that no longer
        exists cannot be blocking anything."""
        from app.tasks.host_lock import acquire_host_lock, check_host_busy

        ssh = await create_ssh_key(db)
        gone = await create_host(db, hostname=f"g-{uuid.uuid4().hex[:8]}", ssh_key_id=ssh.id)
        live = await create_host(
            db, hostname=f"l-{uuid.uuid4().hex[:8]}", ssh_key_id=ssh.id, ip="10.77.2.1"
        )
        run = ActionRun(
            action_key="pkg.upgrade",
            action_version="1.0",
            parameters={},
            parallelism=1,
            status="running",
            target_kind="fleet",
            target_label="All hosts",
        )
        db.add(run)
        await db.flush()
        db.add(
            ActionHostRun(
                action_run_id=run.id,
                host_id=gone.id,
                hostname=gone.hostname,
                status="running",
            )
        )
        await db.flush()
        await db.commit()

        await db.delete(gone)
        await db.flush()

        await acquire_host_lock(db, live.id)
        assert await check_host_busy(db, live.id) is None


class TestDispatchWritesTheSnapshot:
    async def test_the_orchestrator_records_the_hostname(self, superuser_client, db):
        """A row created without a hostname would only reveal itself once
        the host was gone, which is far too late to notice."""
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        from app.tasks.action_orchestrator import _run_action_async

        hostname = f"h-{uuid.uuid4().hex[:8]}"
        ssh = await create_ssh_key(db)
        host = await create_host(db, hostname=hostname, ssh_key_id=ssh.id)
        await db.commit()

        resp = await superuser_client.post(
            "/api/actions/runs",
            json={"action_key": "_builtin.collect_state", "host_id": host.id},
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]

        @asynccontextmanager
        async def _fake_session():
            yield db

        class _Result:
            def join(self, *args, **kwargs):  # noqa: ARG002
                return []

        def _group(sig_iter):
            list(sig_iter)

            class _G:
                def apply_async(self):
                    return _Result()

            return _G()

        with (
            patch("app.db.task_session", new=_fake_session),
            patch("redis.from_url", return_value=_FakeRedis()),
            patch("app.tasks.action_orchestrator.celery_app.signature", return_value=("sig",)),
            patch("celery.group", side_effect=_group),
        ):
            await _run_action_async(run_id)

        db.expire_all()
        rows = (
            (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
            .scalars()
            .all()
        )
        assert [r.hostname for r in rows] == [hostname]


class _FakeRedis:
    """Enough of the redis client for the orchestrator's cancel probe."""

    def exists(self, *_args):
        return 0

    def publish(self, *_args, **_kwargs):
        return None
