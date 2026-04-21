"""Tests for the GitOps cron jobs importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.cron.models import CronJob
from app.gitops.importer import import_group_from_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from tests.conftest import create_group

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

CRON_JOBS_YAML = """\
group: test-cron
cron_jobs:
  - name: backup
    schedule: "0 2 * * *"
    command: /usr/local/bin/backup.sh
    priority: 100
    comment: Nightly backup
  - name: cleanup
    schedule: "*/15 * * * *"
    command: /usr/local/bin/cleanup.sh
    priority: 50
"""

DIFFERENT_CRON_JOBS_YAML = """\
group: test-cron
cron_jobs:
  - name: sync
    schedule: "30 6 * * 1"
    command: /usr/local/bin/sync.sh
    priority: 200
    comment: Weekly sync
"""

NO_CRON_JOBS_YAML = """\
group: test-cron
"""

EMPTY_CRON_JOBS_YAML = """\
group: test-cron
cron_jobs: []
"""

INVALID_SCHEDULE_YAML = """\
group: test-cron
cron_jobs:
  - name: bad-job
    schedule: "@daily"
    command: /usr/local/bin/bad.sh
"""

INVALID_SCHEDULE_FIELD_YAML = """\
group: test-cron
cron_jobs:
  - name: bad-field
    schedule: "99 * * * *"
    command: /usr/local/bin/whatever.sh
"""

ENV_CRON_JOBS_YAML = """\
group: test-cron
cron_jobs:
  - name: env-job
    schedule: "0 * * * *"
    command: /usr/local/bin/env-job.sh
    environment:
      FOO: bar
      BAZ: qux
"""

ENV_REORDERED_YAML = """\
group: test-cron
cron_jobs:
  - name: env-job
    schedule: "0 * * * *"
    command: /usr/local/bin/env-job.sh
    environment:
      BAZ: qux
      FOO: bar
"""

SCHEDULE_ROUNDTRIP_YAML = """\
group: test-cron
cron_jobs:
  - name: roundtrip
    schedule: "*/5 * * * *"
    command: /usr/local/bin/roundtrip.sh
"""

IDEMPOTENT_YAML = """\
group: test-cron
cron_jobs:
  - name: idempotent
    schedule: "0 0 * * 0"
    command: /usr/local/bin/idempotent.sh
    priority: 10
"""

# CronJobCreate validator rejects shell metacharacters in user field.
INVALID_USER_YAML = """\
group: test-cron
cron_jobs:
  - name: bad-user-job
    schedule: "0 * * * *"
    command: /usr/local/bin/cmd.sh
    user: "root;rm -rf /"
