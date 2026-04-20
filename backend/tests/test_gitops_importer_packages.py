"""Tests for the GitOps packages importer — real PostgreSQL, no mocks."""

import uuid

import pytest
from sqlalchemy import select

from app.gitops.importer import import_group_from_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from app.packages.models import PackageRepository, PackageRule
from tests.conftest import create_group

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

PACKAGES_YAML = """\
group: test-packages
packages:
  - package_name: nginx
    state: present
    priority: 100
    comment: Web server
  - package_name: curl
    state: present
    priority: 50
"""

DIFFERENT_PACKAGES_YAML = """\
group: test-packages
packages:
  - package_name: wget
    state: present
    priority: 200
    comment: Downloader
"""

NO_PACKAGES_YAML = """\
group: test-packages
"""

EMPTY_PACKAGES_YAML = """\
group: test-packages
packages: []
"""

PACKAGES_WITH_PROTECTED_YAML = """\
group: test-packages
packages:
  - package_name: bash
    state: present
  - package_name: nginx
    state: present
    priority: 100
"""

REPOS_YAML = """\
group: test-packages
package_repositories:
  - name: myrepo
    url: https://packages.example.com/apt
    repo_type: apt
    distribution: focal
    components: main
"""

DIFFERENT_REPOS_YAML = """\
group: test-packages
package_repositories:
  - name: otherrepo
    url: https://other.example.com/yum
    repo_type: yum
"""

NO_REPOS_YAML = """\
group: test-packages
"""

EMPTY_REPOS_YAML = """\
group: test-packages
package_repositories: []
"""

BOTH_YAML = """\
group: test-packages
packages:
  - package_name: nginx
    state: present
    priority: 100
  - package_name: curl
    state: latest
package_repositories:
  - name: myrepo
    url: https://packages.example.com/apt
    repo_type: apt
    distribution: focal
    components: main
"""

DIFFERENT_BOTH_YAML = """\
group: test-packages
packages:
  - package_name: wget
    state: present
package_repositories:
  - name: otherrepo
    url: https://other.example.com/yum
    repo_type: yum
"""

INVALID_REPO_URL_YAML = """\
group: test-packages
package_repositories:
  - name: badrepo
    url: ftp://packages.example.com/apt
    repo_type: apt
"""

