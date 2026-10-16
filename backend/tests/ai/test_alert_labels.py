"""Grafana labels choose what an alert does.

``labdog_action`` runs an action from the list of actions alerts may run;
``labdog_ai`` and ``labdog_ai_automate`` ask for the AI. The label reading
and the guards (app.alerts.labels), the order of things when an alert
arrives and the check afterwards (app.tasks.alert_handling), the AI's
label check (app.tasks.ai_alerts), and the API around them
(app.api.alerts).
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
from app.alerts.labels import dispatch_for, is_true, requested_action
from app.alerts.models import AlertAction, AlertActionDispatch
from app.config import settings
from app.models.action_run import ActionHostRun, ActionRun
from app.models.app_setting import AppSetting
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


async def _allow(
    db,
    action_key: str = "example",
    *,
    target_kind: str = "fleet",
    target_id: int | None = None,
    cooldown: int = 60,
    enabled: bool = True,
) -> AlertAction:
    entry = AlertAction(
        action_key=action_key,
        target_kind=target_kind,
        target_id=target_id,
        parameters={"marker_dir": "/tmp/x"},
        snapshot_enabled=False,
        cooldown_minutes=cooldown,
        enabled=enabled,
    )
    db.add(entry)
    await db.flush()
    return entry


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
# Reading the labels
# ---------------------------------------------------------------------------


class TestLabels:
    @pytest.mark.parametrize(
        ("value", "expected"), [("true", True), ("True", False), ("yes", False), ("", False)]
    )
    def test_true_means_exactly_true(self, value: str, expected: bool) -> None:
        assert is_true({"labdog_ai": value}, "labdog_ai") is expected
        assert is_true(None, "labdog_ai") is False

    def test_the_action_label(self) -> None:
        assert requested_action({"labdog_action": " restart-nginx "}) == "restart-nginx"
        assert requested_action({"labdog_action": "  "}) is None
        assert requested_action({}) is None
        assert requested_action(None) is None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_an_allowed_action_runs_on_the_alerts_host(self, db) -> None:
        host = await create_host(db)
        entry = await _allow(db)
        event = await _alert(db, host.id, labdog_action="example")

        dispatch = await dispatch_for(db, event)

        assert dispatch.outcome == "started"
        assert dispatch.alert_action_id == entry.id
        run = await db.get(ActionRun, dispatch.action_run_id)
        assert (run.host_id, run.group_id, run.target_kind) == (host.id, None, "host")
        assert (run.scheduled_action_id, run.alert_event_id) == (None, event.id)
        assert run.parallelism == 1
        assert run.parameters == {"marker_dir": "/tmp/x"}
        assert run.snapshot_enabled is False
        assert run.triggered_by_user_id is None

    async def test_no_label_no_dispatch(self, db) -> None:
        host = await create_host(db)
        await _allow(db)
        assert await dispatch_for(db, await _alert(db, host.id)) is None

    async def test_a_resolved_alert_dispatches_nothing(self, db) -> None:
        host = await create_host(db)
        await _allow(db)
        event = await _alert(db, host.id, status="resolved", labdog_action="example")
        assert await dispatch_for(db, event) is None

    async def test_the_list_starts_empty(self, db) -> None:
        host = await create_host(db)
        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert dispatch.outcome == "not_allowed"
        assert dispatch.detail == "example is not on the list of actions alerts may run"
        assert dispatch.action_run_id is None
        assert dispatch.alert_action_id is None

    async def test_a_paused_entry_refuses(self, db) -> None:
        host = await create_host(db)
        await _allow(db, enabled=False)
        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert dispatch.outcome == "not_allowed"
        assert "paused" in dispatch.detail

    async def test_the_scope_holds(self, db) -> None:
        group = await create_group(db, name="web")
        member = await create_host(db, group_ids=[group.id])
        outsider = await create_host(db, hostname="db-1")
        await _allow(db, target_kind="group", target_id=group.id)

        inside = await dispatch_for(db, await _alert(db, member.id, labdog_action="example"))
        assert inside.outcome == "started"
        outside = await dispatch_for(db, await _alert(db, outsider.id, labdog_action="example"))
        assert outside.outcome == "not_allowed"
        assert outside.detail == "alerts may run example on group web, and db-1 is not one"

    async def test_a_host_scope(self, db) -> None:
        host = await create_host(db, hostname="web-1")
        other = await create_host(db)
        await _allow(db, target_kind="host", target_id=host.id)
        assert (
            await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        ).outcome == "started"
        assert (
            await dispatch_for(db, await _alert(db, other.id, labdog_action="example"))
        ).outcome == "not_allowed"

    async def test_an_unknown_action(self, db) -> None:
        host = await create_host(db)
        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="no-such"))
        assert dispatch.outcome == "unknown_action"
        assert dispatch.detail == "no action has the key 'no-such'"

    async def test_an_alert_with_no_host_says_so(self, db) -> None:
        await _allow(db)
        dispatch = await dispatch_for(db, await _alert(db, None, labdog_action="example"))
        assert dispatch.outcome == "skipped_no_host"
        assert dispatch.action_run_id is None

    async def test_a_short_token_keeps_webhook_alerts_from_running_anything(
        self, db, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings.alerts, "webhook_token", "short")
        host = await create_host(db)
        await _allow(db, cooldown=0)

        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert dispatch.outcome == "skipped_token"

        # A polled alert carries no token, so the token does not decide it.
        polled = await dispatch_for(
            db, await _alert(db, host.id, source="alertmanager_poll", labdog_action="example")
        )
        assert polled.outcome == "started"

    async def test_one_run_at_a_time_per_host(self, db) -> None:
        host = await create_host(db)
        await _allow(db, cooldown=0)
        first = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        second = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert first.outcome == "started"
        assert second.outcome == "skipped_in_flight"
        assert f"run #{first.action_run_id}" in second.detail

    async def test_a_run_started_by_hand_does_not_count(self, db) -> None:
        host = await create_host(db)
        await _allow(db)
        db.add(
            ActionRun(
                action_key="example",
                action_version="1",
                host_id=host.id,
                target_kind="host",
                target_label=host.hostname,
                parameters={},
                status="running",
            )
        )
        await db.flush()
        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert dispatch.outcome == "started"

    async def test_the_cooldown(self, db) -> None:
        host = await create_host(db)
        await _allow(db, cooldown=60)
        first = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        run = await db.get(ActionRun, first.action_run_id)
        run.status = "succeeded"
        await db.flush()

        second = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert second.outcome == "skipped_cooldown"
        assert "cooldown 60 min" in second.detail

        run.created_at = datetime.now(UTC) - timedelta(minutes=61)
        await db.flush()
        third = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert third.outcome == "started"

    async def test_whether_the_ai_follows_is_read_from_the_labels(self, db) -> None:
        host = await create_host(db)
        await _allow(db, cooldown=0)
        alone = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        assert alone.hand_to_ai is False

        other = await create_host(db)
        investigate = await dispatch_for(
            db, await _alert(db, other.id, labdog_action="example", labdog_ai="true")
        )
        assert investigate.hand_to_ai is True

        third = await create_host(db)
        automate = await dispatch_for(
            db, await _alert(db, third.id, labdog_action="example", labdog_ai_automate="true")
        )
        assert automate.hand_to_ai is True


# ---------------------------------------------------------------------------
# When an alert arrives
# ---------------------------------------------------------------------------


class TestHandleAlert:
    async def test_the_action_runs_and_the_ai_waits(
        self, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        await _allow(db)
        event = await _alert(db, host.id, labdog_action="example", labdog_ai="true")

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
        await _allow(db)
        event = await _alert(db, host.id, labdog_action="example")
        await _handle(event.id)
        await db.refresh(event)
        assert event.investigation_outcome == "handled_by_action"
        ai_policy.assert_not_awaited()

    async def test_no_action_label_goes_to_the_ai_policy(
        self, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        event = await _alert(db, host.id, labdog_ai="true")
        result = await _handle(event.id)
        ai_policy.assert_awaited_once_with(event.id, already_tried=None)
        assert result["actions"] == []
        sent.assert_not_called()

    async def test_an_action_not_run_goes_to_the_ai_policy_with_why(
        self, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        event = await _alert(db, host.id, labdog_action="linux-upgrade", labdog_ai="true")
        await _handle(event.id)
        tried = ai_policy.await_args.kwargs["already_tried"]
        assert tried.startswith(
            "The alert's labdog_action label asked for the action linux-upgrade, but LabDog "
            "did not run it:"
        )
        sent.assert_not_called()

    async def test_a_run_already_going_keeps_the_ai_out(
        self, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        await _allow(db, cooldown=0)
        await _handle((await _alert(db, host.id, labdog_action="example")).id)
        second = await _alert(db, host.id, labdog_action="example", labdog_ai="true")
        await _handle(second.id)
        ai_policy.assert_not_awaited()
        await db.refresh(second)
        assert second.investigation_outcome == "handled_by_action"
        assert "still going" in second.investigation_detail

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
                        "labels": {"alertname": "NginxDown", "labdog_action": "example"},
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
# The AI asks for its label
# ---------------------------------------------------------------------------


class TestTheAIWantsALabel:
    @pytest.fixture
    async def ai_on(self, db, ai_provider):
        for key in ("ai.enabled", "ai.auto_investigate_enabled"):
            await _set(db, key, "1")

    async def test_an_unlabelled_alert_is_not_investigated(
        self, db, ai_on, tasks_use_test_db, sent
    ) -> None:
        from app.tasks.ai_alerts import _decide_and_run

        host = await create_host(db)
        event = await _alert(db, host.id)
        decided = await _decide_and_run(event.id)
        assert decided["outcome"] == "skipped_no_label"
        await db.refresh(event)
        assert event.investigation_detail == (
            "not labelled labdog_ai=true or labdog_ai_automate=true"
        )

    @pytest.mark.parametrize("label", ["labdog_ai", "labdog_ai_automate"])
    async def test_either_label_asks_for_it(
        self, db, ai_on, tasks_use_test_db, sent, label
    ) -> None:
        from app.tasks.ai_alerts import _decide_and_run

        host = await create_host(db)
        event = await _alert(db, host.id, **{label: "true"})
        assert (await _decide_and_run(event.id))["outcome"] == "started"

    async def test_with_the_rules_on_a_matching_rule_asks_for_it(
        self, db, ai_on, tasks_use_test_db, sent
    ) -> None:
        from app.tasks.ai_alerts import _decide_and_run

        await _set(db, "ai.alert_full_auto_rules_enabled", "1")
        await _set(db, "ai.alert_full_auto_rules", "NginxDown")
        host = await create_host(db)
        assert (await _decide_and_run((await _alert(db, host.id)).id))["outcome"] == "started"
        other = await _alert(db, host.id, "DiskFull")
        assert (await _decide_and_run(other.id))["outcome"] == "skipped_no_label"

    async def test_the_button_needs_no_label(self, db, ai_on, superuser_client, sent) -> None:
        host = await create_host(db)
        event = await _alert(db, host.id)
        await db.commit()
        resp = await superuser_client.post(f"/api/alerts/{event.id}/investigate")
        assert resp.status_code == 200, resp.text
        assert resp.json()["investigation_session_id"] is not None

    async def test_the_investigation_starts_from_what_was_tried(
        self, db, ai_on, tasks_use_test_db, sent
    ) -> None:
        from app.ai.models import AISession
        from app.tasks.ai_alerts import _decide_and_run

        host = await create_host(db)
        event = await _alert(db, host.id, labdog_ai="true")

        decided = await _decide_and_run(event.id, already_tried="example ran as run #7.")

        assert decided["outcome"] == "started"
        session = await db.get(AISession, decided["session_id"])
        assert session.mission.endswith("## What LabDog already tried\n\nexample ran as run #7.")


# ---------------------------------------------------------------------------
# The check afterwards
# ---------------------------------------------------------------------------


async def _started(db, *, ai: bool = True, output: str = ""):
    host = await create_host(db)
    await _allow(db)
    labels = {"labdog_action": "example", **({"labdog_ai": "true"} if ai else {})}
    event = await _alert(db, host.id, **labels)
    dispatch = await dispatch_for(db, event)
    run = await db.get(ActionRun, dispatch.action_run_id)
    await db.execute(
        insert(ActionHostRun).values(
            action_run_id=run.id, host_id=host.id, hostname=host.hostname, output=output
        )
    )
    event.investigation_outcome = "waiting_on_action" if ai else "handled_by_action"
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
        assert f"as run #{run.id}, as the alert's labdog_action label asked." in tried
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

    async def test_without_an_ai_label_it_never_reaches_the_ai(
        self, db, tasks_use_test_db, ai_policy
    ):
        from app.tasks.alert_handling import _check

        _, dispatch, run = await _started(db, ai=False)
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
        assert "as its labdog_action label asked" in notify.await_args.kwargs["body"]
        assert notify.await_args.kwargs["link_path"] == f"/actions/runs/{run.id}"


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


class TestActionsAPI:
    async def test_allow_read_edit_and_remove(self, superuser_client, db) -> None:
        group = await create_group(db, name="web")
        await db.commit()

        resp = await superuser_client.post(
            "/api/alerts/actions",
            json={
                "action_key": "example",
                "target_kind": "group",
                "target_id": group.id,
                "parameters": {"marker_dir": "/tmp/x"},
                "cooldown_minutes": 15,
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert (created["action_name"], created["target_name"]) == ("Example / System Probe", "web")
        assert (created["cooldown_minutes"], created["enabled"]) == (15, True)
        assert created["last_run_id"] is None

        listed = (await superuser_client.get("/api/alerts/actions")).json()
        assert [e["id"] for e in listed] == [created["id"]]

        put = await superuser_client.put(
            f"/api/alerts/actions/{created['id']}",
            json={"action_key": "example", "target_kind": "fleet", "enabled": False},
        )
        assert put.status_code == 200, put.text
        assert (put.json()["target_name"], put.json()["enabled"]) == ("All hosts", False)

        gone = await superuser_client.delete(f"/api/alerts/actions/{created['id']}")
        assert gone.status_code == 204
        assert (await superuser_client.get("/api/alerts/actions")).json() == []

    async def test_a_second_entry_for_the_same_action_is_refused(
        self, superuser_client, db
    ) -> None:
        body = {"action_key": "example"}
        assert (await superuser_client.post("/api/alerts/actions", json=body)).status_code == 201
        again = await superuser_client.post("/api/alerts/actions", json=body)
        assert again.status_code == 409
        assert again.json()["detail"] == (
            "example is already on the list of actions alerts may run. Edit that entry instead."
        )

    @pytest.mark.parametrize(
        ("body", "status", "message"),
        [
            ({"action_key": "no-such"}, 422, "No action has the key"),
            ({"action_key": "_builtin.ai_task_group"}, 422, "cannot run on a single host"),
            ({"action_key": "example", "target_kind": "host"}, 422, "needs a target_id"),
            ({"action_key": "example", "target_id": 1}, 422, "takes no target_id"),
            ({"action_key": "example", "target_kind": "host", "target_id": 999999}, 404, "Host"),
            ({"action_key": "example", "parameters": {"fail_verify": "x"}}, 422, "fail_verify"),
        ],
    )
    async def test_refused(self, superuser_client, body, status, message) -> None:
        resp = await superuser_client.post("/api/alerts/actions", json=body)
        assert resp.status_code == status, resp.text
        assert message in resp.text

    async def test_the_action_cannot_change(self, superuser_client, db) -> None:
        created = (
            await superuser_client.post("/api/alerts/actions", json={"action_key": "example"})
        ).json()
        put = await superuser_client.put(
            f"/api/alerts/actions/{created['id']}", json={"action_key": "linux-upgrade"}
        )
        assert put.status_code == 422
        assert "cannot change" in put.text

    async def test_taking_an_action_off_keeps_what_alerts_did(self, superuser_client, db) -> None:
        host = await create_host(db)
        entry = await _allow(db)
        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        await db.commit()
        assert (await superuser_client.delete(f"/api/alerts/actions/{entry.id}")).status_code == 204
        await db.refresh(dispatch)
        assert (dispatch.alert_action_id, dispatch.action_key) == (None, "example")

    async def test_the_last_run_is_shown(self, superuser_client, db) -> None:
        host = await create_host(db)
        await _allow(db)
        dispatch = await dispatch_for(db, await _alert(db, host.id, labdog_action="example"))
        await db.commit()
        (entry,) = (await superuser_client.get("/api/alerts/actions")).json()
        assert (entry["last_run_id"], entry["last_run_status"]) == (
            dispatch.action_run_id,
            "queued",
        )


class TestWhereItShows:
    async def test_alerts_runs_and_hosts(
        self, superuser_client, db, tasks_use_test_db, sent, ai_policy
    ) -> None:
        from app.tasks.alert_handling import _handle

        host = await create_host(db)
        entry = await _allow(db)
        event = await _alert(db, host.id, labdog_action="example", labdog_ai="true")
        await _handle(event.id)
        await db.commit()

        alerts = (await superuser_client.get("/api/alerts")).json()
        (row,) = [a for a in alerts if a["id"] == event.id]
        (action,) = row["actions"]
        assert (action["action_key"], action["outcome"], action["run_status"]) == (
            "example",
            "started",
            "queued",
        )
        assert action["alert_action_id"] == entry.id
        assert row["investigation_outcome"] == "waiting_on_action"

        run = (await superuser_client.get(f"/api/actions/runs/{action['action_run_id']}")).json()
        assert (run["alert_event_id"], run["alertname"]) == (event.id, "NginxDown")

        view = (await superuser_client.get(f"/api/alerts/hosts/{host.id}")).json()
        assert [(a["id"], a["scope"]) for a in view["alert_actions"]] == [(entry.id, "all hosts")]

    async def test_the_old_paths_still_answer(self, superuser_client, db) -> None:
        host = await create_host(db)
        event = await _alert(db, host.id)
        await db.commit()
        rows = (await superuser_client.get("/api/ai/alerts")).json()
        assert event.id in [r["id"] for r in rows]
        # The button's old path reaches the same handler: with the AI off,
        # the same refusal as the new one.
        old = await superuser_client.post(f"/api/ai/alerts/{event.id}/investigate")
        new = await superuser_client.post(f"/api/alerts/{event.id}/investigate")
        assert (old.status_code, old.json()) == (new.status_code, new.json())

    async def test_the_host_page_says_full_auto_is_paused_after_a_fix_made_it_worse(
        self, superuser_client, db
    ) -> None:
        host = await create_host(db)
        event = await _alert(db, host.id)
        event.remediation_outcome = "made_worse"
        event.remediation_checked_at = datetime(2026, 10, 15, 16, 13, tzinfo=UTC)
        await db.commit()

        with patch("app.api.alerts.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
            view = (await superuser_client.get(f"/api/alerts/hosts/{host.id}")).json()
        assert (
            "an automatic fix made this host worse, so full auto is off it until "
            "2026-10-16 16:13 UTC"
        ) in view["blockers"]

    async def test_only_entries_whose_scope_covers_the_host(self, superuser_client, db) -> None:
        group = await create_group(db, name="web")
        member = await create_host(db, group_ids=[group.id])
        await create_host(db)
        await _allow(db, "example", target_kind="group", target_id=group.id)
        await _allow(db, "linux-upgrade", target_kind="host", target_id=member.id + 1000)
        await db.commit()
        view = (await superuser_client.get(f"/api/alerts/hosts/{member.id}")).json()
        assert [(a["action_key"], a["scope"]) for a in view["alert_actions"]] == [
            ("example", "group web")
        ]


async def test_dispatch_rows_are_cleaned_up_with_their_alert(db) -> None:
    host = await create_host(db)
    await _allow(db)
    event = await _alert(db, host.id, labdog_action="example")
    await dispatch_for(db, event)
    await db.delete(event)
    await db.flush()
    assert (await db.execute(select(AlertActionDispatch))).scalars().all() == []
