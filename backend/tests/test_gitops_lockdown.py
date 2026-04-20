import uuid

import pytest

from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


class TestGitOpsLockdown:
    async def test_write_blocked_on_gitops_group(self, superuser_client, db):
        """POST rules to gitops-enabled group returns 403."""
        group = await create_group(db, name=f"gitops-lock-{uuid.uuid4().hex[:6]}", priority=901)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/rules",
            json={
                "action": "allow",
                "protocol": "tcp",
                "direction": "input",
                "port_start": 80,
            },
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_read_allowed_on_gitops_group(self, superuser_client, db):
        """GET rules on gitops-enabled group returns 200."""
        group = await create_group(db, name=f"gitops-read-{uuid.uuid4().hex[:6]}", priority=902)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.get(f"/api/groups/{group.id}/rules")
        assert resp.status_code == 200

    async def test_write_allowed_when_gitops_disabled(self, superuser_client, db):
        """POST rules on non-gitops group returns 201."""
        group = await create_group(db, name=f"no-gitops-{uuid.uuid4().hex[:6]}", priority=903)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/rules",
            json={
                "action": "allow",
                "protocol": "tcp",
                "direction": "input",
                "port_start": 443,
            },
        )
        assert resp.status_code == 201

    async def test_delete_blocked_on_gitops_group(self, superuser_client, db):
        """DELETE rule on gitops-enabled group returns 403."""
        group = await create_group(db, name=f"gitops-del-{uuid.uuid4().hex[:6]}", priority=904)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/rules",
            json={
                "action": "allow",
                "protocol": "tcp",
                "direction": "input",
                "port_start": 22,
            },
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(f"/api/groups/{group.id}/rules/{rule_id}")
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_update_blocked_on_gitops_group(self, superuser_client, db):
        """PUT rule on gitops-enabled group returns 403."""
        group = await create_group(db, name=f"gitops-upd-{uuid.uuid4().hex[:6]}", priority=905)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/rules",
            json={
                "action": "allow",
                "protocol": "tcp",
                "direction": "input",
                "port_start": 8080,
            },
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/rules/{rule_id}",
            json={"comment": "updated"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]


class TestServicesLockdown:
    """GitOps lock applied to group-level service endpoints only."""

    _SERVICE_BODY = {
        "service_name": "nginx",
        "state": "running",
        "enabled": True,
    }

    async def test_post_group_service_blocked_when_gitops_enabled(self, superuser_client, db):
        """POST /groups/{id}/services returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gs-lock-{uuid.uuid4().hex[:6]}", priority=910)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json=self._SERVICE_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_service_blocked_when_gitops_enabled(self, superuser_client, db):
        """PUT /groups/{id}/services/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gs-upd-{uuid.uuid4().hex[:6]}", priority=911)

        # Create the rule before locking.
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json=self._SERVICE_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/services/{rule_id}",
            json={"comment": "updated via API"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_service_blocked_when_gitops_enabled(self, superuser_client, db):
        """DELETE /groups/{id}/services/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gs-del-{uuid.uuid4().hex[:6]}", priority=912)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json=self._SERVICE_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(f"/api/groups/{group.id}/services/{rule_id}")
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_post_group_service_allowed_when_gitops_disabled(self, superuser_client, db):
        """POST /groups/{id}/services returns 201 for non-gitops group."""
        group = await create_group(db, name=f"gs-ok-{uuid.uuid4().hex[:6]}", priority=913)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json=self._SERVICE_BODY,
        )
        assert resp.status_code == 201

    async def test_post_host_service_not_locked_by_gitops(self, superuser_client, db):
        """POST /hosts/{id}/services returns 201 even when the host's group has gitops."""
        group = await create_group(db, name=f"gs-host-{uuid.uuid4().hex[:6]}", priority=914)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.99.0.1", ssh_key_id=ssh_key.id, group_ids=[group.id])

        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/services",
            json=self._SERVICE_BODY,
        )
        # Host-level endpoints are NOT locked — they're per-host, not group-scoped.
        assert resp.status_code == 201


class TestPackagesLockdown:
    """GitOps lock applied to group-level package and package-repo endpoints."""

    _PKG_BODY = {
        "package_name": "nginx",
        "state": "present",
    }

    _REPO_BODY = {
        "name": "myrepo",
        "url": "https://packages.example.com/apt",
        "repo_type": "apt",
    }

    async def test_post_group_package_blocked_when_gitops_enabled(self, superuser_client, db):
        """POST /groups/{id}/packages returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gp-lock-{uuid.uuid4().hex[:6]}", priority=920)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json=self._PKG_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_package_blocked_when_gitops_enabled(self, superuser_client, db):
        """PUT /groups/{id}/packages/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gp-upd-{uuid.uuid4().hex[:6]}", priority=921)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json=self._PKG_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/packages/{rule_id}",
            json={"comment": "updated via API"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_package_blocked_when_gitops_enabled(self, superuser_client, db):
        """DELETE /groups/{id}/packages/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gp-del-{uuid.uuid4().hex[:6]}", priority=922)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json=self._PKG_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(f"/api/groups/{group.id}/packages/{rule_id}")
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_post_group_repo_blocked_when_gitops_enabled(self, superuser_client, db):
        """POST /groups/{id}/package-repos returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gr-lock-{uuid.uuid4().hex[:6]}", priority=923)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/package-repos",
            json=self._REPO_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_repo_blocked_when_gitops_enabled(self, superuser_client, db):
        """PUT /groups/{id}/package-repos/{repo_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gr-upd-{uuid.uuid4().hex[:6]}", priority=924)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/package-repos",
            json=self._REPO_BODY,
        )
        assert resp.status_code == 201
        repo_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/package-repos/{repo_id}",
            json={"components": "main contrib"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_repo_blocked_when_gitops_enabled(self, superuser_client, db):
        """DELETE /groups/{id}/package-repos/{repo_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gr-del-{uuid.uuid4().hex[:6]}", priority=925)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/package-repos",
            json=self._REPO_BODY,
        )
        assert resp.status_code == 201
        repo_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(f"/api/groups/{group.id}/package-repos/{repo_id}")
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_post_group_package_allowed_when_gitops_disabled(self, superuser_client, db):
        """POST /groups/{id}/packages returns 201 for non-gitops group."""
        group = await create_group(db, name=f"gp-ok-{uuid.uuid4().hex[:6]}", priority=926)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json=self._PKG_BODY,
        )
        assert resp.status_code == 201

    async def test_post_host_package_not_locked_by_gitops(self, superuser_client, db):
        """POST /hosts/{id}/packages returns 201 even when host's group has gitops.

        Host-level package overrides stay manual and are never locked.
        """
        group = await create_group(db, name=f"gp-host-{uuid.uuid4().hex[:6]}", priority=927)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.99.1.1", ssh_key_id=ssh_key.id, group_ids=[group.id])

        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/packages",
            json=self._PKG_BODY,
        )
        # Host-level endpoints are NOT locked.
        assert resp.status_code == 201
