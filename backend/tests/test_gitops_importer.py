"""Tests for GitOps importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.models.firewall_rule import FirewallRule
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from tests.conftest import create_group

pytestmark = pytest.mark.integration


VALID_YAML = """\
group: test-group
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 443
      source: 10.0.0.0/8
      comment: HTTPS
    - action: deny
      protocol: udp
      direction: output
      dest: 0.0.0.0/0
      comment: Block UDP out
"""

DIFFERENT_YAML = """\
group: test-group
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 8080
      comment: New HTTP rule
"""

INVALID_YAML = "group: [broken yaml"


async def _make_gitops_group(db, name=None, priority=None):
    """Create a group with gitops enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-{uuid.uuid4().hex[:6]}",
        priority=priority or int(uuid.uuid4().int % 1000) + 1,
    )
    group.gitops_enabled = True
    group.gitops_file_path = "test.yaml"
    group.gitops_status = GitOpsStatus.disconnected
    await db.flush()
    return group


class TestImporter:
    async def test_import_creates_rules(self, db):
        """Import valid YAML creates expected rules in DB."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=VALID_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.modules[0].added == 2
        assert result.error_message is None

        db_rules = await db.execute(select(FirewallRule).where(FirewallRule.group_id == group.id))
        rules = db_rules.scalars().all()
        assert len(rules) == 2

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.synced
        assert grp.gitops_last_import_at is not None

    async def test_invalid_yaml_sets_error(self, db):
        """Import invalid YAML sets gitops_status to error."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_YAML,
            commit_sha="deadbeef",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.error
        assert grp.gitops_error_message is not None

    async def test_import_replaces_old_rules(self, db):
        """Importing different YAML replaces existing non-system rules."""
        group = await _make_gitops_group(db)

        result1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=VALID_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert result1.success is True
        assert result1.modules[0].added == 2

        result2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert result2.success is True
        assert result2.modules[0].added == 1
        assert result2.modules[0].removed == 2

        db_rules = await db.execute(select(FirewallRule).where(FirewallRule.group_id == group.id))
        rules = db_rules.scalars().all()
        assert len(rules) == 1
        assert rules[0].port_start == 8080

    async def test_import_non_gitops_group_rejected(self, db):
        """Importing to a non-gitops group returns error."""
        group = await create_group(db, priority=int(uuid.uuid4().int % 1000) + 1)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=VALID_YAML,
            commit_sha="abc123",
            db=db,
        )
        assert result.success is False
        assert "GitOps enabled" in result.error_message

    async def test_import_nonexistent_group(self, db):
        """Importing to a nonexistent group returns error."""
        result = await import_group_from_yaml(
            group_id=999999,
            yaml_content=VALID_YAML,
            commit_sha="abc123",
            db=db,
        )
        assert result.success is False
        assert "not found" in result.error_message
