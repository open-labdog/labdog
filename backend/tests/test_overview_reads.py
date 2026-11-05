"""The reads behind the Overview tabs.

- ``GET /api/scheduled-actions``: ``next_run_at``, ``upcoming_runs`` and
  ``recent_runs`` on every schedule.
- ``GET /api/actions/runs``: ``host_counts`` / ``first_failure`` on every
  run, and the ``status`` / ``since`` / ``scheduled`` filters.
- ``GET /api/dashboard/stuck-operations``, ``/module-status`` and
  ``/ca-cert-expiries``.
- ``GET /api/grafana/fleet/metrics`` and its PromQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ca_certs.models import CACertRule, CertState
from app.grafana import metrics as gm
from app.models.action_run import ActionHostRun, ActionRun
from app.models.host_module_status import HostModuleStatus
from app.models.scheduled_action import ScheduledAction
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration

PEM = "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----"


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


# ---------------------------------------------------------------------------
# Scheduled actions: next run, upcoming, recent runs
# ---------------------------------------------------------------------------


async def test_schedule_lists_next_run_and_the_weeks_fire_times(superuser_client, db):
    host = await create_host(db)
    db.add(
        ScheduledAction(
            target_kind="host",
            target_id=host.id,
            action_key="_builtin.drift_check",
            schedule_cron="0 */6 * * *",
            enabled=True,
        )
    )
    await db.flush()

    resp = await superuser_client.get("/api/scheduled-actions")
    assert resp.status_code == 200, resp.text
    [row] = resp.json()
    nxt = datetime.fromisoformat(row["next_run_at"])
    now = datetime.now(UTC)
    assert now < nxt <= now + timedelta(hours=6)
    assert nxt.hour % 6 == 0 and nxt.minute == 0
    upcoming = [datetime.fromisoformat(t) for t in row["upcoming_runs"]]
    # Four a day for seven days; the first is next_run_at.
    assert len(upcoming) in (28, 29)
    assert upcoming[0] == nxt
    assert upcoming == sorted(upcoming)
    assert upcoming[-1] <= now + timedelta(days=7)


async def test_paused_schedule_has_no_next_run(superuser_client, db):
    host = await create_host(db)
    db.add(
        ScheduledAction(
            target_kind="host",
            target_id=host.id,
            action_key="_builtin.drift_check",
            schedule_cron="0 3 * * *",
            enabled=False,
        )
    )
    await db.flush()

    [row] = (await superuser_client.get("/api/scheduled-actions")).json()
    assert row["next_run_at"] is None
    assert row["upcoming_runs"] == []


async def test_busy_cron_upcoming_is_capped(superuser_client, db):
    host = await create_host(db)
    db.add(
        ScheduledAction(
            target_kind="host",
            target_id=host.id,
            action_key="_builtin.drift_check",
            schedule_cron="*/5 * * * *",
            enabled=True,
        )
    )
    await db.flush()

    [row] = (await superuser_client.get("/api/scheduled-actions")).json()
    assert len(row["upcoming_runs"]) == 200


async def test_recent_runs_newest_first_per_schedule(superuser_client, db):
    host = await create_host(db)
    a = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.drift_check",
        schedule_cron="0 3 * * *",
        enabled=True,
    )
    b = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="0 4 * * *",
        enabled=True,
    )
    db.add_all([a, b])
    await db.flush()
    base = datetime.now(UTC) - timedelta(days=20)
    for i in range(10):
        db.add(
            _run(
                action_key="_builtin.drift_check",
                host_id=host.id,
                scheduled_action_id=a.id,
                status="failed" if i == 9 else "succeeded",
                created_at=base + timedelta(days=i),
            )
        )
    db.add(_run(action_key="_builtin.collect_state", host_id=host.id, scheduled_action_id=b.id))
    await db.flush()

    rows = {r["id"]: r for r in (await superuser_client.get("/api/scheduled-actions")).json()}
    recent_a = rows[a.id]["recent_runs"]
    assert len(recent_a) == 8
    assert recent_a[0]["status"] == "failed"
    assert [r["created_at"] for r in recent_a] == sorted(
        (r["created_at"] for r in recent_a), reverse=True
    )
    assert len(rows[b.id]["recent_runs"]) == 1

    rows = (await superuser_client.get("/api/scheduled-actions?recent_runs=0")).json()
    assert all(r["recent_runs"] == [] for r in rows)


# ---------------------------------------------------------------------------
# Action runs: host tallies and filters
# ---------------------------------------------------------------------------


async def test_run_list_tallies_hosts_and_names_the_first_failure(superuser_client, db):
    group = await create_group(db)
    hosts = [await create_host(db, hostname=f"h{i}", group_ids=[group.id]) for i in range(3)]
    run = _run(group_id=group.id, target_kind="group", target_label="g", status="partial")
    db.add(run)
    await db.flush()
    db.add_all(
        [
            ActionHostRun(
                action_run_id=run.id, host_id=hosts[0].id, hostname="h0", status="succeeded"
            ),
            ActionHostRun(
                action_run_id=run.id,
                host_id=hosts[1].id,
                hostname="h1",
                status="failed",
                exit_code=2,
                error_message="ansible-runner exited with status=failed, rc=2",
                output=(
                    "TASK [apt upgrade] ***\n"
                    'fatal: [h1]: FAILED! => {"changed": false, "msg": '
                    '"E: Could not get lock /var/lib/dpkg/lock-frontend"}\n\n'
                    "PLAY RECAP ***\nh1 : ok=3 changed=0 failed=1\n"
                ),
            ),
            ActionHostRun(
                action_run_id=run.id, host_id=hosts[2].id, hostname="h2", status="failed"
            ),
        ]
    )
    await db.flush()

    [row] = (await superuser_client.get("/api/actions/runs")).json()
    assert row["host_counts"] == {"succeeded": 1, "failed": 2}
    assert row["first_failure"]["hostname"] == "h1"
    assert row["first_failure"]["exit_code"] == 2
    assert row["first_failure"]["reason"] == "E: Could not get lock /var/lib/dpkg/lock-frontend"
    assert row["first_failure"]["error_message"].startswith("ansible-runner exited")
    assert row["first_failure"]["host_run_id"] > 0
    assert row["host_runs"] == []


async def test_run_list_filters_by_status_since_and_origin(superuser_client, db):
    host = await create_host(db)
    sched = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="linux-upgrade",
        schedule_cron="0 4 * * *",
        enabled=True,
    )
    db.add(sched)
    await db.flush()
    old = datetime.now(UTC) - timedelta(days=10)
    db.add_all(
        [
            _run(host_id=host.id, status="failed", scheduled_action_id=sched.id),
            _run(host_id=host.id, status="partial"),
            _run(host_id=host.id, status="succeeded"),
            _run(host_id=host.id, status="failed", created_at=old),
        ]
    )
    await db.flush()

    async def statuses(qs: str) -> list[str]:
        resp = await superuser_client.get(f"/api/actions/runs?{qs}")
        assert resp.status_code == 200, resp.text
        return sorted(r["status"] for r in resp.json())

    assert await statuses("status=failed,partial") == ["failed", "failed", "partial"]
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert await statuses(f"status=failed,partial&since={since.replace('+', '%2B')}") == [
        "failed",
        "partial",
    ]
    assert await statuses("scheduled=true") == ["failed"]
    assert await statuses("scheduled=false&limit=500") == ["failed", "partial", "succeeded"]


# ---------------------------------------------------------------------------
# Dashboard reads
# ---------------------------------------------------------------------------


async def test_stuck_operations_counts_what_is_queued_behind(superuser_client, db):
    now = datetime.now(UTC)
    busy = await create_host(db, hostname="valheim")
    calm = await create_host(db, hostname="calm")
    db.add_all(
        [
            SyncJob(
                host_id=busy.id,
                status=JobStatus.running,
                module_type="bulk",
                module_filter=["firewall", "packages"],
                started_at=now - timedelta(hours=2),
            ),
            # Queued behind it: one sync and one host-targeted run that has
            # both a run row and a host row pending — it counts once.
            SyncJob(host_id=busy.id, status=JobStatus.pending, module_type="firewall"),
            # A recent sync is not stuck.
            SyncJob(
                host_id=calm.id,
                status=JobStatus.running,
                module_type="firewall",
                started_at=now - timedelta(minutes=5),
            ),
        ]
    )
    queued_run = _run(host_id=busy.id, status="pending")
    db.add(queued_run)
    await db.flush()
    db.add(
        ActionHostRun(
            action_run_id=queued_run.id, host_id=busy.id, hostname="valheim", status="pending"
        )
    )

    group = await create_group(db)
    k = [await create_host(db, hostname=f"k8s-{i}", group_ids=[group.id]) for i in range(2)]
    long_run = _run(
        action_key="k8s-upgrade", group_id=group.id, target_kind="group", status="running"
    )
    db.add(long_run)
    await db.flush()
    for i, h in enumerate(k):
        db.add(
            ActionHostRun(
                action_run_id=long_run.id,
                host_id=h.id,
                hostname=h.hostname,
                status="running",
                started_at=now - timedelta(minutes=50 - i),
            )
        )
    await db.flush()

    resp = await superuser_client.get("/api/dashboard/stuck-operations")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["threshold_minutes"] == 30
    by_kind = {i["kind"]: i for i in data["items"]}
    assert len(data["items"]) == 2
    sync = by_kind["sync"]
    assert sync["hostnames"] == ["valheim"]
    assert sync["label"] == "firewall, packages"
    assert sync["queued_behind"] == 2
    action = by_kind["action"]
    assert action["id"] == long_run.id
    assert action["label"] == "k8s-upgrade"
    assert sorted(action["hostnames"]) == ["k8s-0", "k8s-1"]
    assert action["queued_behind"] == 0
    # Oldest first.
    assert data["items"][0]["kind"] == "sync"


async def test_module_status_counts(superuser_client, db):
    a = await create_host(db)
    b = await create_host(db)
    db.add_all(
        [
            HostModuleStatus(host_id=a.id, module_type="services", sync_status="in_sync"),
            HostModuleStatus(host_id=b.id, module_type="services", sync_status="out_of_sync"),
            HostModuleStatus(host_id=a.id, module_type="packages", sync_status="in_sync"),
        ]
    )
    await db.flush()

    rows = (await superuser_client.get("/api/dashboard/module-status")).json()
    got = {(r["module_type"], r["sync_status"]): r["count"] for r in rows}
    assert got == {
        ("services", "in_sync"): 1,
        ("services", "out_of_sync"): 1,
        ("packages", "in_sync"): 1,
    }


async def test_ca_cert_expiries_merge_scopes_and_sort(superuser_client, db):
    group = await create_group(db, name="default-allow")
    members = [await create_host(db, group_ids=[group.id]) for _ in range(3)]
    outsider = await create_host(db, hostname="media")
    soon = datetime.now(UTC) + timedelta(days=23)
    later = datetime.now(UTC) + timedelta(days=400)
    fp = "ab" * 32
    db.add_all(
        [
            CACertRule(
                group_id=group.id,
                name="lab-root-ca",
                pem_content=PEM,
                fingerprint_sha256=fp,
                not_after=soon,
                state=CertState.present,
            ),
            # The same certificate on a member and on an outsider: one entry,
            # four distinct hosts.
            CACertRule(
                host_id=members[0].id,
                name="lab-root-ca",
                pem_content=PEM,
                fingerprint_sha256=fp,
                not_after=soon,
                state=CertState.present,
            ),
            CACertRule(
                host_id=outsider.id,
                name="lab-root-ca",
                pem_content=PEM,
                fingerprint_sha256=fp,
                not_after=soon,
                state=CertState.present,
            ),
            CACertRule(
                group_id=group.id,
                name="vendor-ca",
                pem_content=PEM,
                fingerprint_sha256="cd" * 32,
                not_after=later,
                state=CertState.present,
            ),
            # Absent certificates are being removed, not deployed.
            CACertRule(
                group_id=group.id,
                name="old-ca",
                pem_content=PEM,
                fingerprint_sha256="ef" * 32,
                not_after=soon,
                state=CertState.absent,
            ),
        ]
    )
    await db.flush()

    rows = (await superuser_client.get("/api/dashboard/ca-cert-expiries")).json()
    assert [r["name"] for r in rows] == ["lab-root-ca", "vendor-ca"]
    assert rows[0]["host_count"] == 4
    assert rows[0]["scopes"] == sorted(
        ["group default-allow", f"host {members[0].hostname}", "host media"]
    )
    assert rows[1]["host_count"] == 3


# ---------------------------------------------------------------------------
# Fleet metrics
# ---------------------------------------------------------------------------


def test_fleet_queries_group_by_host_id():
    for q in (
        gm.fleet_cpu_percent_query(),
        gm.fleet_memory_percent_query(),
        gm.fleet_disk_percent_query(),
    ):
        assert "by (labdog_host_id)" in q
        assert 'labdog_host_id!=""' in q
    assert 'mountpoint="/"' in gm.fleet_disk_percent_query()
    assert "tmpfs" in gm.fleet_disk_percent_query()


class _VectorClient:
    def __init__(self, mapping: dict[str, list[dict]]):
        self.mapping = mapping

    async def query(self, promql: str):
        for key, val in self.mapping.items():
            if key in promql:
                return val
        return []


async def test_fetch_fleet_metrics_joins_by_host_and_drops_unknown_ids():
    def vec(pairs):
        return [
            {"metric": {"labdog_host_id": str(h)}, "value": [1000.0 + h, str(v)]} for h, v in pairs
        ]

    client = _VectorClient(
        {
            "node_cpu_seconds_total": vec([(1, 12.34), (2, 101)]),
            "MemAvailable": vec([(1, 61), (9, 50)]),
            "node_filesystem_avail_bytes": vec([(2, 91.26)])
            + [{"metric": {"labdog_host_id": "x"}, "value": [1, "5"]}],
        }
    )
    rows, sampled = await gm.fetch_fleet_metrics(client, {1: "nextcloud", 2: "media"})  # type: ignore[arg-type]
    assert [(r.host_id, r.hostname, r.cpu, r.memory, r.disk) for r in rows] == [
        (1, "nextcloud", 12.3, 61.0, None),
        (2, "media", 100.0, None, 91.3),
    ]
    assert sampled == 1009.0


async def test_fleet_metrics_unconfigured(superuser_client, db):
    resp = await superuser_client.get("/api/grafana/fleet/metrics")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is False
    assert body["unconfigured_reason"] == "no_instance"
    assert body["hosts"] == []