IDEMPOTENT_YAML = """\
group: test-packages
packages:
  - package_name: nginx
    state: present
    priority: 100
package_repositories:
  - name: myrepo
    url: https://packages.example.com/apt
    repo_type: apt
    distribution: focal
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_gitops_group(db, name=None, priority=None):
    """Create a group with gitops enabled."""
    group = await create_group(
        db,
        name=name or f"gitops-pkg-{uuid.uuid4().hex[:6]}",
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


class TestPackagesImporter:
    async def test_happy_path_packages_only(self, db):
        """Import YAML with 2 packages creates the expected DB rows."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=PACKAGES_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True
        assert result.error_message is None

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.added == 2
        assert pkg_result.removed == 0
        assert pkg_result.unchanged == 0
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRule).where(PackageRule.group_id == group.id)
        )
        rules = db_rows.scalars().all()
        assert len(rules) == 2
        names = {r.package_name for r in rules}
        assert names == {"nginx", "curl"}

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.synced

    async def test_happy_path_repos_only(self, db):
        """Import YAML with 1 repo creates the expected DB row."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=REPOS_YAML,
            commit_sha="abc123def456",
            db=db,
        )

        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.added == 1
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRepository).where(PackageRepository.group_id == group.id)
        )
        repos = db_rows.scalars().all()
        assert len(repos) == 1
        assert repos[0].name == "myrepo"
        assert repos[0].distribution == "focal"

    async def test_happy_path_both_together(self, db):
        """Import YAML with packages and repos populates both tables."""
        group = await _make_gitops_group(db)
        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=BOTH_YAML,
            commit_sha="sha_both",
            db=db,
        )

        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        # 2 packages + 1 repo = 3 added
        assert pkg_result.added == 3
        assert pkg_result.changed is True

        pkg_rows = await db.execute(
            select(PackageRule).where(PackageRule.group_id == group.id)
        )
        assert len(pkg_rows.scalars().all()) == 2

        repo_rows = await db.execute(
            select(PackageRepository).where(PackageRepository.group_id == group.id)
        )
        assert len(repo_rows.scalars().all()) == 1

    async def test_replace_wipes_old_adds_new_both_tables(self, db):
        """Second import with different data wipes old rows, inserts new for both tables."""
        group = await _make_gitops_group(db)

        r1 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=BOTH_YAML,
            commit_sha="sha_first",
            db=db,
        )
        assert r1.success is True

        r2 = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=DIFFERENT_BOTH_YAML,
            commit_sha="sha_second",
            db=db,
        )
        assert r2.success is True

        pkg_result = next(m for m in r2.modules if m.module == "packages")
        assert pkg_result.changed is True
        # 1 pkg added + 1 repo added = 2; 2 pkg removed + 1 repo removed = 3
        assert pkg_result.added == 2
        assert pkg_result.removed == 3

        pkg_rows = await db.execute(
            select(PackageRule).where(PackageRule.group_id == group.id)
        )
        rules = pkg_rows.scalars().all()
        assert len(rules) == 1
        assert rules[0].package_name == "wget"

        repo_rows = await db.execute(
            select(PackageRepository).where(PackageRepository.group_id == group.id)
        )
        repos = repo_rows.scalars().all()
        assert len(repos) == 1
        assert repos[0].name == "otherrepo"

    async def test_null_packages_wipes_existing_rows(self, db):
        """YAML with no packages key removes all existing group package rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=PACKAGES_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_PACKAGES_YAML,
            commit_sha="sha_wipe",
            db=db,
        )
        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.removed == 2
        assert pkg_result.added == 0
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRule).where(PackageRule.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_empty_packages_list_wipes_existing_rows(self, db):
        """YAML with packages: [] removes all existing group package rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=PACKAGES_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_PACKAGES_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.removed == 2
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRule).where(PackageRule.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_null_repos_wipes_existing_rows(self, db):
        """YAML with no package_repositories key removes all existing repo rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=REPOS_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=NO_REPOS_YAML,
            commit_sha="sha_wipe",
            db=db,
        )
        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.removed == 1
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRepository).where(PackageRepository.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_empty_repos_list_wipes_existing_rows(self, db):
        """YAML with package_repositories: [] removes all existing repo rows."""
        group = await _make_gitops_group(db)

        await import_group_from_yaml(
            group_id=group.id,
            yaml_content=REPOS_YAML,
            commit_sha="sha_seed",
            db=db,
        )

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=EMPTY_REPOS_YAML,
            commit_sha="sha_empty",
            db=db,
        )
        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.removed == 1
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRepository).where(PackageRepository.group_id == group.id)
        )
        assert db_rows.scalars().all() == []

    async def test_protected_package_stripped_others_imported(self, db):
        """Protected package is dropped with warning; other packages are still imported."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=PACKAGES_WITH_PROTECTED_YAML,
            commit_sha="sha_protected",
            db=db,
        )
        assert result.success is True

        pkg_result = next(m for m in result.modules if m.module == "packages")
        # bash is protected and stripped, only nginx should be added.
        assert pkg_result.added == 1
        assert pkg_result.changed is True

        db_rows = await db.execute(
            select(PackageRule).where(PackageRule.group_id == group.id)
        )
        rules = db_rows.scalars().all()
        assert len(rules) == 1
        assert rules[0].package_name == "nginx"

    async def test_invalid_repo_url_returns_error(self, db):
        """Repository with non-http(s) URL produces a module error."""
        group = await _make_gitops_group(db)

        result = await import_group_from_yaml(
            group_id=group.id,
            yaml_content=INVALID_REPO_URL_YAML,
            commit_sha="sha_bad_url",
            db=db,
        )
        assert result.success is False
        assert result.error_message is not None
        assert "badrepo" in result.error_message or "ftp" in result.error_message

        pkg_result = next(m for m in result.modules if m.module == "packages")
        assert pkg_result.error_message is not None

        refreshed = await db.execute(select(HostGroup).where(HostGroup.id == group.id))
        grp = refreshed.scalar_one()
        assert grp.gitops_status == GitOpsStatus.error

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

        pkg_result = next(m for m in r2.modules if m.module == "packages")
        assert pkg_result.added == 0
        assert pkg_result.removed == 0
        # 1 package + 1 repo = 2 unchanged
        assert pkg_result.unchanged == 2
        assert pkg_result.changed is False
