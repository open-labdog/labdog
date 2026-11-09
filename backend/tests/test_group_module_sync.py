"""Characterisation tests for the per-group sync trigger endpoints.

Six modules (services, packages, /etc/hosts, cron, Linux users, resolver)
each have a ``POST .../groups/{id}/sync`` that lists the group's members,
refuses an empty group, skips a host with a sync of that module in flight,
stages a pending ``SyncJob`` per remaining host and dispatches after the
commit. The firewall group sync and the group bulk sync do the same with
an audit row, and the package delete's uninstall branch stages its own
jobs. ``test_bug37_dispatch_after_commit`` pins the dispatch order; these
pin the rest, against the current code, so sharing those steps has
something to answer to.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.sync_job import SyncJob
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


@dataclass
class Route:
    path: str
    delay: str
    module_type: str
    needs_config: bool = False


ROUTES = [
    Route("/api/services/groups/{gid}/sync", "app.tasks.service_sync.run_service_sync", "service"),
    Route("/api/packages/groups/{gid}/sync", "app.tasks.package_sync.run_package_sync", "package"),
    Route("/api/hosts-mgmt/groups/{gid}/sync", "app.tasks.hosts_sync.run_hosts_sync", "hosts_file"),
    Route("/api/cron/groups/{gid}/sync", "app.tasks.cron_sync.cron_sync_task", "cron"),
    Route("/api/linux-users/groups/{gid}/sync", "app.tasks.user_sync.user_sync_task", "linux_user"),
    Route(
        "/api/resolver/groups/{gid}/sync",
        "app.tasks.resolver_sync.run_resolver_sync",
        "resolver",
        needs_config=True,
    ),
]
IDS = [r.module_type for r in ROUTES]


def _recorder():
    calls: list[tuple] = []

    def delay(*args, **kwargs):
        calls.append((args, kwargs))

    return calls, delay


async def _resolver_config(db, group_id):
    from app.resolver.models import ResolverConfig

    db.add(ResolverConfig(group_id=group_id, nameservers=["10.0.0.53"]))
    await db.flush()


async def _jobs(db, *, since: int = 0):
    rows = (
        (await db.execute(select(SyncJob).where(SyncJob.id > since).order_by(SyncJob.id)))
        .scalars()
        .all()
    )
    return rows


async def _last_job_id(db) -> int:
    return await db.scalar(select(SyncJob.id).order_by(SyncJob.id.desc()).limit(1)) or 0


async def _three_member_group(db, module_type, *, config=False):
    """A group of three: one free, one with this module in flight, one
    with another module in flight."""
    group = await create_group(db)
    free = await create_host(db, ip="10.9.0.1", group_ids=[group.id])
    busy = await create_host(db, ip="10.9.0.2", group_ids=[group.id])
    other = await create_host(db, ip="10.9.0.3", group_ids=[group.id])
    if config:
        await _resolver_config(db, group.id)
    db.add(SyncJob(host_id=busy.id, module_type=module_type, status="pending"))
    db.add(SyncJob(host_id=other.id, module_type="unrelated", status="running"))
    await db.commit()
    return group, free, busy, other


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
@pytest.mark.parametrize("which", ["empty", "missing"])
async def test_a_group_with_no_members_is_refused(superuser_client, db, route, which):
    gid = (await create_group(db)).id if which == "empty" else 987654
    calls, delay = _recorder()
    before = await _last_job_id(db)

    with patch(f"{route.delay}.delay", side_effect=delay):
        resp = await superuser_client.post(route.path.format(gid=gid))

    assert (resp.status_code, resp.json()) == (400, {"detail": "No hosts in this group"})
    assert calls == []
    assert await _jobs(db, since=before) == []


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
async def test_members_with_a_sync_in_flight_are_skipped(superuser_client, db, route):
    group, free, busy, other = await _three_member_group(
        db, route.module_type, config=route.needs_config
    )
    before = await _last_job_id(db)
    calls, delay = _recorder()

    with patch(f"{route.delay}.delay", side_effect=delay):
        resp = await superuser_client.post(route.path.format(gid=group.id))

    assert (resp.status_code, resp.json()) == (201, {"triggered": 2, "skipped": 1})
    jobs = await _jobs(db, since=before)
    assert sorted(j.host_id for j in jobs) == sorted([free.id, other.id])
    assert {(j.group_id, j.module_type, j.status) for j in jobs} == {
        (group.id, route.module_type, "pending")
    }
    assert len({j.triggered_by_user_id for j in jobs}) == 1
    assert jobs[0].triggered_by_user_id is not None
    assert calls == [((), {"job_id": j.id, "host_id": j.host_id}) for j in jobs]


async def test_resolver_skips_members_without_a_resolver_config(superuser_client, db):
    group, *_ = await _three_member_group(db, "resolver", config=False)
    before = await _last_job_id(db)
    calls, delay = _recorder()

    with patch("app.tasks.resolver_sync.run_resolver_sync.delay", side_effect=delay):
        resp = await superuser_client.post(f"/api/resolver/groups/{group.id}/sync")

    assert (resp.status_code, resp.json()) == (201, {"triggered": 0, "skipped": 3})
    assert calls == []
    assert await _jobs(db, since=before) == []


async def test_the_firewall_group_sync_audits_what_it_triggered(superuser_client, db):
    group, free, busy, other = await _three_member_group(db, "firewall")
    before = await _last_job_id(db)
    last_audit = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0
    calls, delay = _recorder()

    with patch("app.tasks.sync.run_sync_playbook.delay", side_effect=delay):
        resp = await superuser_client.post(f"/api/sync/groups/{group.id}/sync")

    assert (resp.status_code, resp.json()) == (201, {"triggered": 2, "skipped": 1})
    jobs = await _jobs(db, since=before)
    assert {(j.group_id, j.module_type, j.status) for j in jobs} == {
        (group.id, "firewall", "pending")
    }
    assert calls == [((), {"job_id": j.id, "host_id": j.host_id}) for j in jobs]
    audits = (await db.execute(select(AuditLog).where(AuditLog.id > last_audit))).scalars().all()
    assert [(a.action, a.entity_type, a.entity_id, a.before_state) for a in audits] == [
        ("sync_triggered", "host_group", group.id, None)
    ]
    assert audits[0].after_state == {
        "sync_job_ids": [j.id for j in jobs],
        "hosts": [j.host_id for j in jobs],
        "module_filter": ["firewall"],
        "trigger_kind": "per_group",
    }


async def test_a_firewall_group_sync_that_triggers_nothing_is_not_audited(superuser_client, db):
    group = await create_group(db)
    host = await create_host(db, ip="10.9.1.1", group_ids=[group.id])
    db.add(SyncJob(host_id=host.id, module_type="firewall", status="running"))
    await db.commit()
    last_audit = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0

    with patch("app.tasks.sync.run_sync_playbook.delay") as delay:
        resp = await superuser_client.post(f"/api/sync/groups/{group.id}/sync")

    assert (resp.status_code, resp.json()) == (201, {"triggered": 0, "skipped": 1})
    delay.assert_not_called()
    assert (await db.execute(select(AuditLog).where(AuditLog.id > last_audit))).first() is None


@pytest.mark.parametrize(
    ("path", "delay"),
    [
        ("/api/sync/groups/{gid}/sync", "app.tasks.sync.run_sync_playbook"),
        ("/api/sync/groups/{gid}/bulk", "app.tasks.host_sync_orchestrator.run_host_sync"),
    ],
    ids=["firewall", "bulk"],
)
async def test_the_firewall_and_bulk_group_syncs_refuse_an_empty_group(
    superuser_client, db, path, delay
):
    group = await create_group(db)
    with patch(f"{delay}.delay") as mock:
        resp = await superuser_client.post(path.format(gid=group.id), json={})
    assert (resp.status_code, resp.json()) == (400, {"detail": "No hosts in this group"})
    mock.assert_not_called()


async def test_the_bulk_group_sync_needs_the_group_to_exist(superuser_client):
    resp = await superuser_client.post("/api/sync/groups/987654/bulk", json={})
    assert (resp.status_code, resp.json()) == (404, {"detail": "Group not found"})


async def test_the_bulk_group_sync_reports_skipped_members(superuser_client, db):
    group, free, busy, other = await _three_member_group(db, "bulk")
    before = await _last_job_id(db)
    last_audit = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0
    calls, delay = _recorder()

    with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", side_effect=delay):
        resp = await superuser_client.post(
            f"/api/sync/groups/{group.id}/bulk", json={"module_filter": ["cron"]}
        )

    assert resp.status_code == 201, resp.text
    jobs = await _jobs(db, since=before)
    assert resp.json() == {
        "group_id": group.id,
        "triggered_job_ids": [j.id for j in jobs],
        "skipped_host_ids": [busy.id],
        "module_filter": ["cron"],
    }
    assert [(j.module_type, j.module_filter) for j in jobs] == [("bulk", ["cron"])] * 2
    assert calls == [
        ((), {"job_id": j.id, "host_id": j.host_id, "module_filter": ["cron"]}) for j in jobs
    ]
    audit = (await db.execute(select(AuditLog).where(AuditLog.id > last_audit))).scalars().one()
    assert audit.after_state["skipped_hosts"] == [busy.id]
    assert audit.after_state["trigger_kind"] == "group_bulk"


async def test_the_package_uninstall_stages_a_sync_per_reachable_member(superuser_client, db):
    from app.packages.models import PackageRule

    key = await create_ssh_key(db)
    group = await create_group(db)
    keyed = await create_host(db, ip="10.9.2.1", ssh_key_id=key.id, group_ids=[group.id])
    busy = await create_host(db, ip="10.9.2.2", ssh_key_id=key.id, group_ids=[group.id])
    await create_host(db, ip="10.9.2.3", group_ids=[group.id])  # no key: skipped
    db.add(SyncJob(host_id=busy.id, module_type="package", status="pending"))
    rule = PackageRule(group_id=group.id, package_name="telnet")
    db.add(rule)
    await db.commit()
    before = await _last_job_id(db)
    last_audit = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0
    calls, delay = _recorder()

    with patch("app.tasks.package_sync.run_package_sync.delay", side_effect=delay):
        resp = await superuser_client.delete(
            f"/api/groups/{group.id}/packages/{rule.id}", params={"uninstall": "true"}
        )

    assert (resp.status_code, resp.json()) == (200, {"sync_jobs": 1})
    jobs = await _jobs(db, since=before)
    assert [(j.host_id, j.group_id, j.module_type, j.status) for j in jobs] == [
        (keyed.id, group.id, "package", "pending")
    ]
    assert calls == [((), {"job_id": jobs[0].id, "host_id": keyed.id})]
    await db.refresh(rule)
    assert str(rule.state) == "absent"
    audit = (await db.execute(select(AuditLog).where(AuditLog.id > last_audit))).scalars().one()
    assert (audit.action, audit.entity_type, audit.entity_id) == ("update", "package_rule", rule.id)
    assert (audit.before_state["state"], audit.after_state["state"]) == ("present", "absent")
