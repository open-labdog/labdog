import uuid

import pytest

from tests.conftest import create_group, create_host, create_ssh_key

_RESOLVER_BODY = {
    "nameservers": ["1.1.1.1"],
    "resolver_type": "resolv_conf",
}

_CRON_JOB_BODY = {
    "name": "lock-test-job",
    "schedule": "0 * * * *",
    "command": "/usr/local/bin/lock-test.sh",
}

_HOSTS_ENTRY_BODY = {
    "ip_address": "192.168.99.1",
    "hostname": "lock-test.internal",
}

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


class TestHostsEntriesLockdown:
    """GitOps lock applied to group-level hosts-entries endpoints only.

    Host-level hosts-entries endpoints (/hosts/{id}/hosts-entries) remain
    unlocked — they are per-host overrides, not group-scoped configuration.
    """

    async def test_post_group_hosts_entry_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/hosts-entries returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"ghe-lock-{uuid.uuid4().hex[:6]}", priority=930)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json=_HOSTS_ENTRY_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_hosts_entry_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """PUT /groups/{id}/hosts-entries/{entry_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"ghe-upd-{uuid.uuid4().hex[:6]}", priority=931)

        # Create the entry before locking.
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json=_HOSTS_ENTRY_BODY,
        )
        assert resp.status_code == 201
        entry_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/hosts-entries/{entry_id}",
            json={"comment": "updated via API"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_hosts_entry_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """DELETE /groups/{id}/hosts-entries/{entry_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"ghe-del-{uuid.uuid4().hex[:6]}", priority=932)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json=_HOSTS_ENTRY_BODY,
        )
        assert resp.status_code == 201
        entry_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(
            f"/api/groups/{group.id}/hosts-entries/{entry_id}"
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_post_group_hosts_entry_allowed_when_gitops_disabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/hosts-entries returns 201 for non-gitops group."""
        group = await create_group(db, name=f"ghe-ok-{uuid.uuid4().hex[:6]}", priority=933)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json=_HOSTS_ENTRY_BODY,
        )
        assert resp.status_code == 201

    async def test_post_host_hosts_entry_not_locked_by_gitops(self, superuser_client, db):
        """POST /hosts/{id}/hosts-entries returns 201 even when host's group has gitops.

        Host-level endpoints are NOT locked — they are per-host overrides,
        not group-scoped GitOps configuration.
        """
        group = await create_group(db, name=f"ghe-host-{uuid.uuid4().hex[:6]}", priority=934)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db, ip="10.99.2.1", ssh_key_id=ssh_key.id, group_ids=[group.id]
        )

        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/hosts-entries",
            json=_HOSTS_ENTRY_BODY,
        )
        # Host-level endpoints are NOT locked.
        assert resp.status_code == 201


class TestCronJobsLockdown:
    """GitOps lock applied to group-level cron-jobs endpoints only.

    Host-level cron-job endpoints (/hosts/{id}/cron-jobs) remain unlocked —
    they are per-host overrides, not group-scoped GitOps configuration.
    """

    async def test_post_group_cron_job_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/cron-jobs returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gcj-lock-{uuid.uuid4().hex[:6]}", priority=940)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/cron-jobs",
            json=_CRON_JOB_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_cron_job_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """PUT /groups/{id}/cron-jobs/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gcj-upd-{uuid.uuid4().hex[:6]}", priority=941)

        # Create the job before locking.
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/cron-jobs",
            json=_CRON_JOB_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/cron-jobs/{rule_id}",
            json={"comment": "updated via API"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_cron_job_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """DELETE /groups/{id}/cron-jobs/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gcj-del-{uuid.uuid4().hex[:6]}", priority=942)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/cron-jobs",
            json=_CRON_JOB_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(f"/api/groups/{group.id}/cron-jobs/{rule_id}")
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_post_group_cron_job_allowed_when_gitops_disabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/cron-jobs returns 201 for non-gitops group."""
        group = await create_group(db, name=f"gcj-ok-{uuid.uuid4().hex[:6]}", priority=943)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/cron-jobs",
            json=_CRON_JOB_BODY,
        )
        assert resp.status_code == 201

    async def test_post_host_cron_job_not_locked_by_gitops(self, superuser_client, db):
        """POST /hosts/{id}/cron-jobs returns 201 even when host's group has gitops.

        Host-level cron-job overrides are NOT locked — they are per-host, not
        group-scoped GitOps configuration.
        """
        group = await create_group(db, name=f"gcj-host-{uuid.uuid4().hex[:6]}", priority=944)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db, ip="10.99.3.1", ssh_key_id=ssh_key.id, group_ids=[group.id]
        )

        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/cron-jobs",
            json=_CRON_JOB_BODY,
        )
        # Host-level endpoints are NOT locked.
        assert resp.status_code == 201