"""

NON_DEFAULT_USER_YAML = """\
group: test-cron
cron_jobs:
  - name: deploy
    user: deploy
    schedule: "0 3 * * *"
    command: /usr/local/bin/deploy.sh
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name=None, priority=None):
    """Create a group with gitops enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-cron-{uuid.uuid4().hex[:6]}",
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


class TestCronJobsImporter:
    async def test_happy_path_creates_cron_job_rows(self, db):
        """Import YAML with 2 cron jobs creates the expected DB rows."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=CRON_JOBS_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        cron_result = next(m for m in result.modules if m.module == "cron_jobs")
        assert cron_result.added == 2
        assert cron_result.removed == 0
        assert cron_result.unchanged == 0
        assert cron_result.changed is True

        db_rows = await db.execute(select(CronJob).where(CronJob.group_id == group.id))
        jobs = db_rows.scalars().all()
        assert len(jobs) == 2
        names = {j.name for j in jobs}
        assert names == {"backup", "cleanup"}

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.synced

    async def test_replace_wipes_old_adds_new(self, db):
        """Second import with different cron jobs wipes old rows, inserts new."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=CRON_JOBS_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_CRON_JOBS_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        cron_result = next(m for m in r2.modules if m.module == "cron_jobs")
        assert cron_result.added == 1
        assert cron_result.removed == 2
        assert cron_result.changed is True

        db_rows = await db.execute(select(CronJob).where(CronJob.group_id == group.id))
        jobs = db_rows.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].name == "sync"

    async def test_null_cron_jobs_wipes_existing_rows(self, db):
        """YAML with no cron_jobs key removes all existing group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=CRON_JOBS_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_CRON_JOBS_YAML,
            commit_sha="sha_wipe",
            db=db,
        )
        assert result.success is True

        cron_result = next(m for m in result.modules if m.module == "cron_jobs")
        assert cron_result.removed == 2
        assert cron_result.added == 0
        assert cron_result.changed is True

        db_rows = await db.execute(select(CronJob).where(CronJob.group_id == group.id))
        assert db_rows.scalars().all() == []

    async def test_empty_cron_jobs_list_wipes_existing_rows(self, db):
        """YAML with cron_jobs: [] removes all existing group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=CRON_JOBS_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_CRON_JOBS_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        cron_result = next(m for m in result.modules if m.module == "cron_jobs")
        assert cron_result.removed == 2
        assert cron_result.changed is True

        db_rows = await db.execute(select(CronJob).where(CronJob.group_id == group.id))
        assert db_rows.scalars().all() == []

    async def test_invalid_cron_schedule_special_returns_error_db_unchanged(self, db):
        """Invalid @daily schedule returns error_message; DB is not mutated."""
        group = await _make_gitops_group(db)

        # Seed one row first so we can confirm it stays.
        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=IDEMPOTENT_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_SCHEDULE_YAML,
            commit_sha="sha_bad",
            db=db,
        )
        assert result.success is False
        assert result.error_message is not None
        assert "bad-job" in result.error_message or "schedule" in result.error_message.lower()

        cron_result = next(m for m in result.modules if m.module == "cron_jobs")
        assert cron_result.error_message is not None

        # DB must remain unchanged — original seed row still present.
        db_rows = await db.execute(select(CronJob).where(CronJob.group_id == group.id))
        jobs = db_rows.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].name == "idempotent"

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.error

    async def test_invalid_schedule_field_returns_error(self, db):
        """Schedule with an out-of-range minute field (99) returns error_message."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_SCHEDULE_FIELD_YAML,
            commit_sha="sha_bad_field",
            db=db,
        )
        assert result.success is False
        assert result.error_message is not None

        cron_result = next(m for m in result.modules if m.module == "cron_jobs")
        assert cron_result.error_message is not None

    async def test_environment_dict_order_insensitive_diff(self, db):
        """Re-import with reordered environment dict does not produce changes."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=ENV_CRON_JOBS_YAML,
            commit_sha="sha_env_first",
            db=db,
        )
        assert r1.success is True
        cron_result = next(m for m in r1.modules if m.module == "cron_jobs")
        assert cron_result.added == 1

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=ENV_REORDERED_YAML,
            commit_sha="sha_env_reordered",
            db=db,
        )
        assert r2.success is True

        cron_result2 = next(m for m in r2.modules if m.module == "cron_jobs")
        assert cron_result2.unchanged == 1
        assert cron_result2.changed is False

    async def test_schedule_round_trip_unchanged(self, db):
        """Re-import with the same schedule text → unchanged=1, changed=False."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SCHEDULE_ROUNDTRIP_YAML,
            commit_sha="sha_rt_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SCHEDULE_ROUNDTRIP_YAML,
            commit_sha="sha_rt_second",
            db=db,
        )
        assert r2.success is True

        cron_result = next(m for m in r2.modules if m.module == "cron_jobs")
        assert cron_result.unchanged == 1
        assert cron_result.changed is False

    async def test_idempotent_reimport_reports_unchanged(self, db):
        """Re-importing identical YAML reports unchanged=N, changed=False."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=IDEMPOTENT_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=IDEMPOTENT_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        cron_result = next(m for m in r2.modules if m.module == "cron_jobs")
        assert cron_result.added == 0
        assert cron_result.removed == 0
        assert cron_result.unchanged == 1
        assert cron_result.changed is False

    async def test_invalid_user_shell_metacharacters_raises_pydantic_error(self, db):
        """User field with shell metacharacters is rejected by Pydantic schema."""
        group = await _make_gitops_group(db)

        # The Pydantic model_validate inside parse_yaml will reject the bad user,
        # surfacing a YAMLParseError at the top-level dispatcher.
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_USER_YAML,
            commit_sha="sha_bad_user",
            db=db,
        )
        assert result.success is False
        assert result.error_message is not None
        # Either the YAML parse error or a module error should mention the invalidity.
        msg = result.error_message.lower()
        assert any(word in msg for word in ("user", "invalid", "yaml", "validation"))

    async def test_non_root_user_persisted(self, db):
        """Non-default user field is stored correctly in the DB."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NON_DEFAULT_USER_YAML,
            commit_sha="sha_deploy_user",
            db=db,
        )
        assert result.success is True

        db_rows = await db.execute(select(CronJob).where(CronJob.group_id == group.id))
        jobs = db_rows.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].user == "deploy"
