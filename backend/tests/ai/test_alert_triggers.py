"""Alert-triggered schedules: an alert fires on a host, an action runs on it.

The match and the guards (app.alerts.triggers), the order of things when
an alert arrives and the check afterwards (app.tasks.alert_handling), and
the API around them.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import insert, select

from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS
from app.ai.models import AlertEvent
from app.alerts.triggers import dispatch_for, parse_match
from app.config import settings
from app.models.action_run import ActionHostRun, ActionRun
from app.models.app_setting import AppSetting
from app.models.scheduled_action import ScheduledAction
from app.settings_service import invalidate_cache
from app.tasks import celery_app
from tests.conftest import create_group, create_host

pytestmark = [pytest.mark.usefixtures("sample_pack")]

TOKEN = "t" * MIN_WEBHOOK_TOKEN_CHARS


@pytest.fixture(autouse=True)
def strong_token(monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_token", TOKEN)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def sent():
    with patch.object(celery_app, "send_task") as send:
        yield send


@pytest.fixture
def tasks_use_test_db(db):
    @asynccontextmanager
    async def _cm():
        yield db

    with patch("app.db.task_session", new=_cm):
        yield


@pytest.fixture
def ai_policy():
    """The AI path, replaced: these tests are about what comes before it."""
    with patch("app.tasks.ai_alerts._decide_and_run", new=AsyncMock()) as decide:
        decide.return_value = {"outcome": "skipped_disabled"}
        yield decide


async def _set(db, key: str, value: str) -> None:
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


async def _trigger(
    db,
    *,
    target_kind: str = "host",
    target_id: int | None = None,
    match: str = "NginxDown",
    action_key: str = "example",
    cooldown: int = 60,
    fallback: bool = True,
    enabled: bool = True,
) -> ScheduledAction:
    sa = ScheduledAction(
        target_kind=target_kind,
        target_id=target_id,
        action_key=action_key,
        parameters={"marker_dir": "/tmp/x"},
        enabled=enabled,
        trigger="alert",
        alert_match=match,
        alert_cooldown_minutes=cooldown,
        investigate_on_failure=fallback,
        snapshot_enabled=False,
    )
    db.add(sa)
    await db.flush()
    return sa


async def _alert(
    db,
    host_id: int | None,
    alertname: str = "NginxDown",
    *,
    status: str = "firing",
    source: str = "grafana_webhook",
    **labels,
) -> AlertEvent:
    event = AlertEvent(
        source=source,
        fingerprint=uuid.uuid4().hex,
        alertname=alertname,
        severity="critical",
        status=status,
        labels={"alertname": alertname, **labels},
        annotations={},
        starts_at=datetime.now(UTC),
        host_id=host_id,
    )
    db.add(event)
    await db.flush()
    return event


# ---------------------------------------------------------------------------
# The match
# ---------------------------------------------------------------------------


class TestMatch:
    def test_names_and_labels(self) -> None:
        (rule,) = parse_match("NginxDown, severity=critical")
        assert rule.matches("NginxDown", {"severity": "critical"}, None, set())
        assert not rule.matches("NginxDown", {"severity": "warning"}, None, set())

    @pytest.mark.parametrize("text", ["NginxDown, host:web-1", "group:web"])
    def test_hosts_and_groups_belong_to_the_target(self, text: str) -> None:
        with pytest.raises(ValueError, match="come from the schedule's target"):
            parse_match(text)

    def test_an_empty_match_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one rule"):
            parse_match("  \n# only a comment\n")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_a_matching_alert_runs_the_action_on_its_host(self, db) -> None:
        host = await create_host(db)
        sa = await _trigger(db, target_id=host.id)
        event = await _alert(db, host.id)

        (dispatch,) = await dispatch_for(db, event)

        assert dispatch.outcome == "started"
        run = await db.get(ActionRun, dispatch.action_run_id)
        assert (run.host_id, run.group_id, run.target_kind) == (host.id, None, "host")
        assert (run.scheduled_action_id, run.alert_event_id) == (sa.id, event.id)
        assert run.parallelism == 1
        assert run.parameters == {"marker_dir": "/tmp/x"}
        assert run.snapshot_enabled is False
        assert run.triggered_by_user_id is None

    async def test_a_group_target_runs_on_the_member_only(self, db) -> None:
        group = await create_group(db)
        member = await create_host(db, group_ids=[group.id])
        outsider = await create_host(db)
        await _trigger(db, target_kind="group", target_id=group.id)

        (dispatch,) = await dispatch_for(db, await _alert(db, member.id))
        assert dispatch.outcome == "started"
        run = await db.get(ActionRun, dispatch.action_run_id)
        assert run.host_id == member.id
        assert await dispatch_for(db, await _alert(db, outsider.id)) == []

    async def test_a_fleet_target_covers_every_host(self, db) -> None:
        host = await create_host(db)
        await _trigger(db, target_kind="fleet")
        (dispatch,) = await dispatch_for(db, await _alert(db, host.id))
        assert dispatch.outcome == "started"

    @pytest.mark.parametrize(
        ("alertname", "labels"),
        [("DiskFull", {}), ("NginxDown", {"severity": "warning"})],
    )
    async def test_an_alert_that_does_not_match_does_nothing(self, db, alertname, labels):
        host = await create_host(db)
        await _trigger(db, target_kind="fleet", match="NginxDown, severity!=warning")
        assert await dispatch_for(db, await _alert(db, host.id, alertname, **labels)) == []

    async def test_a_label_set_in_grafana_can_be_the_match(self, db) -> None:
        host = await create_host(db)
        await _trigger(db, target_kind="fleet", match="labdog_action=restart-nginx")
        (dispatch,) = await dispatch_for(
            db, await _alert(db, host.id, "Anything", labdog_action="restart-nginx")
        )
        assert dispatch.outcome == "started"

    async def test_resolved_alerts_and_disabled_triggers_do_nothing(self, db) -> None:
        host = await create_host(db)
        await _trigger(db, target_id=host.id)
        assert await dispatch_for(db, await _alert(db, host.id, status="resolved")) == []

        other = await create_host(db)
        await _trigger(db, target_id=other.id, enabled=False)
        assert await dispatch_for(db, await _alert(db, other.id)) == []

    async def test_an_alert_with_no_host_says_so(self, db) -> None:
        await _trigger(db, target_kind="fleet")
        (dispatch,) = await dispatch_for(db, await _alert(db, None))
        assert dispatch.outcome == "skipped_no_host"
        assert dispatch.action_run_id is None

    async def test_a_short_token_keeps_webhook_alerts_from_running_anything(
        self, db, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings.alerts, "webhook_token", "short")
        host = await create_host(db)
        await _trigger(db, target_id=host.id)

        (dispatch,) = await dispatch_for(db, await _alert(db, host.id))
        assert dispatch.outcome == "skipped_token"

        # A polled alert carries no token, so the token does not decide it.
        (polled,) = await dispatch_for(db, await _alert(db, host.id, source="alertmanager_poll"))
        assert polled.outcome == "started"

    async def test_one_run_at_a_time_per_host(self, db) -> None:
        host = await create_host(db)
        await _trigger(db, target_id=host.id, cooldown=0)
        (first,) = await dispatch_for(db, await _alert(db, host.id))
        (second,) = await dispatch_for(db, await _alert(db, host.id))
        assert first.outcome == "started"
        assert second.outcome == "skipped_in_flight"
        assert f"run #{first.action_run_id}" in second.detail

    async def test_the_cooldown(self, db) -> None:
        host = await create_host(db)
        await _trigger(db, target_id=host.id, cooldown=60)
        (first,) = await dispatch_for(db, await _alert(db, host.id))
        run = await db.get(ActionRun, first.action_run_id)
        run.status = "succeeded"
        await db.flush()

        (second,) = await dispatch_for(db, await _alert(db, host.id))
        assert second.outcome == "skipped_cooldown"
        assert "cooldown 60 min" in second.detail

        run.created_at = datetime.now(UTC) - timedelta(minutes=61)
        await db.flush()
        (third,) = await dispatch_for(db, await _alert(db, host.id))
        assert third.outcome == "started"

    async def test_an_action_that_is_gone_fails_and_says_so(self, db) -> None:
        host = await create_host(db)
        await _trigger(db, target_id=host.id, action_key="removed-from-its-pack")
        (dispatch,) = await dispatch_for(db, await _alert(db, host.id))
        assert dispatch.outcome == "failed"
        assert "no longer registered" in dispatch.detail


# ---------------------------------------------------------------------------
# When an alert arrives
# ---------------------------------------------------------------------------


class TestHandleAlert:
    async def test_the_action_runs_and_the_ai_waits(
        self, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        await _trigger(db, target_id=host.id)
        event = await _alert(db, host.id)

        result = await _handle(event.id)

        assert result["outcome"] == "action"
        ai_policy.assert_not_awaited()
        run_id = result["actions"][0]["action_run_id"]
        sent.assert_called_once_with("app.tasks.action_orchestrator.run_action", args=[run_id])
        await db.refresh(event)
        assert event.investigation_outcome == "waiting_on_action"
        assert event.investigation_detail == f"example run #{run_id}"

    async def test_action_only(self, db, tasks_use_test_db, sent, ai_policy) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        await _trigger(db, target_id=host.id, fallback=False)
        event = await _alert(db, host.id)
        await _handle(event.id)
        await db.refresh(event)
        assert event.investigation_outcome == "handled_by_action"
        ai_policy.assert_not_awaited()

    async def test_no_matching_trigger_goes_to_the_ai_as_before(
        self, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        event = await _alert(db, host.id)
        await _handle(event.id)
        ai_policy.assert_awaited_once_with(event.id, already_tried=None)
        sent.assert_not_called()

    async def test_a_skipped_action_goes_to_the_ai_with_why(
        self, db, tasks_use_test_db, sent, ai_policy, monkeypatch
    ) -> None:
        from app.tasks.alert_handling import _handle

        monkeypatch.setattr(settings.alerts, "webhook_token", "short")
        host = await create_host(db)
        await _trigger(db, target_id=host.id)
        event = await _alert(db, host.id)
        await _handle(event.id)
        tried = ai_policy.await_args.kwargs["already_tried"]
        assert "did not run its action: example: token" in tried

    async def test_a_skipped_action_only_trigger_keeps_the_ai_out(
        self, db, tasks_use_test_db, sent, ai_policy, monkeypatch
    ) -> None:
        from app.tasks.alert_handling import _handle

        monkeypatch.setattr(settings.alerts, "webhook_token", "short")
        host = await create_host(db)
        await _trigger(db, target_id=host.id, fallback=False)
        event = await _alert(db, host.id)
        await _handle(event.id)
        ai_policy.assert_not_awaited()
        await db.refresh(event)
        assert event.investigation_outcome == "handled_by_action"
        assert "token" in event.investigation_detail

    async def test_the_webhook_hands_new_alerts_to_the_handler(
        self, db, external_client, sent
    ) -> None:
        await _set(db, "alerts.intake_enabled", "1")
        await db.commit()
        resp = await external_client.post(
            "/api/webhooks/grafana-alerts",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "NginxDown"},
                        "startsAt": "2026-10-15T10:00:00Z",
                        "fingerprint": uuid.uuid4().hex,
                    }
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        (call,) = sent.call_args_list
        assert call.args == ("app.tasks.alert_handling.handle_alert",)


# ---------------------------------------------------------------------------
# The check afterwards
# ---------------------------------------------------------------------------


async def _started(db, *, fallback: bool = True, output: str = ""):
    host = await create_host(db)
    await _trigger(db, target_id=host.id, fallback=fallback)
    event = await _alert(db, host.id)
    (dispatch,) = await dispatch_for(db, event)
    run = await db.get(ActionRun, dispatch.action_run_id)
    await db.execute(
        insert(ActionHostRun).values(
            action_run_id=run.id, host_id=host.id, hostname=host.hostname, output=output
        )
    )
    event.investigation_outcome = "waiting_on_action"
    await db.flush()
    return event, dispatch, run


class TestCheck:
    async def test_a_run_still_going_is_left_alone(self, db, tasks_use_test_db, ai_policy):
        from app.tasks.alert_handling import _check

        _, dispatch, _ = await _started(db)
        assert (await _check())["judged"] == 0
        assert dispatch.check_outcome is None

    async def test_resolved_after_the_run_is_fixed(self, db, tasks_use_test_db, ai_policy):
        from app.tasks.alert_handling import _check

        event, dispatch, run = await _started(db)
        run.status, run.finished_at = "succeeded", datetime.now(UTC)
        event.status = "resolved"
        await db.flush()

        await _check()

        assert dispatch.check_outcome == "fixed"
        assert event.investigation_outcome == "handled_by_action"
        assert event.investigation_detail == f"fixed by example run #{run.id}"
        ai_policy.assert_not_awaited()

    async def test_still_firing_after_the_window_goes_to_the_ai(
        self, db, tasks_use_test_db, ai_policy
    ):
        from app.tasks.alert_handling import _check

        event, dispatch, run = await _started(db, output="PLAY [x]\nok: [web]\nall done\n")
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.flush()

        assert (await _check())["judged"] == 0  # inside the 10-minute window
        run.finished_at = datetime.now(UTC) - timedelta(minutes=11)
        await db.flush()
        await _check()

        assert dispatch.check_outcome == "not_effective"
        ai_policy.assert_awaited_once()
        assert ai_policy.await_args.args == (event.id,)
        tried = ai_policy.await_args.kwargs["already_tried"]
        assert f"as run #{run.id}" in tried
        assert "The run succeeded, but the alert is still firing." in tried
        assert tried.count("The run succeeded") == 1
        assert "all done" in tried

    async def test_a_failed_run_goes_to_the_ai_at_once(self, db, tasks_use_test_db, ai_policy):
        from app.tasks.alert_handling import _check

        _, dispatch, run = await _started(db)
        run.status, run.error_message = "failed", "unreachable"
        await db.flush()
        await _check()
        assert dispatch.check_outcome == "run_failed"
        assert "The run failed: unreachable." in ai_policy.await_args.kwargs["already_tried"]

    async def test_action_only_never_reaches_the_ai(self, db, tasks_use_test_db, ai_policy):
        from app.tasks.alert_handling import _check

        _, dispatch, run = await _started(db, fallback=False)
        run.status = "failed"
        await db.flush()
        await _check()
        assert dispatch.check_outcome == "run_failed"
        ai_policy.assert_not_awaited()

    async def test_the_outcome_is_emailed(self, db, tasks_use_test_db, ai_policy) -> None:
        from app.tasks.alert_handling import _check

        with patch("app.notifications.service.notify", new=AsyncMock(return_value=1)) as notify:
            event, _, run = await _started(db)
            run.status, run.finished_at = "succeeded", datetime.now(UTC)
            event.status = "resolved"
            await db.flush()
            await _check()

        assert notify.await_args.args[1] == "alert_action"
        assert notify.await_args.kwargs["subject"].startswith("Example / System Probe fixed")
        assert notify.await_args.kwargs["link_path"] == f"/actions/runs/{run.id}"


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def _body(target_kind="host", target_id=None, **over) -> dict:
    return {
        "target_kind": target_kind,
        "target_id": target_id,
        "action_key": "example",
        "trigger": "alert",
        "alert_match": "NginxDown",
        "enabled": True,
        **over,
    }


class TestAPI:
    async def test_create_and_read_back(self, superuser_client, db) -> None:
        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            "/api/scheduled-actions",
            json=_body(
                "group",
                group.id,
                alert_match="  NginxDown\n\nseverity=critical  ",
                alert_cooldown_minutes=15,
                investigate_on_failure=False,
            ),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["trigger"] == "alert"
        assert data["alert_match"] == "NginxDown\n\nseverity=critical"
        assert (data["alert_cooldown_minutes"], data["investigate_on_failure"]) == (15, False)
        assert data["schedule_cron"] is None

    @pytest.mark.parametrize(
        ("over", "message"),
        [
            ({"alert_match": ""}, "needs alert_match"),
            ({"alert_match": "NginxDown, host:web-1"}, "schedule's target"),
            ({"schedule_cron": "0 3 * * *"}, "no cron expression"),
            ({"action_key": "_builtin.ai_task_group"}, "does not support host runs"),
        ],
    )
    async def test_refused(self, superuser_client, db, over, message) -> None:
        host = await create_host(db)
        await db.commit()
        resp = await superuser_client.post(
            "/api/scheduled-actions", json=_body("host", host.id, **over)
        )
        assert resp.status_code == 422, resp.text
        assert message in resp.text

    async def test_a_cron_schedule_and_an_alert_trigger_can_share_a_target(
        self, superuser_client, db
    ) -> None:
        host = await create_host(db)
        await db.commit()
        cron = {
            **_body("host", host.id),
            "trigger": "cron",
            "alert_match": None,
            "schedule_cron": "0 3 * * *",
        }
        assert (await superuser_client.post("/api/scheduled-actions", json=cron)).status_code == 201
        first = await superuser_client.post("/api/scheduled-actions", json=_body("host", host.id))
        assert first.status_code == 201, first.text
        again = await superuser_client.post("/api/scheduled-actions", json=_body("host", host.id))
        assert again.status_code == 409
        assert "An alert trigger already exists" in again.text

    async def test_the_trigger_cannot_change_and_run_now_is_refused(
        self, superuser_client, db
    ) -> None:
        host = await create_host(db)
        await db.commit()
        created = (
            await superuser_client.post("/api/scheduled-actions", json=_body("host", host.id))
        ).json()
        cron = {**_body("host", host.id), "trigger": "cron", "alert_match": None}
        put = await superuser_client.put(f"/api/scheduled-actions/{created['id']}", json=cron)
        assert put.status_code == 422
        run_now = await superuser_client.post(f"/api/scheduled-actions/{created['id']}/run-now")
        assert run_now.status_code == 409
        assert "no target to run now" in run_now.text

    async def test_would_have_matched(self, superuser_client, db) -> None:
        group = await create_group(db)
        member = await create_host(db, group_ids=[group.id])
        outsider = await create_host(db)
        hit = await _alert(db, member.id)
        await _alert(db, outsider.id)
        await _alert(db, member.id, "DiskFull")
        sa = await _trigger(db, target_kind="group", target_id=group.id)
        await db.commit()

        data = (await superuser_client.get(f"/api/scheduled-actions/{sa.id}/matches")).json()
        assert [(m["alert_event_id"], m["hostname"]) for m in data] == [(hit.id, member.hostname)]

    async def test_preview_before_saving(self, superuser_client, db) -> None:
        host = await create_host(db)
        hit = await _alert(db, host.id, "Nginx Down", team="web")
        await _alert(db, host.id, "Nginx Down", team="db")
        await db.commit()
        resp = await superuser_client.post(
            "/api/scheduled-actions/preview-matches",
            json={"target_kind": "fleet", "alert_match": "Nginx Down, team=web"},
        )
        assert resp.status_code == 200, resp.text
        assert [m["alert_event_id"] for m in resp.json()] == [hit.id]

        bad = await superuser_client.post(
            "/api/scheduled-actions/preview-matches",
            json={"target_kind": "fleet", "alert_match": "group:web"},
        )
        assert bad.status_code == 422
        assert "schedule's target" in bad.text

    async def test_alerts_runs_and_hosts_show_it(
        self, superuser_client, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        sa = await _trigger(db, target_id=host.id)
        event = await _alert(db, host.id)
        await _handle(event.id)
        await db.commit()

        alerts = (await superuser_client.get("/api/ai/alerts")).json()
        (row,) = [a for a in alerts if a["id"] == event.id]
        (action,) = row["actions"]
        assert (action["action_key"], action["outcome"], action["run_status"]) == (
            "example",
            "started",
            "queued",
        )
        assert row["investigation_outcome"] == "waiting_on_action"

        run = (await superuser_client.get(f"/api/actions/runs/{action['action_run_id']}")).json()
        assert (run["alert_event_id"], run["alertname"]) == (event.id, "NginxDown")

        view = (await superuser_client.get(f"/api/ai/full-auto/hosts/{host.id}")).json()
        assert [(a["id"], a["alert_match"]) for a in view["alert_actions"]] == [
            (sa.id, "NginxDown")
        ]


async def test_the_investigation_starts_from_what_was_tried(
    db, ai_provider, tasks_use_test_db, sent
) -> None:
    from app.ai.models import AISession
    from app.tasks.ai_alerts import _decide_and_run

    for key in ("ai.enabled", "ai.auto_investigate_enabled"):
        await _set(db, key, "1")
    host = await create_host(db)
    event = await _alert(db, host.id)

    decided = await _decide_and_run(event.id, already_tried="example ran as run #7.")

    assert decided["outcome"] == "started"
    session = await db.get(AISession, decided["session_id"])
    assert session.mission.endswith("## What LabDog already tried\n\nexample ran as run #7.")
