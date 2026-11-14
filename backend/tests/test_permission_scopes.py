"""Scoped permissions, as an extension (RBAC) would grant them.

Community's own resolver ignores scope, so these tests register a small
stand-in: a user with no role who holds a few permissions on chosen hosts
and groups only. They pin down what the core promises such an extension:
list routes answer and filter, single-target routes check their target,
body-target routes refuse anything outside the grant, and ``/me`` tells
the frontend where the user may act.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.permissions import (
    ANYWHERE,
    GLOBAL,
    BoundScope,
    Scope,
    require_on,
    set_permission_resolver,
    set_visible_groups_resolver,
    set_visible_hosts_resolver,
)
from app.models.sync_job import SyncJob
from tests.conftest import create_group, create_host, create_ssh_key
from tests.test_permissions import _client

GRANT = frozenset({"hosts.view", "config.edit", "sync.run", "actions.run", "schedules.manage"})


class Grant:
    """Holds GRANT on ``hosts`` and ``groups``; nothing globally."""

    def __init__(self, hosts: set[int], groups: set[int]):
        self.hosts, self.groups = hosts, groups

    async def permissions(self, user, scope):
        resolved = await scope.resolve() if scope is not None else GLOBAL
        if resolved == ANYWHERE:
            return GRANT
        if resolved.kind == "host" and resolved.id in self.hosts:
            return GRANT
        if resolved.kind == "group" and resolved.id in self.groups:
            return GRANT
        return frozenset()

    async def visible_hosts(self, user):
        return set(self.hosts)

    async def visible_groups(self, user):
        return set(self.groups)


@pytest.fixture
async def world(db):
    g_in = await create_group(db, name=f"in-{uuid.uuid4().hex[:6]}")
    g_out = await create_group(db, name=f"out-{uuid.uuid4().hex[:6]}")
    h_in = await create_host(db, hostname=f"in-{uuid.uuid4().hex[:6]}", group_ids=[g_in.id])
    h_out = await create_host(db, hostname=f"out-{uuid.uuid4().hex[:6]}", group_ids=[g_out.id])
    return {"g_in": g_in.id, "g_out": g_out.id, "h_in": h_in.id, "h_out": h_out.id}


@pytest.fixture
async def scoped(app, db, world):
    grant = Grant({world["h_in"]}, {world["g_in"]})
    set_permission_resolver(grant.permissions)
    set_visible_hosts_resolver(grant.visible_hosts)
    set_visible_groups_resolver(grant.visible_groups)
    client, _ = await _client(app, db, None)
    try:
        yield client
    finally:
        set_permission_resolver(None)
        set_visible_hosts_resolver(None)
        set_visible_groups_resolver(None)
        await client.aclose()


class TestListsAnswerAndFilter:
    async def test_host_list(self, scoped, world):
        resp = await scoped.get("/api/hosts")
        assert resp.status_code == 200
        assert {h["id"] for h in resp.json()} == {world["h_in"]}

    async def test_host_summary(self, scoped, world):
        ids = {h["id"] for h in (await scoped.get("/api/hosts/summary")).json()}
        assert ids == {world["h_in"]}

    async def test_group_list_and_summary(self, scoped, world):
        assert {g["id"] for g in (await scoped.get("/api/groups")).json()} == {world["g_in"]}
        summary = (await scoped.get("/api/groups/summary")).json()
        assert {g["id"] for g in summary} == {world["g_in"]}

    async def test_sync_jobs(self, scoped, db, world):
        db.add_all(
            [
                SyncJob(host_id=world["h_in"], status="success"),
                SyncJob(host_id=world["h_out"], status="success"),
            ]
        )
        await db.flush()
        jobs = (await scoped.get("/api/sync/jobs?limit=500")).json()
        assert {j["host_id"] for j in jobs} == {world["h_in"]}

    async def test_global_only_routes_stay_closed(self, scoped):
        assert (await scoped.get("/api/audit-log")).status_code == 403
        assert (await scoped.get("/api/admin/users")).status_code == 403


class TestSingleTargets:
    async def test_host_routes_follow_the_grant(self, scoped, world):
        assert (await scoped.get(f"/api/hosts/{world['h_in']}")).status_code == 200
        assert (await scoped.get(f"/api/hosts/{world['h_out']}")).status_code == 403

    async def test_host_runs_need_the_host(self, scoped, world):
        ok = await scoped.get(f"/api/actions/host-runs?host_id={world['h_in']}")
        assert ok.status_code == 200
        no = await scoped.get(f"/api/actions/host-runs?host_id={world['h_out']}")
        assert no.status_code == 403


class TestBodyTargets:
    async def test_action_run_outside_the_grant(self, scoped, world):
        resp = await scoped.post(
            "/api/actions/runs",
            json={"action_key": "_builtin.drift_check", "host_id": world["h_out"]},
        )
        assert resp.status_code == 403
        assert f"host {world['h_out']}" in resp.json()["detail"]

    async def test_schedule_outside_the_grant(self, scoped, world):
        resp = await scoped.post(
            "/api/scheduled-actions",
            json={
                "target_kind": "group",
                "target_id": world["g_out"],
                "action_key": "_builtin.drift_check",
                "schedule_cron": "0 3 * * *",
            },
        )
        assert resp.status_code == 403

    async def test_adding_a_host_you_do_not_hold_to_your_group(self, scoped, world):
        resp = await scoped.post(
            f"/api/groups/{world['g_in']}/hosts", json={"host_ids": [world["h_out"]]}
        )
        assert resp.status_code == 403

    async def test_moving_your_host_into_a_group_you_do_not_hold(self, scoped, world):
        resp = await scoped.put(
            f"/api/hosts/{world['h_in']}", json={"group_ids": [world["g_in"], world["g_out"]]}
        )
        assert resp.status_code == 403

    async def test_reprioritising_your_group_needs_the_right_everywhere(self, scoped, world):
        renamed = await scoped.put(
            f"/api/groups/{world['g_in']}", json={"description": "still mine"}
        )
        assert renamed.status_code == 200, renamed.text
        moved = await scoped.put(f"/api/groups/{world['g_in']}", json={"priority": 777})
        assert moved.status_code == 403


class TestHostConnectionFields:
    """A grant on a host covers its config, not where unbork connects to."""

    @pytest.mark.parametrize(
        "change",
        [
            {"ip_address": "192.0.2.99"},
            {"hostname": "elsewhere.example"},
            {"ssh_port": 2222},
            {"ssh_user": "someone"},
        ],
    )
    async def test_scoped_editor_cannot_repoint_their_host(self, scoped, db, world, change):
        from app.models.host import Host

        before = await db.get(Host, world["h_in"])
        snapshot = {f: getattr(before, f) for f in change}
        resp = await scoped.put(f"/api/hosts/{world['h_in']}", json=change)
        assert resp.status_code == 403, resp.text
        assert "globally" in resp.json()["detail"]
        await db.refresh(before)
        assert {f: getattr(before, f) for f in change} == snapshot

    async def test_scoped_editor_cannot_swap_the_ssh_key(self, scoped, db, world):
        key = await create_ssh_key(db)
        resp = await scoped.put(f"/api/hosts/{world['h_in']}", json={"ssh_key_id": key.id})
        assert resp.status_code == 403, resp.text

    async def test_unchanged_connection_fields_and_other_edits_pass(self, scoped, db, world):
        from app.models.host import Host

        host = await db.get(Host, world["h_in"])
        resp = await scoped.put(
            f"/api/hosts/{world['h_in']}",
            json={
                "hostname": host.hostname,
                "ip_address": host.ip_address,
                "ssh_port": host.ssh_port,
                "ssh_user": host.ssh_user,
                "drift_check_enabled": True,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["drift_check_enabled"] is True

    async def test_global_editor_can_repoint(self, app, db, world):
        client, _ = await _client(app, db, "operator")
        try:
            resp = await client.put(
                f"/api/hosts/{world['h_in']}", json={"ip_address": "192.0.2.98"}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["ip_address"] == "192.0.2.98"
        finally:
            await client.aclose()

    async def test_importing_rules_into_a_group_you_do_not_hold(self, scoped, world):
        resp = await scoped.post(
            f"/api/hosts/{world['h_in']}/import-rules",
            json={"group_id": world["g_out"], "rules": [{"port_start": 22}]},
        )
        assert resp.status_code == 403, resp.text
        ok = await scoped.post(
            f"/api/hosts/{world['h_in']}/import-rules",
            json={"group_id": world["g_in"], "rules": [{"port_start": 22}]},
        )
        assert ok.status_code == 201, ok.text


class TestMe:
    async def test_me_reports_global_and_anywhere(self, scoped):
        me = (await scoped.get("/api/users/me")).json()
        assert me["permissions"] == []
        assert set(me["permissions_anywhere"]) == GRANT

    async def test_permissions_for_one_target(self, scoped, world):
        on = (await scoped.get(f"/api/users/me/permissions?host_id={world['h_in']}")).json()
        assert on == {"scope": "host", "id": world["h_in"], "permissions": sorted(GRANT)}
        off = (await scoped.get(f"/api/users/me/permissions?group_id={world['g_out']}")).json()
        assert off["permissions"] == []
        both = await scoped.get("/api/users/me/permissions?host_id=1&group_id=1")
        assert both.status_code == 422

    async def test_community_reports_the_role_everywhere(self, app, db, world):
        client, _ = await _client(app, db, "viewer")
        try:
            me = (await client.get("/api/users/me")).json()
            assert me["permissions"] == me["permissions_anywhere"]
            on = await client.get(f"/api/users/me/permissions?host_id={world['h_out']}")
            assert on.json()["permissions"] == me["permissions"]
        finally:
            await client.aclose()


class TestRequireOn:
    async def test_no_targets_is_a_global_check(self, db):
        class U:
            id = 1
            is_active = True
            role = None

        grant = Grant({1}, set())
        set_permission_resolver(grant.permissions)
        try:
            with pytest.raises(Exception) as refused:
                await require_on(U(), "config.edit")
            assert "global" in str(refused.value.detail)
            await require_on(U(), "config.edit", hosts=[1])
            assert await grant.permissions(U(), BoundScope.of(Scope("host", 1))) == GRANT
        finally:
            set_permission_resolver(None)


class TestAlertActions:
    async def test_alerts_may_only_run_where_their_author_may(self, app, db, world):
        class AlertAuthor(Grant):
            async def permissions(self, user, scope):
                resolved = await scope.resolve() if scope is not None else GLOBAL
                base = {"alerts.manage", "hosts.view"}
                if resolved == ANYWHERE or (
                    resolved.kind == "group" and resolved.id in self.groups
                ):
                    base.add("actions.run")
                return frozenset(base)

        grant = AlertAuthor(set(), {world["g_in"]})
        set_permission_resolver(grant.permissions)
        client, _ = await _client(app, db, None)
        try:
            body = {"action_key": "_builtin.collect_state", "target_kind": "group"}
            out = await client.post(
                "/api/alerts/actions", json={**body, "target_id": world["g_out"]}
            )
            assert out.status_code == 403
            fleet = await client.post("/api/alerts/actions", json={**body, "target_kind": "fleet"})
            assert fleet.status_code == 403
            ok = await client.post("/api/alerts/actions", json={**body, "target_id": world["g_in"]})
            assert ok.status_code != 403, ok.text
        finally:
            set_permission_resolver(None)
            await client.aclose()
