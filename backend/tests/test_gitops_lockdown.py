import uuid

import pytest

from tests.conftest import create_group

pytestmark = pytest.mark.integration


class TestGitOpsLockdown:
    async def test_write_blocked_on_gitops_group(self, superuser_client, db):
        """POST rules to gitops-enabled group returns 403."""
        group = await create_group(db, name=f"gitops-lock-{uuid.uuid4().hex[:6]}", priority=9001)
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
        group = await create_group(db, name=f"gitops-read-{uuid.uuid4().hex[:6]}", priority=9002)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.get(f"/api/groups/{group.id}/rules")
        assert resp.status_code == 200

    async def test_write_allowed_when_gitops_disabled(self, superuser_client, db):
        """POST rules on non-gitops group returns 201."""
        group = await create_group(db, name=f"no-gitops-{uuid.uuid4().hex[:6]}", priority=9003)

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
        group = await create_group(db, name=f"gitops-del-{uuid.uuid4().hex[:6]}", priority=9004)

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
        group = await create_group(db, name=f"gitops-upd-{uuid.uuid4().hex[:6]}", priority=9005)

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
