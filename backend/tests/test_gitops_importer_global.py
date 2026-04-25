"""Tests for the GitOps global-YAML importer (drift + discovery).

Phase 2 covers the genuinely global state that doesn't fit the per-group
``LabDogGroupYAML`` shape: the ``drift.check_interval_minutes`` setting
and the ``ScanConfig`` rows. These tests exercise both module handlers
through ``import_global_from_yaml`` against a real Postgres.
"""

import pytest
from sqlalchemy import select

from app.gitops.importer import import_global_from_yaml
from app.models.app_setting import AppSetting
from app.models.scan_config import ScanConfig
from app.settings_service import invalidate_cache
from tests.conftest import create_group, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------


def _drift_yaml(minutes: int) -> str:
    return f"drift:\n  check_interval_minutes: {minutes}\n"


def _discovery_yaml(*, ssh_key_name: str, group_name: str | None = None) -> str:
    groups_line = f"\n    default_groups: [{group_name}]" if group_name else ""
    return f"""\
discovery:
  - name: dmz-scan
    cidrs:
      - 10.20.0.0/24
    ssh_key: {ssh_key_name}
    interval_minutes: 60{groups_line}
"""


EMPTY_YAML = ""

DRIFT_NULL_YAML = "drift: null\n"

DISCOVERY_EMPTY_LIST_YAML = "discovery: []\n"

INVALID_CRON_YAML = """\
discovery:
  - name: bad
    cidrs: [10.0.0.0/24]
    ssh_key: default
    cron_expression: "this is not cron"
"""

UNKNOWN_SSH_KEY_YAML = """\
discovery:
  - name: orphan
    cidrs: [10.0.0.0/24]
    ssh_key: this-key-does-not-exist
    interval_minutes: 60
"""

UNKNOWN_GROUP_YAML_TEMPLATE = """\
discovery:
  - name: orphan
    cidrs: [10.0.0.0/24]
    ssh_key: {ssh_key_name}
    interval_minutes: 60
    default_groups: [no-such-group]
"""

OUT_OF_RANGE_DRIFT_YAML = "drift:\n  check_interval_minutes: 0\n"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDriftImporter:
    async def test_happy_path_updates_setting(self, db):
        """Importing a non-default drift value writes the row.

        Migration `a1b2c3d4e5f6_create_app_settings_table` seeds
        `drift.check_interval_minutes=30`, so the first import is an
        in-place update rather than an insert. Either way, `changed` is
        True and the persisted value reflects the YAML.
        """
        invalidate_cache()  # Settings cache is module-global; reset between tests.

        result = await import_global_from_yaml(
            repo_id=1,
            yaml_content=_drift_yaml(15),
            commit_sha="abcdef0123",
            db=db,
        )

        assert result.success is True
        drift = next(m for m in result.modules if m.module == "drift")
        assert drift.changed is True

        row = await db.scalar(
            select(AppSetting).where(AppSetting.key == "drift.check_interval_minutes")
        )
        assert row is not None
        assert row.value == "15"

    async def test_idempotent_reimport(self, db):
        """Re-importing the same drift value is a no-op."""
        invalidate_cache()

        await import_global_from_yaml(1, _drift_yaml(20), "sha1", db)
        result = await import_global_from_yaml(1, _drift_yaml(20), "sha2", db)

        drift = next(m for m in result.modules if m.module == "drift")
        assert drift.changed is False
        assert drift.unchanged == 1

    async def test_update_existing_value(self, db):
        """Second import with different value updates the row in place."""
        invalidate_cache()

        await import_global_from_yaml(1, _drift_yaml(15), "sha1", db)
        result = await import_global_from_yaml(1, _drift_yaml(45), "sha2", db)

        drift = next(m for m in result.modules if m.module == "drift")
        assert drift.changed is True

        row = await db.scalar(
            select(AppSetting).where(AppSetting.key == "drift.check_interval_minutes")
        )
        assert row.value == "45"

    async def test_drift_section_absent_leaves_alone(self, db):
        """No `drift:` block ⇒ existing setting untouched."""
        invalidate_cache()

        # Seed.
        await import_global_from_yaml(1, _drift_yaml(15), "sha1", db)

        # Re-import without a `drift:` section. Use empty payload.
        result = await import_global_from_yaml(1, EMPTY_YAML, "sha2", db)

        drift = next(m for m in result.modules if m.module == "drift")
        assert drift.changed is False
        assert drift.added == 0
        assert drift.unchanged == 0  # No row touched, no comparison made.

        row = await db.scalar(
            select(AppSetting).where(AppSetting.key == "drift.check_interval_minutes")
        )
        assert row is not None
        assert row.value == "15"  # Seeded value preserved.

    async def test_drift_null_leaves_alone(self, db):
        """`drift: null` ⇒ existing setting untouched (singleton precedent)."""
        invalidate_cache()

        await import_global_from_yaml(1, _drift_yaml(15), "sha1", db)
        result = await import_global_from_yaml(1, DRIFT_NULL_YAML, "sha2", db)

        drift = next(m for m in result.modules if m.module == "drift")
        assert drift.changed is False

        row = await db.scalar(
            select(AppSetting).where(AppSetting.key == "drift.check_interval_minutes")
        )
        assert row.value == "15"

    async def test_out_of_range_is_rejected(self, db):
        """`check_interval_minutes` below 1 fails YAML parse."""
        invalidate_cache()

        result = await import_global_from_yaml(
            1, OUT_OF_RANGE_DRIFT_YAML, "sha", db
        )

        assert result.success is False
        assert "check_interval_minutes" in (result.error_message or "")


