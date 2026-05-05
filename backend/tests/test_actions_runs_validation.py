"""Tests for the parameter-validation gate on POST /api/actions/runs.

Covers C3 scope: missing-required, type-mismatch, unknown-key all
return 422 with a structured error body. Built-in actions are rejected
with 400 from the C2 guard until C5 wires per-host dispatch.

Uses ``conftest.create_host`` and the ``superuser_client`` fixture; no
Celery dispatch is exercised — the API layer's validation runs before
the task is sent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import create_host

pytestmark = pytest.mark.integration


@pytest.fixture
def stub_celery_dispatch():
    """Block any Celery send_task during these tests."""
    with patch("app.api.actions.celery_app", create=True), patch(
        "celery.app.base.Celery.send_task"
    ):
        yield


async def test_unknown_action_returns_400(superuser_client, db, stub_celery_dispatch):
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "no-such-action", "host_id": host.id},
    )
    assert resp.status_code == 400
    assert "Unknown action" in resp.text


async def test_builtin_action_dispatches_via_runs_endpoint(
    superuser_client, db, stub_celery_dispatch
):
    """C5 wired built-in dispatch — /api/actions/runs is now universal."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "_builtin.collect_state", "host_id": host.id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["action_key"] == "_builtin.collect_state"
    assert body["status"] == "queued"


async def test_missing_required_parameter_returns_422(
    superuser_client, db, stub_celery_dispatch
):
    """linux-os-upgrade requires current_version and next_version."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-os-upgrade",
            "host_id": host.id,
            "parameters": {},  # missing both
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    # Pydantic-shaped errors list, not the old "Missing required parameters: …"
    detail = body["detail"]
    assert isinstance(detail, list)
    missing_fields = {tuple(err["loc"]) for err in detail if err["type"] == "missing"}
    assert ("current_version",) in missing_fields
    assert ("next_version",) in missing_fields


async def test_type_mismatched_parameter_returns_422(
    superuser_client, db, stub_celery_dispatch
):
    """linux-os-upgrade.parameters has typed entries — passing the wrong
    type should reject before dispatch, not silently coerce."""
    host = await create_host(db)
    # Action manifest defines current_version + next_version as choice
    # (Literal). Passing an int rejects.
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-os-upgrade",
            "host_id": host.id,
            "parameters": {"current_version": 99, "next_version": "trixie"},
        },
    )
    assert resp.status_code == 422


async def test_unknown_parameter_key_returns_422(
    superuser_client, db, stub_celery_dispatch
):
    """``extra='forbid'`` on the dynamic param model → unknown keys 422."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-upgrade",
            "host_id": host.id,
            "parameters": {"there_is_no_such_key": True},
        },
    )
    assert resp.status_code == 422


async def test_supports_fleet_exposed_in_actions_listing(superuser_client, db):
    resp = await superuser_client.get("/api/actions/")
    assert resp.status_code == 200
    rows = {r["key"]: r for r in resp.json()}
    # Built-ins are in the listing with the right supports_fleet.
    assert rows["_builtin.drift_check"]["supports_fleet"] is True
    assert rows["_builtin.sync"]["supports_fleet"] is False
    # Pack-supplied actions default to False.
    assert rows["linux-upgrade"]["supports_fleet"] is False
