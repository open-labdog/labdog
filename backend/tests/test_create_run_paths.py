"""Characterisation tests for ``POST /api/actions/runs`` exits the suite leaves dark.

The validation, duplicate-guard, resolution and metrics tests drive the
common refusals. These pin the rest (a missing playbook, a group target for
a host-only action, an unknown metrics destination, a missing host or
group), which refusal wins when a request has several faults, and that a
failed dispatch still returns the queued run, so a restructuring of
``create_run`` has something to answer to.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app.actions.registry import ACTION_REGISTRY
from app.actions.validation import DRY_RUN_PARAM
from app.grafana.models import GrafanaInstance
from app.models.action_run import ActionRun
from tests.conftest import create_group, create_host

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("sample_pack")]

ALLOY_BACKEND = {"prometheus_push_var": "alloy_prometheus_url"}


@pytest.fixture
def sent():
    calls: list[tuple[str, list]] = []

    def _send_task(name, args=None, **_kwargs):
        calls.append((name, args))

    with patch("app.tasks.celery_app.send_task", side_effect=_send_task):
        yield calls


def _replaced(key: str, **changes):
    return patch.dict(ACTION_REGISTRY, {key: dataclasses.replace(ACTION_REGISTRY[key], **changes)})


async def _post(client, **body):
    return await client.post("/api/actions/runs", json=body)


async def test_a_missing_playbook_is_refused(superuser_client, db, sent):
    host = await create_host(db)
    with _replaced("example", playbook_path=Path("/nonexistent/playbook.yml")):
        resp = await _post(superuser_client, action_key="example", host_id=host.id)
    assert (resp.status_code, resp.json()) == (400, {"detail": "Playbook file not found"})
    assert sent == []


async def test_a_group_target_for_a_host_only_action_is_refused(superuser_client, db, sent):
    group = await create_group(db)
    with _replaced("example", supports_group=False):
        resp = await _post(superuser_client, action_key="example", group_id=group.id)
    assert resp.status_code == 422
    assert resp.json() == {"detail": "This action does not support group-scoped runs."}


async def test_a_host_target_for_a_group_only_action_is_refused(superuser_client, db, sent):
    host = await create_host(db)
    resp = await _post(superuser_client, action_key="k8s-upgrade", host_id=host.id)
    assert (resp.status_code, resp.json()) == (
        400,
        {"detail": "This action can only target a group."},
    )


@pytest.mark.parametrize("problem", ["missing", "wrong_kind"])
async def test_an_unknown_metrics_destination_is_refused(superuser_client, db, sent, problem):
    host = await create_host(db)
    loki = GrafanaInstance(name="loki-cr", kind="loki", url="https://loki-cr.test/push")
    db.add(loki)
    await db.flush()
    instance_id = 987654 if problem == "missing" else loki.id

    with _replaced("example", metrics_backend=dict(ALLOY_BACKEND)):
        resp = await _post(
            superuser_client,
            action_key="example",
            host_id=host.id,
            metrics_destinations={"mimir": instance_id},
        )

    assert resp.status_code == 422
    assert resp.json() == {"detail": f"No mimir destination with id {instance_id}."}


async def test_a_missing_host_is_404(superuser_client, db, sent):
    resp = await _post(superuser_client, action_key="example", host_id=987654)
    assert (resp.status_code, resp.json()) == (404, {"detail": "Host not found"})


async def test_a_missing_group_is_404(superuser_client, db, sent):
    resp = await _post(superuser_client, action_key="example", group_id=987654)
    assert (resp.status_code, resp.json()) == (404, {"detail": "Group not found"})


async def test_both_targets_are_refused_before_the_action_is_looked_up(superuser_client, sent):
    resp = await _post(superuser_client, action_key="no-such-action", host_id=1, group_id=1)
    assert resp.status_code == 422
    assert resp.json() == {"detail": "Exactly one of host_id or group_id must be provided."}


async def test_bad_parameters_are_refused_before_the_target_is_checked(superuser_client, sent):
    resp = await _post(
        superuser_client,
        action_key="example",
        host_id=987654,
        parameters={"not_a_parameter": 1},
    )
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)


async def test_a_missing_host_is_reported_before_a_duplicate_run(superuser_client, db, sent):
    db.add(
        ActionRun(
            action_key="example",
            action_version="1.0",
            parameters={},
            parallelism=1,
            status="running",
            group_id=(await create_group(db)).id,
        )
    )
    await db.flush()
    resp = await _post(superuser_client, action_key="example", host_id=987654)
    assert resp.status_code == 404


async def test_a_failed_dispatch_still_returns_the_queued_run(superuser_client, db, caplog):
    host = await create_host(db, hostname="dispatch-fails.test")
    with (
        patch("app.tasks.celery_app.send_task", side_effect=ConnectionError("broker down")),
        caplog.at_level(logging.WARNING, logger="app.api.actions"),
    ):
        resp = await _post(
            superuser_client, action_key="example", host_id=host.id, dry_run=True, parallelism=3
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["status"], body["host_id"], body["parallelism"]) == ("queued", host.id, 3)
    assert body["parameters"] == {DRY_RUN_PARAM: True}
    run_id = body["id"]
    assert f"Failed to dispatch actions.run task for run {run_id}: broker down" in caplog.text
    run = await db.get(ActionRun, run_id)
    assert (run.target_kind, run.target_label) == ("host", "dispatch-fails.test")


async def test_a_run_is_dispatched_to_the_orchestrator_after_it_is_stored(
    superuser_client, db, sent
):
    host = await create_host(db)
    resp = await _post(superuser_client, action_key="example", host_id=host.id)
    assert resp.status_code == 201, resp.text
    assert sent == [("app.tasks.action_orchestrator.run_action", [resp.json()["id"]])]
