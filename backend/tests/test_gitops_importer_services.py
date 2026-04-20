"""Tests for the GitOps services importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from app.services.models import ServiceRule
from tests.conftest import create_group

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

SERVICES_YAML = """\
group: test-services
services:
  - service_name: nginx
    state: running
    enabled: true
    priority: 100
    comment: Web server
  - service_name: postgresql
    state: running
    enabled: true
    priority: 50
"""

DIFFERENT_SERVICES_YAML = """\
group: test-services
services:
  - service_name: redis
    state: running
    enabled: true
    priority: 200
    comment: Cache
"""

NO_SERVICES_YAML = """\
group: test-services
"""

EMPTY_SERVICES_YAML = """\
group: test-services
services: []
"""

SERVICES_WITH_PROTECTED_YAML = """\
group: test-services
services:
  - service_name: sshd
    state: running
    enabled: true
  - service_name: nginx
    state: running
    enabled: true
    priority: 100
"""

SERVICES_FULL_DEPLOY_NO_CONTENT_YAML = """\
group: test-services
services:
  - service_name: myapp
    state: running
    enabled: true
    deploy_mode: full
"""

SERVICES_FULL_DEPLOY_WITH_CONTENT_YAML = """\
group: test-services
services:
  - service_name: myapp
    state: running
    enabled: true
    deploy_mode: full
    unit_content: |
      [Unit]
      Description=My App
      [Service]
      ExecStart=/usr/bin/myapp
"""

SERVICES_IDEMPOTENT_YAML = """\
group: test-services
services:
  - service_name: nginx
    state: running
    enabled: true
    priority: 100
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name=None, priority=None):
    """Create a group with gitops enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-svc-{uuid.uuid4().hex[:6]}",
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


class TestServicesImporter:
    async def test_happy_path_creates_service_rules(self, db):
        """Import YAML with 2 services creates the expected DB rows."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        # Find the services module result.
        svc_result = next(m for m in result.modules if m.module == "services")
        assert svc_result.added == 2
        assert svc_result.removed == 0
        assert svc_result.unchanged == 0
        assert svc_result.changed is True

        db_rows = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group.id)
        )
        rules = db_rows.scalars().all()
        assert len(rules) == 2
        names = {r.service_name for r in rules}
        assert names == {"nginx", "postgresql"}

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.synced

    async def test_replace_wipes_old_adds_new(self, db):
        """Second import with different services wipes old rows, inserts new."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_SERVICES_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        svc_result = next(m for m in r2.modules if m.module == "services")
        assert svc_result.added == 1
        assert svc_result.removed == 2
        assert svc_result.changed is True

        db_rows = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group.id)
        )
        rules = db_rows.scalars().all()
        assert len(rules) == 1
        assert rules[0].service_name == "redis"

    async def test_null_services_wipes_existing_rows(self, db):
        """YAML with no services key removes all existing group rows."""
        group = await _make_gitops_group(db)

        # Seed two rows first.
        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_SERVICES_YAML,
            commit_sha="sha_wipe",
            db=db,
        )
        assert result.success is True

        svc_result = next(m for m in result.modules if m.module == "services")
        assert svc_result.removed == 2
        assert svc_result.added == 0
        assert svc_result.changed is True

        db_rows = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_empty_services_list_wipes_existing_rows(self, db):
        """YAML with services: [] removes all existing group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_SERVICES_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        svc_result = next(m for m in result.modules if m.module == "services")
        assert svc_result.removed == 2
        assert svc_result.changed is True

        db_rows = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_protected_service_stripped_others_imported(self, db):
        """Protected service is dropped with warning; other services are still imported."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_WITH_PROTECTED_YAML,
            commit_sha="sha_protected",
            db=db,
        )
        assert result.success is True

        svc_result = next(m for m in result.modules if m.module == "services")
        # sshd is stripped, only nginx should be added.
        assert svc_result.added == 1
        assert svc_result.changed is True

        db_rows = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group.id)
        )
        rules = db_rows.scalars().all()
        assert len(rules) == 1
        assert rules[0].service_name == "nginx"

    async def test_deploy_mode_full_without_unit_content_returns_error(self, db):
        """deploy_mode=full without unit_content produces a module error."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_FULL_DEPLOY_NO_CONTENT_YAML,
            commit_sha="sha_bad_full",
            db=db,
        )
        assert result.success is False
        assert result.error_message is not None
        assert "unit_content" in result.error_message or "full" in result.error_message

        svc_result = next(m for m in result.modules if m.module == "services")
        assert svc_result.error_message is not None

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.error

    async def test_deploy_mode_full_with_unit_content_succeeds(self, db):
        """deploy_mode=full with unit_content is valid and imports correctly."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_FULL_DEPLOY_WITH_CONTENT_YAML,
            commit_sha="sha_full_ok",
            db=db,
        )
        assert result.success is True

        svc_result = next(m for m in result.modules if m.module == "services")
        assert svc_result.added == 1

        db_rows = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group.id)
        )
        rules = db_rows.scalars().all()
        assert len(rules) == 1
        assert rules[0].deploy_mode.value == "full"
        assert rules[0].unit_content is not None

    async def test_idempotent_reimport_reports_unchanged(self, db):
        """Re-importing identical YAML reports unchanged=N, changed=False."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_IDEMPOTENT_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SERVICES_IDEMPOTENT_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        svc_result = next(m for m in r2.modules if m.module == "services")
        assert svc_result.added == 0
        assert svc_result.removed == 0
        assert svc_result.unchanged == 1
        assert svc_result.changed is False
