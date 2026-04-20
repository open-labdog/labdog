"""Tests for the GitOps hosts-entries importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.gitops.schema import HostsEntryYAML
from app.hosts_mgmt.models import HostsEntry
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

LITERAL_YAML = """\
group: test-hosts-entries
hosts_entries:
  - ip_address: 192.168.1.10
    hostname: web.internal
    comment: Web server
    priority: 100
  - ip_address: 192.168.1.20
    hostname: db.internal
    priority: 50
"""

DIFFERENT_LITERAL_YAML = """\
group: test-hosts-entries
hosts_entries:
  - ip_address: 10.0.0.1
    hostname: proxy.internal
    priority: 200
"""

NO_HOSTS_ENTRIES_YAML = """\
group: test-hosts-entries
"""

EMPTY_HOSTS_ENTRIES_YAML = """\
group: test-hosts-entries
hosts_entries: []
"""

NULL_HOSTS_ENTRIES_YAML = """\
group: test-hosts-entries
hosts_entries: ~
"""

IDEMPOTENT_YAML = """\
group: test-hosts-entries
hosts_entries:
  - ip_address: 192.168.1.10
    hostname: web.internal
    comment: Web server
    priority: 100
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name=None, priority=None):
    """Create a group with gitops enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-he-{uuid.uuid4().hex[:6]}",
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


class TestHostsEntriesImporter:
    async def test_happy_path_literal_entries(self, db):
        """Import YAML with 2 literal entries creates the expected DB rows."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LITERAL_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.added == 2
        assert he_result.removed == 0
        assert he_result.unchanged == 0
        assert he_result.changed is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        entries = db_rows.scalars().all()
        assert len(entries) == 2
        hostnames = {e.hostname for e in entries}
        assert hostnames == {"web.internal", "db.internal"}

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.synced

    async def test_happy_path_ref_entries(self, db):
        """Import YAML with host_ref_id entries creates the expected DB rows."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.10.0.1", ssh_key_id=ssh_key.id)

        group = await _make_gitops_group(db)
        yaml_content = f"""\
group: test-hosts-entries
hosts_entries:
  - host_ref_id: {host.id}
    aliases:
      - web
      - web.internal
    comment: Host reference
    priority: 10
