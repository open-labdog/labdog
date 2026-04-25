"""Tests for the GitOps update-workflow importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from app.workflows.models import UpdateWorkflow
from tests.conftest import create_group

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

WORKFLOW_YAML = """\
group: test-workflow
workflow:
  enabled: true
  schedule_cron: "0 3 * * 0"
  batch_size: 1
  pre_update_snapshot: true
  auto_rollback: true
  auto_reboot: true
  verification_prompt: "check /healthz"
  action_key: linux-upgrade
  action_parameters: {}
"""

DIFFERENT_WORKFLOW_YAML = """\
group: test-workflow
workflow:
  enabled: false
  schedule_cron: "30 4 * * 1-5"
  batch_size: 5
  pre_update_snapshot: false
  auto_rollback: false
  auto_reboot: false
  verification_prompt: null
  action_key: linux-upgrade
  action_parameters: {}
"""

NO_WORKFLOW_YAML = """\
group: test-workflow
"""

NULL_WORKFLOW_YAML = """\
group: test-workflow
workflow: null
"""

INVALID_CRON_YAML = """\
group: test-workflow
workflow:
  enabled: true
  schedule_cron: "this is not a cron expression"
"""

UNKNOWN_ACTION_YAML = """\
group: test-workflow
workflow:
  enabled: true
  action_key: definitely-not-a-real-action
"""

OS_UPGRADE_MISSING_PARAMS_YAML = """\
group: test-workflow
workflow:
  enabled: true
  action_key: linux-os-upgrade
  action_parameters: {}
"""

OS_UPGRADE_VALID_YAML = """\
group: test-workflow
workflow:
  enabled: true
  action_key: linux-os-upgrade
  action_parameters:
    current_version: bookworm
    next_version: trixie
"""

NEGATIVE_BATCH_SIZE_YAML = """\
group: test-workflow
workflow:
  batch_size: 0
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name: str | None = None):
    group = await create_group(
        db,
        name=name or f"gitops-wf-{uuid.uuid4().hex[:6]}",
        priority=int(uuid.uuid4().int % 1000) + 1,
    )
    group.gitops_enabled = True
    group.gitops_file_path = "test.yaml"
    group.gitops_status = GitOpsStatus.disconnected
    await db.flush()
    return group


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkflowImporter:
    async def test_happy_path_creates_workflow(self, db):
        """Full workflow YAML creates the expected DB row."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=WORKFLOW_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        wf_result = next(m for m in result.modules if m.module == "workflow")
        assert wf_result.added == 1
        assert wf_result.removed == 0
        assert wf_result.unchanged == 0
        assert wf_result.changed is True

        row = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id))
        assert row is not None
        assert row.enabled is True
        assert row.schedule_cron == "0 3 * * 0"
        assert row.batch_size == 1
        assert row.pre_update_snapshot is True
        assert row.auto_rollback is True
        assert row.auto_reboot is True
        assert row.verification_prompt == "check /healthz"
        assert row.action_key == "linux-upgrade"
        assert row.action_parameters == {}

        refreshed = await db.scalar(select(HostGroup).where(HostGroup.id == group.id))
        assert refreshed.gitops_status == GitOpsStatus.synced

    async def test_update_existing_workflow_when_yaml_differs(self, db):
        """Second import with different YAML updates the existing row in place."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=WORKFLOW_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True
        first_row = await db.scalar(
            select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id)
        )
        first_id = first_row.id

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_WORKFLOW_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        wf_result = next(m for m in r2.modules if m.module == "workflow")
        assert wf_result.changed is True

        row = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id))
        assert row.id == first_id  # in-place update, not delete-and-recreate
        assert row.enabled is False
        assert row.schedule_cron == "30 4 * * 1-5"
        assert row.batch_size == 5
        assert row.pre_update_snapshot is False
        assert row.auto_rollback is False
        assert row.auto_reboot is False
        assert row.verification_prompt is None

    async def test_leave_alone_when_workflow_key_absent(self, db):
        """Missing workflow: key leaves the existing DB row untouched."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=WORKFLOW_YAML,
            commit_sha="sha_seed",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_WORKFLOW_YAML,
            commit_sha="sha_no_wf",
            db=db,
        )
        assert r2.success is True

        wf_result = next(m for m in r2.modules if m.module == "workflow")
        assert wf_result.changed is False
        assert wf_result.added == 0
        assert wf_result.removed == 0

        row = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id))
        assert row is not None
        assert row.enabled is True  # unchanged

    async def test_leave_alone_when_workflow_null(self, db):
        """workflow: null leaves the existing DB row untouched."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=WORKFLOW_YAML,
            commit_sha="sha_seed",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NULL_WORKFLOW_YAML,
            commit_sha="sha_null",
            db=db,
        )
        assert r2.success is True

        wf_result = next(m for m in r2.modules if m.module == "workflow")
        assert wf_result.changed is False

        row = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id))
        assert row is not None

    async def test_idempotent_reimport_reports_unchanged(self, db):
        """Re-importing identical YAML reports unchanged=1, changed=False."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=WORKFLOW_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=WORKFLOW_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        wf_result = next(m for m in r2.modules if m.module == "workflow")
        assert wf_result.added == 0
        assert wf_result.removed == 0
        assert wf_result.unchanged == 1
        assert wf_result.changed is False

    async def test_invalid_cron_returns_error(self, db):
        """Invalid cron expression fails YAML parse with a clear error."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_CRON_YAML,
            commit_sha="sha_bad_cron",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None
        assert "cron" in result.error_message.lower()

    async def test_unknown_action_key_returns_error(self, db):
        """Unknown action_key fails the workflow module with a clear error."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=UNKNOWN_ACTION_YAML,
            commit_sha="sha_bad_action",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None
        assert "action_key" in result.error_message
        assert "definitely-not-a-real-action" in result.error_message

    async def test_linux_os_upgrade_missing_params_returns_error(self, db):
        """linux-os-upgrade without current_version/next_version is rejected."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=OS_UPGRADE_MISSING_PARAMS_YAML,
            commit_sha="sha_missing_params",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None
        assert "linux-os-upgrade" in result.error_message
        assert "current_version" in result.error_message
        assert "next_version" in result.error_message

    async def test_linux_os_upgrade_with_valid_params_succeeds(self, db):
        """linux-os-upgrade with both required params imports cleanly."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=OS_UPGRADE_VALID_YAML,
            commit_sha="sha_valid_os",
            db=db,
        )

        assert result.success is True

        row = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id))
        assert row is not None
        assert row.action_key == "linux-os-upgrade"
        assert row.action_parameters == {
            "current_version": "bookworm",
            "next_version": "trixie",
        }

    async def test_invalid_batch_size_returns_error(self, db):
        """batch_size=0 fails schema validation (Field ge=1)."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NEGATIVE_BATCH_SIZE_YAML,
            commit_sha="sha_bad_batch",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None

    async def test_null_schedule_cron_creates_unscheduled_workflow(self, db):
        """schedule_cron: null is valid (manual-trigger-only workflow)."""
        group = await _make_gitops_group(db)

        manual_yaml = """\
group: test-workflow
workflow:
  enabled: true
  schedule_cron: null
  action_key: linux-upgrade
"""
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=manual_yaml,
            commit_sha="sha_manual",
            db=db,
        )

        assert result.success is True
        row = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group.id))
        assert row is not None
        assert row.schedule_cron is None
        assert row.enabled is True
