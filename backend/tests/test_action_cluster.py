"""Tests for the cluster-mode action path: inventory generator,
orchestrator validation, API submit-time gating, and the
membership-role endpoint."""

from __future__ import annotations

from sqlalchemy import select, update

from app.models.host import HostGroupMembership
from tests.conftest import create_group, create_host

# ---------------------------------------------------------------------------
# generate_group_inventory
# ---------------------------------------------------------------------------


def test_generate_group_inventory_groups_by_role():
    import json

    from app.ansible_runtime.inventory import generate_group_inventory

    members = [
        {
            "hostname": "cp-1",
            "host_ip": "10.0.0.10",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key_path": "/dev/shm/labdog-cluster-AAAA.key",
            "role": "control_plane",
        },
        {
            "hostname": "cp-2",
            "host_ip": "10.0.0.11",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key_path": "/dev/shm/labdog-cluster-BBBB.key",
            "role": "control_plane",
        },
        {
            "hostname": "worker-1",
            "host_ip": "10.0.0.20",
            "ssh_port": 22,
            "ssh_user": "ubuntu",
            "ssh_key_path": "/dev/shm/labdog-cluster-CCCC.key",
            "role": "worker",
        },
    ]
    result = json.loads(generate_group_inventory(members))
    cps = result["all"]["children"]["control_plane"]["hosts"]
    workers = result["all"]["children"]["workers"]["hosts"]

    assert set(cps.keys()) == {"cp-1", "cp-2"}
    assert set(workers.keys()) == {"worker-1"}
    assert cps["cp-1"]["ansible_host"] == "10.0.0.10"
    assert cps["cp-1"]["ansible_user"] == "root"
    assert workers["worker-1"]["ansible_user"] == "ubuntu"
    assert cps["cp-1"]["ansible_ssh_private_key_file"] == "/dev/shm/labdog-cluster-AAAA.key"


def test_generate_group_inventory_rejects_unknown_role():
    import pytest

    from app.ansible_runtime.inventory import generate_group_inventory

    with pytest.raises(ValueError, match="unknown role"):
        generate_group_inventory(
            [
                {
                    "hostname": "x",
                    "host_ip": "1.2.3.4",
                    "ssh_port": 22,
                    "ssh_user": "root",
                    "ssh_key_path": "/dev/shm/x",
                    "role": "etcd",
                }
            ]
        )


def test_generate_group_inventory_rejects_empty():
    import pytest

    from app.ansible_runtime.inventory import generate_group_inventory

    with pytest.raises(ValueError):
        generate_group_inventory([])


# ---------------------------------------------------------------------------
# Membership role endpoint
# ---------------------------------------------------------------------------


async def test_set_membership_role_round_trip(superuser_client, db):
    group = await create_group(db, name="k8s")
    host = await create_host(db, hostname="cp-1.test", group_ids=[group.id])
    await db.commit()

    r = await superuser_client.put(
        f"/api/groups/{group.id}/hosts/{host.id}/role",
        json={"role": "control_plane"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"host_id": host.id, "role": "control_plane"}

    listing = await superuser_client.get(f"/api/groups/{group.id}/memberships")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0] == {"host_id": host.id, "role": "control_plane"}

    # Clear by passing role: null
    r2 = await superuser_client.put(
        f"/api/groups/{group.id}/hosts/{host.id}/role",
        json={"role": None},
    )
    assert r2.status_code == 200
    assert r2.json() == {"host_id": host.id, "role": None}


async def test_set_membership_role_rejects_unknown_value(superuser_client, db):
    group = await create_group(db, name="k8s2")
    host = await create_host(db, hostname="x.test", group_ids=[group.id])
    await db.commit()

    r = await superuser_client.put(
        f"/api/groups/{group.id}/hosts/{host.id}/role",
        json={"role": "etcd"},
    )
    assert r.status_code == 422


async def test_set_membership_role_404_when_not_member(superuser_client, db):
    group = await create_group(db, name="k8s3")
    other_host = await create_host(db, hostname="not-a-member.test")
    await db.commit()

    r = await superuser_client.put(
        f"/api/groups/{group.id}/hosts/{other_host.id}/role",
        json={"role": "worker"},
    )
    assert r.status_code == 404


async def test_set_membership_role_requires_superuser(regular_user_client, db):
    group = await create_group(db, name="k8s4")
    host = await create_host(db, hostname="rbac.test", group_ids=[group.id])
    await db.commit()

    r = await regular_user_client.put(
        f"/api/groups/{group.id}/hosts/{host.id}/role",
        json={"role": "worker"},
    )
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# API submit-time validation for cluster-mode actions
# ---------------------------------------------------------------------------


