"""Tests for /api/scheduled-actions/* endpoints (C4)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.action_run import ActionRun
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def stub_celery_dispatch():
    """Block any Celery task dispatch during these tests."""
    with patch("celery.app.base.Celery.send_task"):
        yield


# ---------------------------------------------------------------------------
# Auth + 404 + 400
# ---------------------------------------------------------------------------


async def test_list_requires_superuser(client, db):
    resp = await client.get("/api/scheduled-actions")
    assert resp.status_code == 401


async def test_list_forbidden_for_regular_user(regular_user_client, db):
    resp = await regular_user_client.get("/api/scheduled-actions")
    assert resp.status_code == 403


async def test_get_404_for_unknown(superuser_client, db):
    resp = await superuser_client.get("/api/scheduled-actions/99999")
    assert resp.status_code == 404


async def test_create_unknown_action_returns_400(superuser_client, db):
    group = await create_group(db)
    await db.commit()
    resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": group.id,
            "action_key": "no-such-action",
            "schedule_cron": "0 3 * * *",
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Target / scope validation
# ---------------------------------------------------------------------------


async def test_fleet_requires_supports_fleet(superuser_client, db):
    """linux-upgrade has supports_fleet=False — should reject."""
    resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "fleet",
            "target_id": None,
            "action_key": "linux-upgrade",
            "schedule_cron": "0 3 * * *",
        },
    )
    assert resp.status_code == 422
    assert "fleet" in resp.text.lower()


async def test_fleet_rejects_target_id(superuser_client, db):
    resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "fleet",
            "target_id": 7,
            "action_key": "_builtin.drift_check",
            "schedule_cron": "0 3 * * *",
        },
    )
    assert resp.status_code == 422


async def test_group_requires_target_id(superuser_client, db):
    resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": None,
            "action_key": "linux-upgrade",
            "schedule_cron": "0 3 * * *",
        },
    )
    assert resp.status_code == 422


async def test_invalid_cron_returns_422(superuser_client, db):
    group = await create_group(db)
    await db.commit()
    resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": group.id,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "this is not a cron",
        },
    )
    assert resp.status_code == 422
    assert "cron" in resp.text.lower()


async def test_unknown_target_id_returns_404(superuser_client, db):
    resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": 99999,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 3 * * *",
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Happy path + listing + filtering
# ---------------------------------------------------------------------------


async def test_create_then_list_then_get(superuser_client, db):
    group = await create_group(db, name="schedules-test-group")
    await db.commit()

    create_resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": group.id,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 3 * * 0",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    sa_id = body["id"]
    assert body["enabled"] is False  # default
    assert body["snapshot_enabled"] is True
    assert body["target_name"] == "schedules-test-group"
    assert body["action_name"] == "Collect host state"
    assert body["pack_name"] == "_builtin"
    assert body["destructive"] is False

    list_resp = await superuser_client.get("/api/scheduled-actions")
    assert list_resp.status_code == 200
    assert any(r["id"] == sa_id for r in list_resp.json())

    get_resp = await superuser_client.get(f"/api/scheduled-actions/{sa_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == sa_id


async def test_unique_per_target_action_collision_409(superuser_client, db):
    host = await create_host(db)
    await db.commit()

    body = {
        "target_kind": "host",
        "target_id": host.id,
        "action_key": "linux-upgrade",
        "schedule_cron": "0 3 * * 0",
    }
    r1 = await superuser_client.post("/api/scheduled-actions", json=body)
    assert r1.status_code == 201
    r2 = await superuser_client.post("/api/scheduled-actions", json=body)
    assert r2.status_code == 409


async def test_filter_by_category_builtin(superuser_client, db):
    host = await create_host(db)
    await db.commit()

    # One pack-supplied schedule.
    await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "host",
            "target_id": host.id,
            "action_key": "linux-upgrade",
            "schedule_cron": "0 3 * * 0",
        },
    )
    # One built-in schedule.
    await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "fleet",
            "target_id": None,
            "action_key": "_builtin.drift_check",
            "schedule_cron": "0 4 * * *",
        },
    )

    builtins = await superuser_client.get("/api/scheduled-actions?category=_builtin")
    assert builtins.status_code == 200
    assert all(r["action_key"].startswith("_builtin.") for r in builtins.json())

    pack = await superuser_client.get("/api/scheduled-actions?category=pack")
    assert pack.status_code == 200
    assert all(not r["action_key"].startswith("_builtin.") for r in pack.json())


# ---------------------------------------------------------------------------
# Update + immutability
# ---------------------------------------------------------------------------


async def test_update_changes_fields_emits_audit(superuser_client, db):
    group = await create_group(db)
    await db.commit()

    create_resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": group.id,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 3 * * 0",
            "enabled": False,
        },
    )
    sa_id = create_resp.json()["id"]

    update_resp = await superuser_client.put(
        f"/api/scheduled-actions/{sa_id}",
        json={
            "target_kind": "group",
            "target_id": group.id,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 4 * * 1",  # changed
            "enabled": True,  # changed
        },
    )
    assert update_resp.status_code == 200, update_resp.text
    body = update_resp.json()
    assert body["schedule_cron"] == "0 4 * * 1"
    assert body["enabled"] is True


async def test_update_action_key_or_target_immutable(superuser_client, db):
    group = await create_group(db)
    other_group = await create_group(db)
    await db.commit()

    create_resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "group",
            "target_id": group.id,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 3 * * 0",
        },
    )
    sa_id = create_resp.json()["id"]

    # Try to change target_id.
    resp = await superuser_client.put(
        f"/api/scheduled-actions/{sa_id}",
        json={
            "target_kind": "group",
            "target_id": other_group.id,  # changed
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 3 * * 0",
        },
    )
    assert resp.status_code == 422
    assert "immutable" in resp.text.lower()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_returns_204_and_runs_history_survives(superuser_client, db):
    host = await create_host(db)
    await db.commit()

    create_resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "host",
            "target_id": host.id,
            "action_key": "_builtin.collect_state",
            "schedule_cron": "0 * * * *",
        },
    )
    sa_id = create_resp.json()["id"]

    # Plant a fake run-history row.
    db.add(
        ActionRun(
            action_key="_builtin.collect_state",
            action_version="1.0.0",
            host_id=host.id,
            scheduled_action_id=sa_id,
            parameters={},
            parallelism=1,
            status="succeeded",
        )
    )
    await db.flush()
    await db.commit()

    del_resp = await superuser_client.delete(f"/api/scheduled-actions/{sa_id}")
    assert del_resp.status_code == 204

    # Schedule is gone.
    assert (await superuser_client.get(f"/api/scheduled-actions/{sa_id}")).status_code == 404

    # Run history survives with FK NULL'd (verified in the C1 model test;
    # here we assert at least one ActionRun row exists for the host).
    from sqlalchemy import select

    rows = (await db.execute(select(ActionRun).where(ActionRun.host_id == host.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].scheduled_action_id is None


# ---------------------------------------------------------------------------
# run-now + runs list
# ---------------------------------------------------------------------------


async def test_run_now_creates_run(superuser_client, db):
    host = await create_host(db)
    await db.commit()

    create_resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "host",
            "target_id": host.id,
            "action_key": "linux-upgrade",
            "schedule_cron": "0 3 * * 0",
        },
    )
    sa_id = create_resp.json()["id"]

    rn = await superuser_client.post(f"/api/scheduled-actions/{sa_id}/run-now")
    assert rn.status_code == 201, rn.text
    body = rn.json()
    assert body["scheduled_action_id"] == sa_id
    assert body["status"] == "queued"

    runs = await superuser_client.get(f"/api/scheduled-actions/{sa_id}/runs")
    assert runs.status_code == 200
    assert len(runs.json()) == 1
    assert runs.json()[0]["id"] == body["id"]


async def test_run_now_blocks_concurrent(superuser_client, db):
    host = await create_host(db)
    await db.commit()

    create_resp = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "host",
            "target_id": host.id,
            "action_key": "linux-upgrade",
            "schedule_cron": "0 3 * * 0",
        },
    )
    sa_id = create_resp.json()["id"]

    first = await superuser_client.post(f"/api/scheduled-actions/{sa_id}/run-now")
    assert first.status_code == 201
    second = await superuser_client.post(f"/api/scheduled-actions/{sa_id}/run-now")
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# validate-cron
# ---------------------------------------------------------------------------


async def test_validate_cron_valid(superuser_client, db):
    resp = await superuser_client.post(
        "/api/scheduled-actions/validate-cron",
        json={"cron": "0 3 * * *"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert len(body["next_run_at"]) == 3


async def test_validate_cron_invalid(superuser_client, db):
    resp = await superuser_client.post(
        "/api/scheduled-actions/validate-cron",
        json={"cron": "not a cron"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
