import pytest
from pydantic import ValidationError

from app.user_mgmt.diff import diff_users
from app.user_mgmt.schemas import LinuxGroupCreate, LinuxUserCreate


class TestUserSchemas:
    def test_protected_user_rejected(self):
        with pytest.raises(ValidationError, match="protected system user"):
            LinuxUserCreate(username="root")

    def test_uid_below_1000_rejected(self):
        with pytest.raises(ValidationError, match="uid must be >= 1000"):
            LinuxUserCreate(username="alice", uid=500)

    def test_uid_zero_rejected(self):
        with pytest.raises(ValidationError, match="uid must be >= 1000"):
            LinuxUserCreate(username="alice", uid=0)

    def test_uid_none_accepted(self):
        user = LinuxUserCreate(username="alice", uid=None)
        assert user.uid is None

    def test_uid_1000_accepted(self):
        user = LinuxUserCreate(username="alice", uid=1000)
        assert user.uid == 1000

    def test_sudo_injection_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="forbidden shell metacharacters"):
            LinuxUserCreate(username="alice", sudo_rule="ALL=(ALL) NOPASSWD: /bin/bash; rm -rf /")

    def test_sudo_injection_pipe_rejected(self):
        with pytest.raises(ValidationError, match="forbidden shell metacharacters"):
            LinuxUserCreate(username="alice", sudo_rule="ALL=(ALL) NOPASSWD: /bin/cat | /bin/sh")

    def test_sudo_injection_backtick_rejected(self):
        with pytest.raises(ValidationError, match="forbidden shell metacharacters"):
            LinuxUserCreate(username="alice", sudo_rule="ALL=(ALL) NOPASSWD: `/bin/sh`")

    def test_valid_sudo_rule_accepted(self):
        user = LinuxUserCreate(
            username="alice",
            sudo_rule="/usr/bin/apt, /usr/bin/systemctl restart nginx",
        )
        assert user.sudo_rule == "/usr/bin/apt, /usr/bin/systemctl restart nginx"

    def test_invalid_ssh_key_rejected(self):
        with pytest.raises(ValidationError, match="Invalid SSH key"):
            LinuxUserCreate(
                username="alice",
                authorized_keys=["not-a-valid-key AAAAB3..."],
            )

    def test_valid_ssh_key_accepted(self):
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey user@host"
        user = LinuxUserCreate(username="alice", authorized_keys=[key])
        assert user.authorized_keys == [key]

    def test_non_absolute_shell_rejected(self):
        with pytest.raises(ValidationError, match="absolute path"):
            LinuxUserCreate(username="alice", shell="bash")


class TestLinuxGroupSchemas:
    def test_protected_group_rejected(self):
        with pytest.raises(ValidationError, match="protected system group"):
            LinuxGroupCreate(groupname="sudo")

    def test_gid_below_1000_rejected(self):
        with pytest.raises(ValidationError, match="gid must be >= 1000"):
            LinuxGroupCreate(groupname="developers", gid=0)

    def test_valid_group_accepted(self):
        grp = LinuxGroupCreate(groupname="developers", gid=1500)
        assert grp.groupname == "developers"
        assert grp.gid == 1500


class TestUserDiff:
    def test_user_to_add(self):
        desired = [{"username": "alice", "state": "present", "shell": "/bin/bash"}]
        actual = []
        result = diff_users(desired, actual)
        assert "alice" in result.users_to_add
        assert not result.users_to_remove
        assert not result.users_in_sync

    def test_user_to_remove(self):
        desired = []
        actual = [{"username": "alice", "state": "present", "shell": "/bin/bash"}]
        result = diff_users(desired, actual)
        assert "alice" in result.users_to_remove
        assert not result.users_to_add

    def test_user_in_sync(self):
        entry = {
            "username": "alice",
            "state": "present",
            "shell": "/bin/bash",
            "authorized_keys": [],
            "sudo_rule": None,
            "supplementary_groups": [],
        }
        result = diff_users([entry], [entry])
        assert "alice" in result.users_in_sync
        assert not result.users_to_add
        assert not result.users_to_update

    def test_user_to_update_shell_changed(self):
        desired = {
            "username": "alice",
            "state": "present",
            "shell": "/bin/zsh",
            "authorized_keys": [],
            "sudo_rule": None,
            "supplementary_groups": [],
        }
        actual = {
            "username": "alice",
            "state": "present",
            "shell": "/bin/bash",
            "authorized_keys": [],
            "sudo_rule": None,
            "supplementary_groups": [],
        }
        result = diff_users([desired], [actual])
        assert "alice" in result.users_to_update
        assert not result.users_in_sync


class TestUserAPI:
    @pytest.mark.asyncio
    async def test_create_group_user(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json={"username": "deploy", "shell": "/bin/bash", "state": "present"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "deploy"
        assert data["group_id"] == group.id

    @pytest.mark.asyncio
    async def test_protected_user_rejected_by_api(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json={"username": "root", "shell": "/bin/bash"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_group_users(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json={"username": "alice", "shell": "/bin/bash"},
        )
        await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json={"username": "bob", "shell": "/bin/bash"},
        )
        resp = await superuser_client.get(f"/api/groups/{group.id}/linux-users")
        assert resp.status_code == 200
        names = [u["username"] for u in resp.json()]
        assert "alice" in names
        assert "bob" in names

    @pytest.mark.asyncio
    async def test_create_host_user(self, superuser_client, db):
        from tests.conftest import create_host, create_ssh_key

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/linux-users",
            json={"username": "hostadmin", "shell": "/bin/bash", "state": "present"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "hostadmin"
        assert data["host_id"] == host.id

    @pytest.mark.asyncio
    async def test_effective_users_merge(self, superuser_client, db):
        from tests.conftest import create_group, create_host, create_ssh_key

        group = await create_group(db)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        await db.commit()

        await superuser_client.post(
            f"/api/groups/{group.id}/linux-users",
            json={"username": "deploy", "shell": "/bin/bash", "state": "present"},
        )
        await superuser_client.post(
            f"/api/hosts/{host.id}/linux-users",
            json={"username": "deploy", "shell": "/bin/zsh", "state": "present"},
        )

        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-users")
        assert resp.status_code == 200
        data = resp.json()
        deploy = [u for u in data if u["username"] == "deploy"]
        assert len(deploy) == 1
        assert deploy[0]["source"] == "host"
        assert deploy[0]["shell"] == "/bin/zsh"