async def _seed_cluster_action(monkeypatch):
    """Inject a fake cluster-mode action into the registry for the
    duration of the test. Avoids touching the real bundled manifest
    (which requires a playbook on disk)."""
    from pathlib import Path

    from app.actions.registry import ACTION_REGISTRY
    from app.actions.types import ActionDefinition

    fake = ActionDefinition(
        key="cluster-test",
        name="Cluster Test",
        description="",
        icon="Network",
        playbook_path=Path("/tmp/labdog-cluster-test.yml"),
        version="1.0",
        estimated_duration="1 min",
        destructive=False,
        supports_group=True,
        supports_host=False,
        supports_fleet=False,
        execution_mode="cluster",
    )
    # Make playbook_path resolve so the file-existence gate passes.
    Path("/tmp/labdog-cluster-test.yml").touch()
    monkeypatch.setitem(ACTION_REGISTRY, "cluster-test", fake)


async def test_cluster_run_rejects_host_id(superuser_client, db, monkeypatch):
    await _seed_cluster_action(monkeypatch)
    host = await create_host(db, hostname="lonely.test")
    await db.commit()

    r = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "cluster-test", "host_id": host.id, "parameters": {}},
    )
    # supports_host=False is checked before execution_mode; either 422
    # is fine — both surface the right thing to the operator.
    assert r.status_code == 422


async def test_cluster_run_rejects_group_with_unassigned_role(superuser_client, db, monkeypatch):
    await _seed_cluster_action(monkeypatch)
    group = await create_group(db, name="k8s-unassigned")
    cp = await create_host(db, hostname="cp.test", group_ids=[group.id])
    await db.commit()
    await db.execute(
        update(HostGroupMembership)
        .where(HostGroupMembership.c.host_id == cp.id)
        .values(role="control_plane")
    )
    worker = await create_host(db, hostname="w-no-role.test", group_ids=[group.id])
    _ = worker  # keeps the fixture alive
    await db.commit()

    r = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "cluster-test", "group_id": group.id, "parameters": {}},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.get("kind") == "members_missing_role"
    assert worker.id in detail["host_ids"]


async def test_cluster_run_rejects_group_without_control_plane(superuser_client, db, monkeypatch):
    await _seed_cluster_action(monkeypatch)
    group = await create_group(db, name="k8s-no-cp")
    h = await create_host(db, hostname="all-workers.test", group_ids=[group.id])
    await db.execute(
        update(HostGroupMembership)
        .where(HostGroupMembership.c.host_id == h.id)
        .values(role="worker")
    )
    await db.commit()

    r = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "cluster-test", "group_id": group.id, "parameters": {}},
    )
    assert r.status_code == 422
    assert "control_plane" in r.text.lower()


async def test_cluster_run_creates_single_host_run_anchored_to_first_cp(
    superuser_client, db, monkeypatch
):
    from unittest.mock import patch

    from app.models.action_run import ActionHostRun, ActionRun

    await _seed_cluster_action(monkeypatch)

    group = await create_group(db, name="k8s-happy")
    cp_a = await create_host(db, hostname="cp-a.test", group_ids=[group.id])
    cp_b = await create_host(db, hostname="cp-b.test", group_ids=[group.id])
    worker = await create_host(db, hostname="w-1.test", group_ids=[group.id])
    await db.execute(
        update(HostGroupMembership)
        .where(HostGroupMembership.c.host_id == cp_a.id)
        .values(role="control_plane")
    )
    await db.execute(
        update(HostGroupMembership)
        .where(HostGroupMembership.c.host_id == cp_b.id)
        .values(role="control_plane")
    )
    await db.execute(
        update(HostGroupMembership)
        .where(HostGroupMembership.c.host_id == worker.id)
        .values(role="worker")
    )
    await db.commit()

    # Patch the class-level ``Celery.send_task`` (matching the
    # convention used elsewhere in the test suite) so the orchestrator's
    # fire-and-forget dispatch doesn't try to reach a real broker.
    with patch("celery.app.base.Celery.send_task"):
        r = await superuser_client.post(
            "/api/actions/runs",
            json={
                "action_key": "cluster-test",
                "group_id": group.id,
                "parameters": {},
            },
        )
        assert r.status_code == 201, r.text
        run_id = r.json()["id"]

        # Drive the cluster-dispatch helper against the test's session
        # directly. ``_run_action_async`` opens its own ``task_session()``,
        # which doesn't share the test's savepoint, so we'd see no
        # ActionRun row. Calling the helper bypasses that.
        from app.tasks.action_orchestrator import _dispatch_cluster_run

        run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
        await _dispatch_cluster_run(db, run)

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    # Exactly one driver host run, anchored to the lowest-id CP.
    assert len(host_runs) == 1
    assert host_runs[0].host_id == min(cp_a.id, cp_b.id)