class TestDiscoveryImporter:
    async def test_happy_path_creates_scan_config(self, db):
        """`discovery:` list with one entry creates a row."""
        invalidate_cache()

        ssh_key = await create_ssh_key(db, name="dmz-key")
        await db.commit()

        result = await import_global_from_yaml(
            1,
            _discovery_yaml(ssh_key_name="dmz-key"),
            "sha",
            db,
        )

        assert result.success is True
        disc = next(m for m in result.modules if m.module == "discovery")
        assert disc.added == 1
        assert disc.changed is True

        row = await db.scalar(select(ScanConfig).where(ScanConfig.name == "dmz-scan"))
        assert row is not None
        assert row.cidrs == ["10.20.0.0/24"]
        assert row.ssh_key_id == ssh_key.id
        assert row.interval_minutes == 60
        assert row.cron_expression is None
        assert row.default_group_ids == []

    async def test_default_groups_resolution_by_name(self, db):
        """`default_groups: [name]` resolves to a group ID list."""
        invalidate_cache()

        await create_ssh_key(db, name="key1")
        group = await create_group(db, name="edge", priority=50)
        await db.commit()

        result = await import_global_from_yaml(
            1,
            _discovery_yaml(ssh_key_name="key1", group_name="edge"),
            "sha",
            db,
        )

        assert result.success is True
        row = await db.scalar(select(ScanConfig).where(ScanConfig.name == "dmz-scan"))
        assert row.default_group_ids == [group.id]

    async def test_unknown_ssh_key_is_rejected(self, db):
        """Unknown ssh_key name aborts the import with a clean error."""
        invalidate_cache()

        result = await import_global_from_yaml(1, UNKNOWN_SSH_KEY_YAML, "sha", db)

        assert result.success is False
        assert "ssh_key" in (result.error_message or "")
        assert "this-key-does-not-exist" in (result.error_message or "")

    async def test_unknown_group_is_rejected(self, db):
        """Unknown default_groups name aborts with a clean error."""
        invalidate_cache()

        await create_ssh_key(db, name="kk")
        await db.commit()

        yaml = UNKNOWN_GROUP_YAML_TEMPLATE.format(ssh_key_name="kk")
        result = await import_global_from_yaml(1, yaml, "sha", db)

        assert result.success is False
        assert "default_groups" in (result.error_message or "")
        assert "no-such-group" in (result.error_message or "")

    async def test_missing_section_wipes(self, db):
        """`discovery:` absent ⇒ all `scan_configs` rows are deleted."""
        invalidate_cache()

        await create_ssh_key(db, name="seed-key")
        await db.commit()

        # Seed a scan via import.
        await import_global_from_yaml(
            1, _discovery_yaml(ssh_key_name="seed-key"), "sha1", db
        )
        seeded = await db.scalar(select(ScanConfig).where(ScanConfig.name == "dmz-scan"))
        assert seeded is not None

        # Re-import with no `discovery:` section ⇒ wipe.
        result = await import_global_from_yaml(1, EMPTY_YAML, "sha2", db)

        disc = next(m for m in result.modules if m.module == "discovery")
        assert disc.changed is True
        assert disc.removed == 1
        assert disc.added == 0

        # And nothing's left.
        rows = (await db.execute(select(ScanConfig))).scalars().all()
        assert rows == []

    async def test_empty_list_wipes(self, db):
        """`discovery: []` ⇒ all rows deleted (same as missing section)."""
        invalidate_cache()

        await create_ssh_key(db, name="seed-key2")
        await db.commit()

        await import_global_from_yaml(
            1, _discovery_yaml(ssh_key_name="seed-key2"), "sha1", db
        )

        result = await import_global_from_yaml(1, DISCOVERY_EMPTY_LIST_YAML, "sha2", db)
        disc = next(m for m in result.modules if m.module == "discovery")
        assert disc.changed is True
        assert disc.removed == 1

    async def test_idempotent_reimport(self, db):
        """Re-importing the same `discovery:` list is a no-op."""
        invalidate_cache()

        await create_ssh_key(db, name="idem-key")
        await db.commit()

        yaml = _discovery_yaml(ssh_key_name="idem-key")
        await import_global_from_yaml(1, yaml, "sha1", db)
        result = await import_global_from_yaml(1, yaml, "sha2", db)

        disc = next(m for m in result.modules if m.module == "discovery")
        assert disc.changed is False
        assert disc.unchanged == 1

    async def test_invalid_cron_rejected(self, db):
        """Invalid cron expression fails YAML parse."""
        invalidate_cache()

        await create_ssh_key(db, name="cron-key")
        await db.commit()

        # The fixture references "default" which doesn't exist; substitute.
        yaml = INVALID_CRON_YAML.replace("ssh_key: default", "ssh_key: cron-key")

        result = await import_global_from_yaml(1, yaml, "sha", db)
        assert result.success is False
        assert "cron" in (result.error_message or "").lower()


class TestDispatcher:
    async def test_invalid_yaml_syntax(self, db):
        """Malformed YAML fails the dispatcher with a parse error."""
        invalidate_cache()

        result = await import_global_from_yaml(
            1, "drift:\n  check_interval_minutes: [unclosed", "sha", db
        )
        assert result.success is False
        assert "yaml" in (result.error_message or "").lower()

    async def test_empty_payload_is_valid_noop(self, db):
        """Empty file is valid and produces a no-change result."""
        invalidate_cache()

        result = await import_global_from_yaml(1, "", "sha", db)
        assert result.success is True
        assert result.any_changes() is False
