"""The reads behind a host's and a group's Overview tab.

- ``GET /api/actions/host-runs``: one host's part in every run that
  reached it, its own status, the failure reason and a rollback flag.
- ``GET /api/alerts?host_id=`` and ``GET /api/ai/sessions?host_id=``.
- ``GET /api/dashboard/sync-success-rate`` and ``/drift-trend`` with
  ``group_id``.
- ``GET /api/groups/{id}/module-status``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.models import AISession, AlertEvent
from app.models.action_run import ActionHostRun, ActionRun
from app.models.drift_sample import DriftSample
from app.models.host_module_status import HostModuleStatus
from app.models.scheduled_action import ScheduledAction
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration

FAILED_LOG = (
    "TASK [apt upgrade] ***\n"
    'fatal: [media]: FAILED! => {"changed": false, "msg": '
    '"E: Could not get lock /var/lib/dpkg/lock-frontend"}\n\n'
    "PLAY RECAP ***\nmedia : ok=3 changed=0 failed=1\n"
)


def _run(**kw) -> ActionRun:
    base = {
        "action_key": "linux-upgrade",
        "action_version": "1.0",
        "parameters": {},
        "parallelism": 1,
        "status": "succeeded",
    }
    base.update(kw)
    return ActionRun(**base)


async def test_host_runs_include_group_runs_with_this_hosts_own_result(superuser_client, db):
    group = await create_group(db)
    media = await create_host(db, hostname="media", group_ids=[group.id])
    other = await create_host(db, hostname="other", group_ids=[group.id])
    sched = ScheduledAction(
        target_kind="group",
        target_id=group.id,
        action_key="linux-upgrade",
        schedule_cron="0 4 * * 0",
        enabled=True,
    )
    db.add(sched)
    await db.flush()
    now = datetime.now(UTC)
    older = _run(
        group_id=group.id,
        target_kind="group",
        target_label="g",
        scheduled_action_id=sched.id,
        created_at=now - timedelta(days=7),
    )
    newer = _run(
        group_id=group.id,
        target_kind="group",
        target_label="g",
        status="partial",
        scheduled_action_id=sched.id,
        created_at=now - timedelta(hours=10),
    )
    manual = _run(
        host_id=media.id,
        target_kind="host",
        target_label="media",
        created_at=now - timedelta(hours=1),
    )
    db.add_all([older, newer, manual])
    await db.flush()
    db.add_all(
        [
            ActionHostRun(
                action_run_id=older.id, host_id=media.id, hostname="media", status="succeeded"
            ),
            ActionHostRun(
                action_run_id=older.id, host_id=other.id, hostname="other", status="succeeded"
            ),
            ActionHostRun(
                action_run_id=newer.id,
                host_id=media.id,
                hostname="media",
                status="failed",
                exit_code=2,
                error_message="ansible-runner exited with status=failed, rc=2",
                output=FAILED_LOG + "[rollback] restoring labdog-1\n[rollback] success=True\n",
            ),
            ActionHostRun(
                action_run_id=newer.id, host_id=other.id, hostname="other", status="succeeded"
            ),
            ActionHostRun(
                action_run_id=manual.id, host_id=media.id, hostname="media", status="succeeded"
            ),
        ]
    )
    await db.flush()

    resp = await superuser_client.get(f"/api/actions/host-runs?host_id={media.id}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["action_run_id"] for r in rows] == [manual.id, newer.id, older.id]
    failed = rows[1]
    assert failed["status"] == "failed"
    assert failed["run_status"] == "partial"
    assert failed["scheduled_action_id"] == sched.id
    assert failed["target_kind"] == "group"
    assert failed["reason"] == "E: Could not get lock /var/lib/dpkg/lock-frontend"
    assert failed["rolled_back"] is True
    assert rows[0]["reason"] is None and rows[0]["rolled_back"] is False

    # The other member's view says "succeeded" for the same partial run.
    [o_new, _] = (await superuser_client.get(f"/api/actions/host-runs?host_id={other.id}")).json()
    assert o_new["action_run_id"] == newer.id and o_new["status"] == "succeeded"

    sched_only = (
        await superuser_client.get(f"/api/actions/host-runs?host_id={media.id}&scheduled=true")
    ).json()
    assert [r["action_run_id"] for r in sched_only] == [newer.id, older.id]
    since = (now - timedelta(days=1)).isoformat().replace("+", "%2B")
    recent = (
        await superuser_client.get(f"/api/actions/host-runs?host_id={media.id}&since={since}")
    ).json()
    assert [r["action_run_id"] for r in recent] == [manual.id, newer.id]


async def test_host_runs_require_a_host(superuser_client):
    resp = await superuser_client.get("/api/actions/host-runs")
    assert resp.status_code == 422


async def test_alerts_and_sessions_filter_by_host(superuser_client, db):
    a = await create_host(db, hostname="a")
    b = await create_host(db, hostname="b")
    for host_id, name in ((a.id, "OnA"), (b.id, "OnB"), (None, "Cluster")):
        db.add(
            AlertEvent(
                source="grafana_webhook",
                fingerprint=uuid.uuid4().hex,
                alertname=name,
                severity="warning",
                status="firing",
                labels={"alertname": name},
                annotations={},
                starts_at=datetime.now(UTC),
                host_id=host_id,
            )
        )
    db.add_all(
        [
            AISession(mode="chat", mission="a and b", target_host_ids=[a.id, b.id]),
            AISession(mode="chat", mission="b only", target_host_ids=[b.id]),
            AISession(mode="chat", mission="no hosts", target_host_ids=None),
        ]
    )
    await db.flush()

    alerts = (await superuser_client.get(f"/api/alerts?host_id={a.id}")).json()
    assert [x["alertname"] for x in alerts] == ["OnA"]
    assert len((await superuser_client.get("/api/alerts")).json()) == 3

    resp = await superuser_client.get(f"/api/ai/sessions?host_id={a.id}&view=all")
    assert resp.status_code == 200, resp.text
    assert [s["mission"] for s in resp.json()] == ["a and b"]
    on_b = (await superuser_client.get(f"/api/ai/sessions?host_id={b.id}&view=all")).json()
    assert sorted(s["mission"] for s in on_b) == ["a and b", "b only"]


async def test_success_rate_and_drift_trend_narrow_to_a_group(superuser_client, db):
    group = await create_group(db)
    inside = await create_host(db, hostname="inside", group_ids=[group.id])
    outside = await create_host(db, hostname="outside")
    now = datetime.now(UTC)
    db.add_all(
        [
            SyncJob(host_id=inside.id, module_type="firewall", status=JobStatus.success),
            SyncJob(host_id=inside.id, module_type="firewall", status=JobStatus.failed),
            SyncJob(host_id=outside.id, module_type="firewall", status=JobStatus.success),
            DriftSample(
                host_id=inside.id,
                module_type="services",
                status="out_of_sync",
                add_count=1,
                remove_count=0,
                policy_change_count=0,
                checked_at=now - timedelta(hours=1),
            ),
            DriftSample(
                host_id=outside.id,
                module_type="services",
                status="out_of_sync",
                add_count=1,
                remove_count=0,
                policy_change_count=0,
                checked_at=now - timedelta(hours=1),
            ),
        ]
    )
    await db.flush()

    rate = (
        await superuser_client.get(f"/api/dashboard/sync-success-rate?group_id={group.id}")
    ).json()
    assert sum(p["total"] for p in rate["points"]) == 2
    assert sum(p["success"] for p in rate["points"]) == 1
    everything = (await superuser_client.get("/api/dashboard/sync-success-rate")).json()
    assert sum(p["total"] for p in everything["points"]) == 3

    trend = (
        await superuser_client.get(f"/api/dashboard/drift-trend?days=14&group_id={group.id}")
    ).json()
    assert sum(p["drifted_checks"] for p in trend["points"]) == 1


async def test_group_module_status_lists_members_only(superuser_client, db):
    group = await create_group(db)
    a = await create_host(db, hostname="a", group_ids=[group.id])
    b = await create_host(db, hostname="b", group_ids=[group.id])
    outside = await create_host(db, hostname="outside")
    checked = datetime.now(UTC) - timedelta(minutes=5)
    db.add_all(
        [
            HostModuleStatus(
                host_id=a.id, module_type="firewall", sync_status="in_sync", collected_at=checked
            ),
            HostModuleStatus(
                host_id=a.id,
                module_type="services",
                sync_status="out_of_sync",
                last_drift_check_at=checked,
            ),
            HostModuleStatus(
                host_id=b.id,
                module_type="firewall",
                sync_status="error",
                error_message="ssh: timeout",
            ),
            HostModuleStatus(host_id=outside.id, module_type="firewall", sync_status="in_sync"),
        ]
    )
    await db.flush()

    resp = await superuser_client.get(f"/api/groups/{group.id}/module-status")
    assert resp.status_code == 200, resp.text
    rows = {(r["host_id"], r["module_type"]): r for r in resp.json()}
    assert set(rows) == {(a.id, "firewall"), (a.id, "services"), (b.id, "firewall")}
    assert rows[(a.id, "services")]["sync_status"] == "out_of_sync"
    assert datetime.fromisoformat(rows[(a.id, "services")]["checked_at"]) == checked
    assert rows[(b.id, "firewall")]["error_message"] == "ssh: timeout"
    assert rows[(b.id, "firewall")]["checked_at"] is None

    assert (await superuser_client.get("/api/groups/999999/module-status")).status_code == 404
