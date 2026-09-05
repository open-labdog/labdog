"""BUG-65: deleting a host with run history must not fail.

``action_runs`` links to its target through ``ON DELETE SET NULL`` FKs,
under a CHECK that forbade all three target columns being NULL —
precisely the state ``SET NULL`` produced for a run that targeted only
the host being deleted. ``DELETE /api/hosts/{id}`` therefore raised

    new row for relation "action_runs" violates check constraint
    "ck_action_runs_ck_action_runs_scope"

and there was no way to remove the host short of manual SQL.

``ON DELETE CASCADE`` would have made the delete succeed by destroying
the audit trail at the moment an operator most needs it. Instead the run
describes its own target in ``target_kind``/``target_label``, which
survive the delete, and the CHECK now constrains only ``target_kind``.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.action_run import ActionRun
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


async def _plant_run(db, *, host_id=None, group_id=None, **kw):
    run = ActionRun(
        action_key="_builtin.collect_state",
        action_version="1.0",
        host_id=host_id,
        group_id=group_id,
        parameters={},
        parallelism=1,
        status="succeeded",
        **kw,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id


class TestTheTargetIsRecordedOnTheRun:
    async def test_a_host_run_records_the_hostname(self, superuser_client, db):
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        resp = await superuser_client.post(
            "/api/actions/runs",
            json={"action_key": "_builtin.collect_state", "host_id": host.id},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["target_kind"] == "host"
        assert body["target_label"] == host.hostname

    async def test_a_group_run_records_the_group_name(self, superuser_client, db):
        group = await create_group(db, name=f"g-{uuid.uuid4().hex[:8]}")
        resp = await superuser_client.post(
            "/api/actions/runs",
            json={"action_key": "_builtin.collect_state", "group_id": group.id},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["target_kind"] == "group"
        assert body["target_label"] == group.name

    async def test_a_run_inserted_without_them_still_gets_a_correct_kind(self, db):
        """The column defaults read the row's own FKs, so a call site that
        does not pass them cannot produce a row that is silently wrong."""
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        run_id = await _plant_run(db, host_id=host.id)
        run = await db.scalar(select(ActionRun).where(ActionRun.id == run_id))
        assert run.target_kind == "host"
        assert run.target_label == f"host {host.id}"


class TestDeletingATargetWithRunHistory:
    async def test_deleting_a_host_succeeds(self, superuser_client, db):
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        await _plant_run(db, host_id=host.id)

        resp = await superuser_client.delete(f"/api/hosts/{host.id}")
        assert resp.status_code == 204, resp.text

    async def test_the_run_survives_and_still_says_what_it_targeted(self, superuser_client, db):
        """The whole reason for preferring a discriminator over CASCADE:
        ``action_runs.output`` is often the only record of what was run
        against a host that is being deleted because something went wrong."""
        hostname = f"h-{uuid.uuid4().hex[:8]}"
        host = await create_host(db, hostname=hostname)
        run_id = await _plant_run(db, host_id=host.id, target_kind="host", target_label=hostname)

        assert (await superuser_client.delete(f"/api/hosts/{host.id}")).status_code == 204

        db.expire_all()
        run = await db.scalar(select(ActionRun).where(ActionRun.id == run_id))
        assert run is not None, "the run was cascaded away"
        assert run.host_id is None, "the FK should have been nulled"
        assert run.target_kind == "host"
        assert run.target_label == hostname

    async def test_deleting_a_group_succeeds_and_keeps_the_run(self, superuser_client, db):
        name = f"g-{uuid.uuid4().hex[:8]}"
        group = await create_group(db, name=name)
        run_id = await _plant_run(db, group_id=group.id, target_kind="group", target_label=name)

        assert (await superuser_client.delete(f"/api/groups/{group.id}")).status_code == 204

        db.expire_all()
        run = await db.scalar(select(ActionRun).where(ActionRun.id == run_id))
        assert run is not None
        assert run.group_id is None
        assert run.target_label == name

    async def test_the_api_still_serves_the_orphaned_run(self, superuser_client, db):
        """A run whose target is gone must still render — this is the
        history an operator opens *after* removing the host."""
        hostname = f"h-{uuid.uuid4().hex[:8]}"
        host = await create_host(db, hostname=hostname)
        run_id = await _plant_run(db, host_id=host.id, target_kind="host", target_label=hostname)
        await superuser_client.delete(f"/api/hosts/{host.id}")

        resp = await superuser_client.get(f"/api/actions/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["host_id"] is None
        assert body["target_label"] == hostname
