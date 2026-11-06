"""The run page's "Investigate with AI" button.

``POST /api/actions/runs/{id}/investigate`` starts a read-only assistant
session about a finished run, or reopens the one already started for the
same hosts.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.ai.models import AIMessage, AISession
from app.models.action_run import ActionHostRun, ActionRun
from app.models.app_setting import AppSetting
from app.settings_service import invalidate_cache
from app.tasks import celery_app
from tests.conftest import create_host

LOG = '[preflight] host reachable\nTASK [t] ***\nfatal: [h]: FAILED! => {\n    "msg": "boom"\n}'


@pytest.fixture
def sent():
    with patch.object(celery_app, "send_task") as send:
        yield send


async def _set(db, key: str, value: str) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


@pytest.fixture
async def ai_on(db, ai_provider):
    await _set(db, "ai.enabled", "1")
    return ai_provider


async def _run(db, hosts, *, status="failed", outputs=None):
    run = ActionRun(
        action_key="linux-upgrade",
        action_version="1.0",
        host_id=hosts[0].id if len(hosts) == 1 else None,
        parameters={},
        parallelism=1,
        status=status,
    )
    db.add(run)
    await db.flush()
    host_runs = []
    for i, host in enumerate(hosts):
        hr = ActionHostRun(
            action_run_id=run.id,
            host_id=host.id,
            hostname=host.hostname,
            status=status,
            output=(outputs or {}).get(i, LOG),
        )
        db.add(hr)
        host_runs.append(hr)
    await db.flush()
    await db.commit()
    return run, host_runs


async def _investigate(client, run_id, **body):
    return await client.post(f"/api/actions/runs/{run_id}/investigate", json=body or None)


class TestStartingOne:
    async def test_a_read_only_chat_about_the_run(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])

        resp = await _investigate(superuser_client, run.id)

        assert resp.status_code == 201, resp.text
        assert resp.json()["created"] is True
        session = await db.get(AISession, resp.json()["session_id"])
        assert session.mode == "chat"
        assert session.autonomy_level == "read_only"
        assert session.target_host_ids == [host.id]
        assert session.investigates_action_run_id == run.id
        # Not the owning-run link: the busy guard lets an owner's session
        # past that run's hold on the host.
        assert session.action_run_id is None
        assert session.title == f"Run #{run.id}: linux-upgrade"
        assert "fatal: [h]: FAILED!" in session.mission
        sent.assert_called_once_with(
            "app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id}
        )

        roles = (
            await db.execute(
                select(AIMessage.role, AIMessage.content)
                .where(AIMessage.session_id == session.id)
                .order_by(AIMessage.seq)
            )
        ).all()
        assert [r.role for r in roles] == ["system", "user"]
        assert roles[1].content == session.mission

    async def test_one_host_of_a_group_run(self, db, ai_on, superuser_client, sent):
        hosts = [await create_host(db), await create_host(db)]
        run, host_runs = await _run(db, hosts)

        resp = await _investigate(superuser_client, run.id, host_run_id=host_runs[1].id)

        assert resp.status_code == 201, resp.text
        session = await db.get(AISession, resp.json()["session_id"])
        assert session.target_host_ids == [hosts[1].id]
        assert session.title == f"Run #{run.id}: linux-upgrade on {hosts[1].hostname}"
        assert hosts[0].hostname not in session.mission

    async def test_the_whole_group(self, db, ai_on, superuser_client, sent):
        hosts = [await create_host(db), await create_host(db)]
        run, _ = await _run(db, hosts)
        resp = await _investigate(superuser_client, run.id)
        session = await db.get(AISession, resp.json()["session_id"])
        assert session.target_host_ids == sorted(h.id for h in hosts)

    async def test_audited(self, db, ai_on, superuser_client, sent):
        from app.models.audit_log import AuditLog

        host = await create_host(db)
        run, _ = await _run(db, [host])
        session_id = (await _investigate(superuser_client, run.id)).json()["session_id"]
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "ai_session_created", AuditLog.entity_id == session_id
                )
            )
        ).scalar_one()
        assert row.after_state["investigates_action_run_id"] == run.id


class TestPressingItAgain:
    async def test_reopens_the_same_session(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        first = (await _investigate(superuser_client, run.id)).json()

        again = await _investigate(superuser_client, run.id)

        assert again.status_code == 200
        assert again.json() == {"session_id": first["session_id"], "created": False}
        assert sent.call_count == 1

    async def test_another_host_is_another_session(self, db, ai_on, superuser_client, sent):
        hosts = [await create_host(db), await create_host(db)]
        run, host_runs = await _run(db, hosts)
        whole = (await _investigate(superuser_client, run.id)).json()["session_id"]
        one = (await _investigate(superuser_client, run.id, host_run_id=host_runs[0].id)).json()

        assert one["created"] is True and one["session_id"] != whole

    @pytest.mark.parametrize("status", ["failed", "cancelled"])
    async def test_starts_afresh_after_one_failed(self, db, ai_on, superuser_client, sent, status):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        first = (await _investigate(superuser_client, run.id)).json()["session_id"]
        (await db.get(AISession, first)).status = status
        await db.commit()

        again = await _investigate(superuser_client, run.id)

        assert again.status_code == 201
        assert again.json()["session_id"] != first

    async def test_reopening_needs_no_budget_or_provider(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        first = (await _investigate(superuser_client, run.id)).json()["session_id"]
        await _set(db, "ai.enabled", "0")
        await db.commit()

        again = await _investigate(superuser_client, run.id)

        assert again.status_code == 200 and again.json()["session_id"] == first


class TestRefusals:
    async def test_a_live_run(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host], status="running")
        resp = await _investigate(superuser_client, run.id)
        assert resp.status_code == 409
        assert "once it has finished" in resp.json()["detail"]
        sent.assert_not_called()

    async def test_ai_off(self, db, ai_provider, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        resp = await _investigate(superuser_client, run.id)
        assert resp.status_code == 409
        assert "ai.enabled" in resp.json()["detail"]
        assert (await db.execute(select(AISession.id))).first() is None

    async def test_every_host_deleted(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        await db.delete(host)
        await db.commit()
        resp = await _investigate(superuser_client, run.id)
        assert resp.status_code == 409
        assert "deleted" in resp.json()["detail"]

    async def test_a_host_run_of_another_run(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        _, other_rows = await _run(db, [host])
        resp = await _investigate(superuser_client, run.id, host_run_id=other_rows[0].id)
        assert resp.status_code == 404

    async def test_no_such_run(self, superuser_client, sent):
        assert (await _investigate(superuser_client, 987654)).status_code == 404


class TestTheRunPageShowsIt:
    async def test_investigations_on_the_run(self, db, ai_on, superuser_client, sent):
        hosts = [await create_host(db), await create_host(db)]
        run, host_runs = await _run(db, hosts)
        whole = (await _investigate(superuser_client, run.id)).json()["session_id"]
        one = (await _investigate(superuser_client, run.id, host_run_id=host_runs[1].id)).json()[
            "session_id"
        ]

        body = (await superuser_client.get(f"/api/actions/runs/{run.id}")).json()

        assert [i["session_id"] for i in body["investigations"]] == [one, whole]
        assert body["investigations"][0]["target_host_ids"] == [hosts[1].id]
        assert body["investigations"][0]["status"] == "queued"

    async def test_none(self, db, superuser_client):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        assert (await superuser_client.get(f"/api/actions/runs/{run.id}")).json()[
            "investigations"
        ] == []

    async def test_the_session_names_the_run(self, db, ai_on, superuser_client, sent):
        host = await create_host(db)
        run, _ = await _run(db, [host])
        session_id = (await _investigate(superuser_client, run.id)).json()["session_id"]
        body = (await superuser_client.get(f"/api/ai/sessions/{session_id}")).json()
        assert body["investigates_action_run_id"] == run.id
