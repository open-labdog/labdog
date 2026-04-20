"""Tests for the GitOps users importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from app.user_mgmt.models import LinuxGroup, LinuxUser
from tests.conftest import create_group

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

USERS_ONLY_YAML = """\
group: test-users
users:
  - username: alice
    uid: 1001
    shell: /bin/bash
    priority: 100
    comment: Test user Alice
  - username: bob
    uid: 1002
    priority: 50
"""

LINUX_GROUPS_ONLY_YAML = """\
group: test-users
linux_groups:
  - groupname: devops
    gid: 2001
    priority: 100
  - groupname: appteam
    gid: 2002
"""

BOTH_YAML = """\
group: test-users
linux_groups:
  - groupname: devops
    gid: 2001
linux_groups:
  - groupname: devops
    gid: 2001
users:
  - username: alice
    uid: 1001
    supplementary_groups:
      - devops
"""

BOTH_YAML_CLEAN = """\
group: test-users
linux_groups:
  - groupname: devops
    gid: 2001
users:
  - username: alice
    uid: 1001
    supplementary_groups:
      - devops
"""

DIFFERENT_USERS_YAML = """\
group: test-users
users:
  - username: charlie
    uid: 1003
    priority: 200
"""

DIFFERENT_GROUPS_YAML = """\
group: test-users
linux_groups:
  - groupname: ops
    gid: 3001
"""

NO_USERS_YAML = """\
group: test-users
"""

EMPTY_USERS_YAML = """\
group: test-users
users: []
"""

EMPTY_LINUX_GROUPS_YAML = """\
group: test-users
linux_groups: []
"""

AUTHORIZED_KEYS_YAML = """\
group: test-users
users:
  - username: alice
    uid: 1001
    authorized_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI key1
      - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAQQDkey2
"""

AUTHORIZED_KEYS_REORDERED_YAML = """\
group: test-users
users:
  - username: alice
    uid: 1001
    authorized_keys:
      - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAQQDkey2
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI key1
"""

SUPPLEMENTARY_GROUPS_YAML = """\
group: test-users
linux_groups:
  - groupname: groupa
    gid: 4001
  - groupname: groupb
    gid: 4002
users:
  - username: alice
    uid: 1001
    supplementary_groups:
      - groupa
      - groupb
"""

SUPPLEMENTARY_GROUPS_REORDERED_YAML = """\
group: test-users
linux_groups:
  - groupname: groupa
    gid: 4001
  - groupname: groupb
    gid: 4002
users:
  - username: alice
    uid: 1001
    supplementary_groups:
      - groupb
      - groupa
"""

CROSSREF_UNKNOWN_GROUP_YAML = """\
group: test-users
users:
  - username: alice
    uid: 1001
    supplementary_groups:
      - nonexistent-group
"""

PROTECTED_USER_YAML = """\
group: test-users
users:
  - username: root
    uid: 1000
"""

INVALID_SSH_KEY_YAML = """\
group: test-users
users:
  - username: alice
    uid: 1001
    authorized_keys:
      - invalid-key-prefix AAAAC3NzaC1lZDI1NTE5AAAAI badkey
"""

INVALID_SUDO_RULE_YAML = """\
group: test-users
users:
  - username: alice
    uid: 1001
    sudo_rule: "ALL=(ALL) $(dangerous_command)"
"""

LOW_UID_YAML = """\
group: test-users
users:
  - username: alice
    uid: 500
"""

IDEMPOTENT_USERS_YAML = """\
group: test-users
linux_groups:
  - groupname: devops
    gid: 2001