"""
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=yaml_content,
            commit_sha="refabc123",
            db=db,
        )

        assert result.success is True

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.added == 1
        assert he_result.changed is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        entries = db_rows.scalars().all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.host_ref_id == host.id
        assert entry.ip_address is None
        assert entry.hostname is None
        assert set(entry.aliases) == {"web", "web.internal"}

    async def test_happy_path_mixed_literal_and_ref(self, db):
        """Import YAML with both literal and ref entries populates both correctly."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="172.16.0.1", ssh_key_id=ssh_key.id)

        group = await _make_gitops_group(db)
        yaml_content = f"""\
group: test-hosts-entries
hosts_entries:
  - ip_address: 192.168.0.1
    hostname: gw.internal
    priority: 100
  - host_ref_id: {host.id}
    aliases:
      - app.internal
    priority: 50
"""
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=yaml_content,
            commit_sha="mixedabc",
            db=db,
        )

        assert result.success is True

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.added == 2
        assert he_result.changed is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        entries = db_rows.scalars().all()
        assert len(entries) == 2
        ref_entry = next(e for e in entries if e.host_ref_id is not None)
        literal_entry = next(e for e in entries if e.hostname is not None)
        assert ref_entry.host_ref_id == host.id
        assert literal_entry.hostname == "gw.internal"

    async def test_replace_semantics_wipes_old_inserts_new(self, db):
        """Second import with different data replaces all previous entries."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LITERAL_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_LITERAL_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        he_result = next(m for m in r2.modules if m.module == "hosts_entries")
        assert he_result.changed is True
        assert he_result.added == 1
        assert he_result.removed == 2

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        entries = db_rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].hostname == "proxy.internal"

    async def test_missing_section_wipes_existing_rows(self, db):
        """YAML with no hosts_entries key removes all existing non-system group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LITERAL_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_HOSTS_ENTRIES_YAML,
            commit_sha="sha_wipe",
            db=db,
        )
        assert result.success is True

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.removed == 2
        assert he_result.added == 0
        assert he_result.changed is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_null_section_wipes_existing_rows(self, db):
        """YAML with hosts_entries: null removes all existing non-system group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LITERAL_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NULL_HOSTS_ENTRIES_YAML,
            commit_sha="sha_null",
            db=db,
        )
        assert result.success is True

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.removed == 2
        assert he_result.changed is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_empty_list_wipes_existing_rows(self, db):
        """YAML with hosts_entries: [] removes all existing non-system group rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=LITERAL_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_HOSTS_ENTRIES_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.removed == 2
        assert he_result.changed is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_system_entries_preserved_across_import(self, db):
        """System-flagged rows are untouched even when YAML wipes non-system entries."""
        group = await _make_gitops_group(db)

        # Manually seed a system row (simulating what the system init does).
        system_entry = HostsEntry(
            group_id=group.id,
            ip_address="127.0.0.1",
            hostname="localhost",
            is_system=True,
            priority=9999,
        )
        db.add(system_entry)
        await db.flush()

        # Import fresh YAML (no hosts_entries → wipe non-system).
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_HOSTS_ENTRIES_YAML,
            commit_sha="sha_system_check",
            db=db,
        )
        assert result.success is True

        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        remaining = db_rows.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].is_system is True
        assert remaining[0].hostname == "localhost"

    async def test_missing_host_ref_returns_error(self, db):
        """YAML with a non-existent host_ref_id produces a module error; DB unchanged."""
        group = await _make_gitops_group(db)

        yaml_content = """\
group: test-hosts-entries
hosts_entries:
  - host_ref_id: 999999
    aliases:
      - ghost.internal
"""
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=yaml_content,
            commit_sha="sha_bad_ref",
            db=db,
        )

        assert result.success is False
        assert result.error_message is not None
        assert "999999" in result.error_message

        he_result = next(m for m in result.modules if m.module == "hosts_entries")
        assert he_result.error_message is not None

        # Group should be in error status.
        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.error

        # No rows should have been inserted.
        db_rows = await db.execute(
            select(HostsEntry).where(HostsEntry.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_alias_reorder_not_flagged_as_drift(self, db):
        """Re-importing with same aliases in different order reports changed=False."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.20.0.1", ssh_key_id=ssh_key.id)

        group = await _make_gitops_group(db)
        yaml_first = f"""\
group: test-hosts-entries
hosts_entries:
  - host_ref_id: {host.id}
    aliases:
      - alpha.internal
      - beta.internal
      - gamma.internal
"""
        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=yaml_first,
            commit_sha="sha_first_aliases",
            db=db,
        )
        assert r1.success is True

        yaml_reordered = f"""\
group: test-hosts-entries
hosts_entries:
  - host_ref_id: {host.id}
    aliases:
      - gamma.internal
      - alpha.internal
      - beta.internal
"""
        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=yaml_reordered,
            commit_sha="sha_reordered_aliases",
            db=db,
        )
        assert r2.success is True

        he_result = next(m for m in r2.modules if m.module == "hosts_entries")
        assert he_result.changed is False
        assert he_result.unchanged == 1
        assert he_result.added == 0
        assert he_result.removed == 0

    async def test_validator_error_both_ref_and_ip(self):
        """HostsEntryYAML rejects entries that have both host_ref_id and ip_address."""
        with pytest.raises(ValidationError) as exc_info:
            HostsEntryYAML(
                host_ref_id=1,
                ip_address="192.168.1.1",
                hostname="should-fail.internal",
            )
        assert "ip_address and hostname must be empty" in str(exc_info.value)

    async def test_validator_error_neither_ref_nor_literal(self):
        """HostsEntryYAML rejects entries missing both host_ref_id and ip_address/hostname."""
        with pytest.raises(ValidationError) as exc_info:
            HostsEntryYAML()  # all None / defaults
        assert "ip_address and hostname are required" in str(exc_info.value)

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

        he_result = next(m for m in r2.modules if m.module == "hosts_entries")
        assert he_result.added == 0
        assert he_result.removed == 0
        assert he_result.unchanged == 1
        assert he_result.changed is False
