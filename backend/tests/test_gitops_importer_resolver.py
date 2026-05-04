"""Tests for the GitOps DNS resolver importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from app.resolver.models import ResolverConfig
from tests.conftest import create_group

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

RESOLVER_YAML = """\
group: test-resolver
resolver:
  nameservers:
    - 1.1.1.1
    - 8.8.8.8
  search_domains:
    - example.com
    - internal.example.com
  options:
    ndots: 5
    timeout: 2
  resolver_type: resolv_conf
  dns_over_tls: false
"""

DIFFERENT_RESOLVER_YAML = """\
group: test-resolver
resolver:
  nameservers:
    - 9.9.9.9
  search_domains: []
  resolver_type: resolv_conf
"""

NO_RESOLVER_YAML = """\
group: test-resolver
"""

NULL_RESOLVER_YAML = """\
group: test-resolver
resolver: null
"""

SYSTEMD_RESOLVER_DOT_YAML = """\
group: test-resolver
resolver:
  nameservers:
    - 1.1.1.1
  resolver_type: systemd_resolved
  dns_over_tls: true
"""

DOT_WITH_RESOLV_CONF_YAML = """\
group: test-resolver
resolver:
  nameservers:
    - 1.1.1.1
  resolver_type: resolv_conf
  dns_over_tls: true
"""

TOO_MANY_NAMESERVERS_YAML = """\
group: test-resolver
resolver:
  nameservers:
    - 1.1.1.1
    - 8.8.8.8
    - 9.9.9.9
    - 4.4.4.4
"""

UNKNOWN_OPTION_YAML = """\
group: test-resolver
resolver:
  nameservers:
    - 1.1.1.1
  options:
    bogus_key: 1
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name: str | None = None, priority: int | None = None):
    """Create a HostGroup with GitOps enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-res-{uuid.uuid4().hex[:6]}",
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


class TestResolverImporter:
    async def test_happy_path_creates_resolver_config(self, db):
        """Full resolver YAML creates the expected DB row with correct fields."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        res_result = next(m for m in result.modules if m.module == "resolver")
        assert res_result.added == 1
        assert res_result.removed == 0
        assert res_result.unchanged == 0
        assert res_result.changed is True

        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        assert row is not None
        assert row.group_id == group.id
        assert list(row.nameservers) == ["1.1.1.1", "8.8.8.8"]
        assert list(row.search_domains) == ["example.com", "internal.example.com"]
        assert dict(row.options) == {"ndots": 5, "timeout": 2}
        assert str(row.resolver_type) == "resolv_conf"
        assert row.dns_over_tls is False

        refreshed = await db.scalar(select(HostGroup).where(HostGroup.id == group.id))
        assert refreshed.gitops_status == GitOpsStatus.synced

    async def test_upsert_existing_row_when_yaml_differs(self, db):
        """Second import with different YAML replaces the existing row."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_RESOLVER_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        res_result = next(m for m in r2.modules if m.module == "resolver")
        assert res_result.added == 1
        assert res_result.removed == 1
        assert res_result.changed is True

        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        assert list(row.nameservers) == ["9.9.9.9"]
        assert list(row.search_domains) == []

    async def test_leave_alone_when_resolver_key_absent(self, db):
        """Missing resolver: key in YAML leaves the existing DB row untouched.

        This is the singleton leave-alone exception — list-shaped modules wipe
        on missing sections, but resolver must never be wiped implicitly.
        """
        group = await _make_gitops_group(db)

        # Seed a resolver row via a first import.
        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="sha_seed",
            db=db,
        )
        assert r1.success is True

        # Now import YAML that has no resolver section at all.
        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_RESOLVER_YAML,
            commit_sha="sha_no_resolver",
            db=db,
        )
        assert r2.success is True

        res_result = next(m for m in r2.modules if m.module == "resolver")
        assert res_result.changed is False
        assert res_result.added == 0
        assert res_result.removed == 0
        assert res_result.unchanged == 0

        # Original row must still be there with unchanged nameservers.
        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        assert row is not None
        assert list(row.nameservers) == ["1.1.1.1", "8.8.8.8"]

    async def test_leave_alone_when_resolver_null(self, db):
        """resolver: null in YAML also leaves the existing DB row untouched.

        NOTE: Explicit deletion of a GitOps-managed resolver is NOT supported
        via YAML absence/null.  To remove a resolver config from a group that
        has GitOps enabled, use the DELETE endpoint from outside of GitOps
        (after disabling GitOps on the group), or manage it via the UI.
        """
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="sha_seed",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NULL_RESOLVER_YAML,
            commit_sha="sha_null",
            db=db,
        )
        assert r2.success is True

        res_result = next(m for m in r2.modules if m.module == "resolver")
        assert res_result.changed is False

        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        assert row is not None
        assert list(row.nameservers) == ["1.1.1.1", "8.8.8.8"]

    async def test_idempotent_reimport_reports_unchanged(self, db):
        """Re-importing identical YAML reports unchanged=1, changed=False, no audit."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        res_result = next(m for m in r2.modules if m.module == "resolver")
        assert res_result.added == 0
        assert res_result.removed == 0
        assert res_result.unchanged == 1
        assert res_result.changed is False

    async def test_invalid_too_many_nameservers_returns_error(self, db):
        """YAML with 4 nameservers produces a clean ModuleImportResult error."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=TOO_MANY_NAMESERVERS_YAML,
            commit_sha="sha_bad_ns",
            db=db,
        )
        # The YAML itself is valid YAML but fails Pydantic validation in
        # ResolverYAML.validate_nameservers, so parse_yaml raises YAMLParseError
        # and the overall import fails at the dispatcher level.
        assert result.success is False
        assert result.error_message is not None
        assert "nameserver" in result.error_message.lower() or "3" in result.error_message

    async def test_invalid_unknown_option_key_returns_error(self, db):
        """YAML with an unknown options key produces a clean error."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=UNKNOWN_OPTION_YAML,
            commit_sha="sha_bad_opt",
            db=db,
        )
        assert result.success is False
        assert result.error_message is not None
        assert "bogus_key" in result.error_message or "option" in result.error_message.lower()

    async def test_dns_over_tls_silently_false_for_resolv_conf(self, db):
        """dns_over_tls=true with resolver_type=resolv_conf is silently normalised to false."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DOT_WITH_RESOLV_CONF_YAML,
            commit_sha="sha_dot_resolv",
            db=db,
        )
        assert result.success is True

        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        assert row is not None
        assert row.dns_over_tls is False  # silently normalised

    async def test_dns_over_tls_true_preserved_for_systemd_resolved(self, db):
        """dns_over_tls=true with resolver_type=systemd_resolved is preserved."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=SYSTEMD_RESOLVER_DOT_YAML,
            commit_sha="sha_dot_systemd",
            db=db,
        )
        assert result.success is True

        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        assert row is not None
        assert row.dns_over_tls is True
        assert str(row.resolver_type) == "systemd_resolved"

    async def test_nameserver_order_preserved(self, db):
        """Nameserver list order is preserved exactly as given in YAML."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=RESOLVER_YAML,
            commit_sha="sha_order",
            db=db,
        )

        row = await db.scalar(select(ResolverConfig).where(ResolverConfig.group_id == group.id))
        # RESOLVER_YAML lists 1.1.1.1 first, then 8.8.8.8 — order must match.
        assert list(row.nameservers) == ["1.1.1.1", "8.8.8.8"]