users:
  - username: alice
    uid: 1001
    priority: 100
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name=None, priority=None):
    """Create a group with gitops enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-usr-{uuid.uuid4().hex[:6]}",
        priority=priority or int(uuid.uuid4().int % 1000) + 1,
    )
    group.gitops_enabled = True
    group.gitops_file_path = "test.yaml"
    group.gitops_status = GitOpsStatus.disconnected
    await db.flush()
    return group


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUsersImporter:
    async def test_happy_path_users_only(self, db):
        """Import YAML with users section only (no linux_groups) creates user rows."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=USERS_ONLY_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.added == 2
        assert users_result.removed == 0
        assert users_result.unchanged == 0
        assert users_result.changed is True

        db_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        users = db_rows.scalars().all()
        assert len(users) == 2
        names = {u.username for u in users}
        assert names == {"alice", "bob"}

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.synced

    async def test_happy_path_linux_groups_only(self, db):
        """Import YAML with linux_groups section only (no users) creates group rows."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LINUX_GROUPS_ONLY_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.added == 2
        assert users_result.changed is True

        db_rows = await db.execute(
            select(LinuxGroup).where(LinuxGroup.group_id == group.id)
        )
        groups = db_rows.scalars().all()
        assert len(groups) == 2
        names = {g.groupname for g in groups}
        assert names == {"devops", "appteam"}

    async def test_happy_path_both_together_groups_before_users(self, db):
        """Import YAML with both sections populates both tables; no spurious cross-ref warning."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=BOTH_YAML_CLEAN,
            commit_sha="sha_both",
            db=db,
        )

        assert result.success is True

        users_result = next(m for m in result.modules if m.module == "users")
        # 1 linux_group + 1 user = 2 added
        assert users_result.added == 2
        assert users_result.changed is True

        lg_rows = await db.execute(
            select(LinuxGroup).where(LinuxGroup.group_id == group.id)
        )
        assert len(lg_rows.scalars().all()) == 1

        lu_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        users = lu_rows.scalars().all()
        assert len(users) == 1
        assert users[0].username == "alice"

    async def test_replace_semantics_users(self, db):
        """Second import with different users wipes old rows and inserts new."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=USERS_ONLY_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_USERS_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        users_result = next(m for m in r2.modules if m.module == "users")
        assert users_result.changed is True
        assert users_result.added == 1
        assert users_result.removed == 2

        db_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        users = db_rows.scalars().all()
        assert len(users) == 1
        assert users[0].username == "charlie"

    async def test_replace_semantics_groups(self, db):
        """Second import with different linux_groups wipes old rows and inserts new."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LINUX_GROUPS_ONLY_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_GROUPS_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        users_result = next(m for m in r2.modules if m.module == "users")
        assert users_result.changed is True
        assert users_result.added == 1
        assert users_result.removed == 2

        db_rows = await db.execute(
            select(LinuxGroup).where(LinuxGroup.group_id == group.id)
        )
        groups = db_rows.scalars().all()
        assert len(groups) == 1
        assert groups[0].groupname == "ops"

    async def test_empty_wipe_users(self, db):
        """YAML with users: [] removes all existing user rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=USERS_ONLY_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_USERS_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.removed == 2
        assert users_result.added == 0
        assert users_result.changed is True

        db_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_null_wipe_users(self, db):
        """YAML with no users key removes all existing user rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=USERS_ONLY_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_USERS_YAML,
            commit_sha="sha_wipe",
            db=db,
        )
        assert result.success is True

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.removed == 2
        assert users_result.changed is True

        db_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_empty_wipe_linux_groups(self, db):
        """YAML with linux_groups: [] removes all existing linux group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LINUX_GROUPS_ONLY_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_LINUX_GROUPS_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.removed == 2
        assert users_result.changed is True

        db_rows = await db.execute(
            select(LinuxGroup).where(LinuxGroup.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_authorized_keys_reorder_is_not_drift(self, db):
        """Reordering authorized_keys does not trigger a change (idempotency)."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=AUTHORIZED_KEYS_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=AUTHORIZED_KEYS_REORDERED_YAML,
            commit_sha="sha_reorder",
            db=db,
        )
        assert r2.success is True

        users_result = next(m for m in r2.modules if m.module == "users")
        assert users_result.changed is False
        assert users_result.added == 0
        assert users_result.removed == 0
        assert users_result.unchanged == 1

    async def test_supplementary_groups_reorder_is_not_drift(self, db):
        """Reordering supplementary_groups does not trigger a change (idempotency)."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SUPPLEMENTARY_GROUPS_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SUPPLEMENTARY_GROUPS_REORDERED_YAML,
            commit_sha="sha_reorder",
            db=db,
        )
        assert r2.success is True

        users_result = next(m for m in r2.modules if m.module == "users")
        # Groups are unchanged too; only the user row matters for this check.
        assert users_result.changed is False

    async def test_crossref_warning_unknown_group_import_succeeds(self, db, caplog):
        """User with supplementary_groups referencing unknown group logs warning but succeeds."""
        import logging

        group = await _make_gitops_group(db)

        with caplog.at_level(logging.WARNING, logger="app.gitops.importers.users"):
            result = await import_group_from_yaml(
                group_id=group.id,
                yaml_content=CROSSREF_UNKNOWN_GROUP_YAML,
                commit_sha="sha_crossref",
                db=db,
            )

        assert result.success is True
        assert result.error_message is None

        # Import succeeded — user row was created.
        db_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        users = db_rows.scalars().all()
        assert len(users) == 1
        assert users[0].username == "alice"

        # Warning was emitted.
        warning_text = " ".join(caplog.messages)
        assert "nonexistent-group" in warning_text

    async def test_protected_user_root_returns_error(self, db):
        """YAML with protected username 'root' returns error_message; DB unchanged."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=PROTECTED_USER_YAML,
            commit_sha="sha_protected",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.error_message is not None
        assert "root" in users_result.error_message.lower() or "protected" in users_result.error_message.lower()

        db_rows = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.error

    async def test_invalid_ssh_key_prefix_returns_error(self, db):
        """YAML with invalid SSH key prefix returns clean error_message."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_SSH_KEY_YAML,
            commit_sha="sha_badkey",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.error_message is not None
        # Message should mention SSH key or invalid prefix.
        msg = users_result.error_message.lower()
        assert "key" in msg or "ssh" in msg or "invalid" in msg

    async def test_invalid_sudo_rule_metachar_returns_error(self, db):
        """YAML with sudo_rule containing $(...) returns clean error_message."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_SUDO_RULE_YAML,
            commit_sha="sha_badsudo",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.error_message is not None
        assert "sudo" in users_result.error_message.lower() or "forbidden" in users_result.error_message.lower() or "metachar" in users_result.error_message.lower()

    async def test_uid_below_1000_returns_error(self, db):
        """YAML with uid < 1000 returns a clean error_message."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LOW_UID_YAML,
            commit_sha="sha_lowuid",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None

        users_result = next(m for m in result.modules if m.module == "users")
        assert users_result.error_message is not None
        msg = users_result.error_message.lower()
        assert "uid" in msg or "1000" in msg or "reserved" in msg

    async def test_idempotent_reimport_reports_unchanged(self, db):
        """Re-importing identical YAML reports changed=False."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=IDEMPOTENT_USERS_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=IDEMPOTENT_USERS_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        users_result = next(m for m in r2.modules if m.module == "users")
        assert users_result.added == 0
        assert users_result.removed == 0
        # 1 linux_group + 1 user = 2 unchanged
        assert users_result.unchanged == 2
        assert users_result.changed is False
