"""BUG-74: collecting host state is queued, not run inside the request.

``POST /hosts/{id}/collect-state`` used to open an SSH connection and
run seven collectors serially while holding a pooled database
connection, and took no host lock. The dashboard's "Check all" fans that
out over every host at once, so a handful of unresponsive hosts drained
the 5+10 pool and every unrelated request began failing on
``pool_timeout``. Run one during a sync and it overwrote the module
status the sync was writing.

It now creates an ``_builtin.collect_state`` run and returns 202. The
work happens on the queue, under the same host claim every other
per-host operation takes.
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionRun
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_broker():
    """Keep ``create_run``'s dispatch off a broker that is not there.

    ``send_task`` retries a missing Redis twenty times at one second
    each before the handler's ``except`` swallows it, which turns every
    POST in this file into a twenty-second wait.

    Patched on the class, like ``test_scheduled_action_schedule`` does.
    Setting the attribute on the app *instance* instead leaves an
    instance attribute shadowing the class method for the rest of the
    session — monkeypatch restores what ``getattr`` found, which is the
    inherited method — and every later test that patches the class then
    silently talks to the real broker.
    """
    with patch("celery.app.base.Celery.send_task"):
        yield


async def _host(db, **kwargs):
    ssh = await create_ssh_key(db)
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}", ssh_key_id=ssh.id, **kwargs)
    await db.commit()
    return host


class TestTheRequestOnlyQueues:
    async def test_it_answers_202_with_a_run_to_poll(self, superuser_client, db):
        host = await _host(db)

        resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state")

        assert resp.status_code == 202, resp.text
        body = resp.json()
        run = await db.scalar(select(ActionRun).where(ActionRun.id == body["run_id"]))
        assert run is not None
        assert run.action_key == "_builtin.collect_state"
        assert run.host_id == host.id

    async def test_no_collector_runs_on_the_request_path(self, superuser_client, db):
        """The point of the fix: the handler must return without ever
        opening an SSH connection or holding one for the duration."""
        host = await _host(db)

        async def _explode(*_args, **_kwargs):  # pragma: no cover - guard
            raise AssertionError("collectors ran inside the request")

        with patch("app.api.host_state.collect_module_state", new=_explode):
            resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state")

        assert resp.status_code == 202

    async def test_a_single_module_is_carried_on_the_run(self, superuser_client, db):
        host = await _host(db)

        resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state?module=firewall")

        assert resp.status_code == 202, resp.text
        run = await db.scalar(select(ActionRun).where(ActionRun.id == resp.json()["run_id"]))
        assert run.parameters["module"] == "firewall"

    async def test_collect_all_carries_no_module(self, superuser_client, db):
        host = await _host(db)
        resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state")
        run = await db.scalar(select(ActionRun).where(ActionRun.id == resp.json()["run_id"]))
        assert run.parameters["module"] is None

    async def test_an_unknown_module_is_rejected_before_a_run_exists(self, superuser_client, db):
        host = await _host(db)

        resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state?module=nonsense")

        assert resp.status_code == 400
        runs = (
            (await db.execute(select(ActionRun).where(ActionRun.host_id == host.id)))
            .scalars()
            .all()
        )
        assert runs == [], "a bad module name should not leave a run behind"

    async def test_a_missing_host_is_404(self, superuser_client):
        assert (await superuser_client.post("/api/hosts/999999/collect-state")).status_code == 404

    async def test_a_host_without_a_key_is_400(self, superuser_client, db):
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        await db.commit()
        resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state")
        assert resp.status_code == 400


class TestPressingTheButtonTwice:
    """The dashboard fires one of these per host and an impatient
    operator fires more. ``create_run`` already refuses a second
    identical run with 409 — its advisory lock is per-transaction, so it
    cannot be provoked through a test client that shares one session
    across both requests. What is asserted here is the half this change
    owns: that the refusal is turned into the run already in flight
    rather than an error the operator sees for pressing a button twice.
    """

    async def test_a_409_from_create_run_becomes_the_running_run(self, superuser_client, db):
        from fastapi import HTTPException

        host = await _host(db)

        async def _already_running(*_args, **_kwargs):
            raise HTTPException(
                status_code=409,
                detail={
                    "detail": "An identical action is already running",
                    "running_run_id": 4242,
                },
            )

        with patch("app.api.actions.create_run", new=_already_running):
            resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state")

        assert resp.status_code == 202, resp.text
        assert resp.json() == {"run_id": 4242, "status": "running", "already_running": True}

    async def test_any_other_error_is_not_swallowed(self, superuser_client, db):
        from fastapi import HTTPException

        host = await _host(db)

        async def _unresolved(*_args, **_kwargs):
            raise HTTPException(status_code=409, detail={"kind": "action_unresolved"})

        with patch("app.api.actions.create_run", new=_unresolved):
            resp = await superuser_client.post(f"/api/hosts/{host.id}/collect-state")

        assert resp.status_code == 409, "a 409 with no run to join must reach the caller"


class TestTheDispatcherDoesTheWork:
    async def test_it_collects_the_module_the_run_names(self, db, monkeypatch):
        from app.tasks import builtin_dispatchers

        host = await _host(db)
        run = ActionRun(
            action_key="_builtin.collect_state",
            action_version="1.1.0",
            host_id=host.id,
            parameters={"module": "cron"},
            parallelism=1,
            status="running",
            target_kind="host",
            target_label=host.hostname,
        )
        db.add(run)
        await db.flush()

        seen: list[tuple[int, str | None]] = []

        async def _fake_collect(host_id, module, _db):
            seen.append((host_id, module))
            return [], ["a competing ruleset is present"]

        finished: dict = {}

        async def _fake_begin(host_run_id, **_kwargs):  # noqa: ARG001
            return host.id

        async def _fake_finish(host_run_id, **kwargs):
            finished.update(kwargs)

        monkeypatch.setattr("app.api.host_state.collect_module_state", _fake_collect)
        monkeypatch.setattr(builtin_dispatchers, "_begin_host_run", _fake_begin)
        monkeypatch.setattr(builtin_dispatchers, "_finish_host_run", _fake_finish)

        async def _fake_params(_run_id):
            return {"module": "cron"}

        monkeypatch.setattr(builtin_dispatchers, "_load_action_run_parameters", _fake_params)

        await builtin_dispatchers._collect_state_async(run.id, 1)

        assert seen == [(host.id, "cron")]
        assert finished["succeeded"] is True
        assert finished["output"] == "a competing ruleset is present"

    async def test_a_collection_failure_fails_the_host_run(self, db, monkeypatch):
        from app.tasks import builtin_dispatchers

        host = await _host(db)
        finished: dict = {}

        async def _boom(*_args, **_kwargs):
            raise LookupError("Host 1 has no SSH key assigned")

        async def _fake_begin(host_run_id, **_kwargs):  # noqa: ARG001
            return host.id

        async def _fake_finish(host_run_id, **kwargs):
            finished.update(kwargs)

        async def _fake_params(_run_id):
            return {"module": None}

        monkeypatch.setattr("app.api.host_state.collect_module_state", _boom)
        monkeypatch.setattr(builtin_dispatchers, "_begin_host_run", _fake_begin)
        monkeypatch.setattr(builtin_dispatchers, "_finish_host_run", _fake_finish)
        monkeypatch.setattr(builtin_dispatchers, "_load_action_run_parameters", _fake_params)

        await builtin_dispatchers._collect_state_async(1, 1)

        assert finished["succeeded"] is False
        assert "no SSH key" in finished["error"]


class TestTheModuleListsAgree:
    def test_the_action_offers_exactly_the_collectable_modules(self):
        from app.actions.builtins import _COLLECTABLE_MODULES
        from app.api.host_state import COLLECTABLE_MODULES

        assert set(_COLLECTABLE_MODULES) == COLLECTABLE_MODULES

    async def test_the_constant_matches_what_is_actually_built(self, db):
        from app.api.host_state import COLLECTABLE_MODULES, _build_collectors

        host = await _host(db)
        collectors = _build_collectors(host, "", "root", db)
        assert set(collectors) == COLLECTABLE_MODULES