class TestResolverLockdown:
    """GitOps lock applied to group-level resolver endpoints only.

    Host-level resolver endpoints (/hosts/{id}/resolver) remain unlocked —
    they are per-host overrides, not group-scoped GitOps configuration.
    """

    async def test_put_group_resolver_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """PUT /groups/{id}/resolver returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gres-lock-{uuid.uuid4().hex[:6]}", priority=950)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/resolver",
            json=_RESOLVER_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_resolver_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """DELETE /groups/{id}/resolver returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"gres-del-{uuid.uuid4().hex[:6]}", priority=951)

        # Create a resolver row before locking.
        resp = await superuser_client.put(
            f"/api/groups/{group.id}/resolver",
            json=_RESOLVER_BODY,
        )
        assert resp.status_code == 200

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(f"/api/groups/{group.id}/resolver")
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_resolver_allowed_when_gitops_disabled(
        self, superuser_client, db
    ):
        """PUT /groups/{id}/resolver returns 200 for non-gitops group."""
        group = await create_group(db, name=f"gres-ok-{uuid.uuid4().hex[:6]}", priority=952)

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/resolver",
            json=_RESOLVER_BODY,
        )
        assert resp.status_code == 200

    async def test_put_host_resolver_not_locked_by_gitops(self, superuser_client, db):
        """PUT /hosts/{id}/resolver returns 200 even when host's group has gitops.

        Host-level resolver overrides are NOT locked — they are per-host, not
        group-scoped GitOps configuration.
        """
        group = await create_group(db, name=f"gres-host-{uuid.uuid4().hex[:6]}", priority=953)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db, ip="10.99.4.1", ssh_key_id=ssh_key.id, group_ids=[group.id]
        )

        resp = await superuser_client.put(
            f"/api/hosts/{host.id}/resolver",
            json=_RESOLVER_BODY,
        )
        # Host-level endpoints are NOT locked.
        assert resp.status_code == 200


class TestUsersLockdown:
    """GitOps lock applied to group-level linux-users and linux-groups endpoints.

    Host-level endpoints (/hosts/{id}/linux-users and /hosts/{id}/linux-groups)
    remain unlocked — they are per-host overrides, not group-scoped GitOps
    configuration.
    """

    _USER_BODY = {
        "username": "locktest",
        "uid": 1099,
        "shell": "/bin/bash",
    }

    _GROUP_BODY = {
        "groupname": "locktestgroup",
        "gid": 2099,
    }

    # ------------------------------------------------------------------
    # linux-users group-level lockdown
    # ------------------------------------------------------------------

    async def test_post_group_linux_user_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/linux-users returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"glu-lock-{uuid.uuid4().hex[:6]}", priority=960)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json=self._USER_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_linux_user_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """PUT /groups/{id}/linux-users/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"glu-upd-{uuid.uuid4().hex[:6]}", priority=961)

        # Create the rule before locking.
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json=self._USER_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/linux-users/{rule_id}",
            json={"comment": "updated via API"},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_linux_user_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """DELETE /groups/{id}/linux-users/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"glu-del-{uuid.uuid4().hex[:6]}", priority=962)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json=self._USER_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(
            f"/api/groups/{group.id}/linux-users/{rule_id}"
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    # ------------------------------------------------------------------
    # linux-groups group-level lockdown
    # ------------------------------------------------------------------

    async def test_post_group_linux_group_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/linux-groups returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"glg-lock-{uuid.uuid4().hex[:6]}", priority=963)
        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-groups",
            json=self._GROUP_BODY,
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_put_group_linux_group_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """PUT /groups/{id}/linux-groups/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"glg-upd-{uuid.uuid4().hex[:6]}", priority=964)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-groups",
            json=self._GROUP_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/linux-groups/{rule_id}",
            json={"gid": 2100},
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    async def test_delete_group_linux_group_blocked_when_gitops_enabled(
        self, superuser_client, db
    ):
        """DELETE /groups/{id}/linux-groups/{rule_id} returns 403 for gitops-enabled group."""
        group = await create_group(db, name=f"glg-del-{uuid.uuid4().hex[:6]}", priority=965)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-groups",
            json=self._GROUP_BODY,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        group.gitops_enabled = True
        await db.flush()

        resp = await superuser_client.delete(
            f"/api/groups/{group.id}/linux-groups/{rule_id}"
        )
        assert resp.status_code == 403
        assert "GitOps" in resp.json()["detail"]

    # ------------------------------------------------------------------
    # Non-gitops group: writes allowed
    # ------------------------------------------------------------------

    async def test_post_group_linux_user_allowed_when_gitops_disabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/linux-users returns 201 for non-gitops group."""
        group = await create_group(db, name=f"glu-ok-{uuid.uuid4().hex[:6]}", priority=966)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json=self._USER_BODY,
        )
        assert resp.status_code == 201

    async def test_post_group_linux_group_allowed_when_gitops_disabled(
        self, superuser_client, db
    ):
        """POST /groups/{id}/linux-groups returns 201 for non-gitops group."""
        group = await create_group(db, name=f"glg-ok-{uuid.uuid4().hex[:6]}", priority=967)

        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-groups",
            json=self._GROUP_BODY,
        )
        assert resp.status_code == 201

    # ------------------------------------------------------------------
    # Host-level endpoints: NOT locked
    # ------------------------------------------------------------------

    async def test_post_host_linux_user_not_locked_by_gitops(self, superuser_client, db):
        """POST /hosts/{id}/linux-users returns 201 even when host's group has gitops."""
        group = await create_group(db, name=f"glu-host-{uuid.uuid4().hex[:6]}", priority=968)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db, ip="10.99.5.1", ssh_key_id=ssh_key.id, group_ids=[group.id]
        )

        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/linux-users",
            json=self._USER_BODY,
        )
        # Host-level endpoints are NOT locked.
        assert resp.status_code == 201

    async def test_post_host_linux_group_not_locked_by_gitops(self, superuser_client, db):
        """POST /hosts/{id}/linux-groups returns 201 even when host's group has gitops."""
        group = await create_group(db, name=f"glg-host-{uuid.uuid4().hex[:6]}", priority=969)
        group.gitops_enabled = True
        await db.flush()

        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db, ip="10.99.5.2", ssh_key_id=ssh_key.id, group_ids=[group.id]
        )

        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/linux-groups",
            json=self._GROUP_BODY,
        )
        # Host-level endpoints are NOT locked.
        assert resp.status_code == 201
