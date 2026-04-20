"""Packages module GitOps import handler.

Handles both ``PackageRule`` (packages) and ``PackageRepository``
(package_repositories) in a single call so they share one audit event and one
:class:`ModuleImportResult`.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import BarricadeGroupYAML, PackageRepositoryYAML, PackageYAML
from app.models.host_group import HostGroup
from app.packages.constants import is_protected
from app.packages.models import (
    PackageManager,
    PackageRepository,
    PackageRule,
    PackageState,
    RepoType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diff helpers — packages
# ---------------------------------------------------------------------------


def _pkg_tuple(
    package_name: str,
    version: str | None,
    state: str,
    package_manager: str,
    priority: int,
    comment: str | None,
    hold: bool,
) -> tuple:
    """Return a comparable tuple of package-rule fields for diffing."""
    return (package_name, version, state, package_manager, priority, comment, hold)


def _rule_to_pkg_tuple(rule: PackageRule) -> tuple:
    """Extract a comparable tuple from a ``PackageRule`` ORM instance."""
    return _pkg_tuple(
        package_name=rule.package_name,
        version=rule.version,
        state=str(rule.state),
        package_manager=str(rule.package_manager),
        priority=rule.priority,
        comment=rule.comment,
        hold=rule.hold,
    )


def _yaml_to_pkg_tuple(entry: PackageYAML) -> tuple:
    """Extract a comparable tuple from a ``PackageYAML`` schema instance."""
    return _pkg_tuple(
        package_name=entry.package_name,
        version=entry.version,
        state=entry.state,
        package_manager=entry.package_manager,
        priority=entry.priority,
        comment=entry.comment,
        hold=entry.hold,
    )


# ---------------------------------------------------------------------------
# Diff helpers — package repositories
# ---------------------------------------------------------------------------


def _repo_tuple(
    name: str,
    url: str,
    key_url: str | None,
    repo_type: str,
    distribution: str | None,
    components: str | None,
    state: str,
) -> tuple:
    """Return a comparable tuple of package-repository fields for diffing."""
    return (name, url, key_url, repo_type, distribution, components, state)


def _repo_orm_to_tuple(repo: PackageRepository) -> tuple:
    """Extract a comparable tuple from a ``PackageRepository`` ORM instance."""
    return _repo_tuple(
        name=repo.name,
        url=repo.url,
        key_url=repo.key_url,
        repo_type=str(repo.repo_type),
        distribution=repo.distribution,
        components=repo.components,
        state=str(repo.state),
    )


def _yaml_to_repo_tuple(entry: PackageRepositoryYAML) -> tuple:
    """Extract a comparable tuple from a ``PackageRepositoryYAML`` schema instance."""
    return _repo_tuple(
        name=entry.name,
        url=entry.url,
        key_url=entry.key_url,
        repo_type=entry.repo_type,
        distribution=entry.distribution,
        components=entry.components,
        state=entry.state,
    )


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def import_packages(
    group: HostGroup,
    parsed: BarricadeGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import package rules and repositories from *parsed* YAML into *group*.

    Handles both ``parsed.packages`` (→ ``PackageRule``) and
    ``parsed.package_repositories`` (→ ``PackageRepository``) in a single
    call, emitting one ``gitops.import.packages`` audit event.

    Missing or ``None`` sections and empty lists both trigger wipe semantics —
    all existing group-scoped rows for that table are deleted.

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``BarricadeGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` with ``module="packages"`` and counts
        summed across both tables.
    """
    group_id = group.id

    # ------------------------------------------------------------------
    # 1. Build desired package-rule entries
    # ------------------------------------------------------------------
    desired_packages: list[PackageYAML] = []

    if parsed.packages is None:
        logger.warning(
            "Group %d: YAML has no packages section — wiping package rules", group_id
        )
    elif not parsed.packages:
        logger.warning(
            "Group %d: YAML has empty packages list — wiping package rules", group_id
        )

    for entry in parsed.packages or []:
        if is_protected(entry.package_name):
            logger.warning(
                "Group %d: skipping protected package '%s' in GitOps YAML",
                group_id,
                entry.package_name,
            )
            continue
        desired_packages.append(entry)

    # ------------------------------------------------------------------
    # 2. Build desired package-repository entries (with URL validation)
    # ------------------------------------------------------------------
    desired_repos: list[PackageRepositoryYAML] = []

    if parsed.package_repositories is None:
        logger.warning(
            "Group %d: YAML has no package_repositories section — wiping repositories",
            group_id,
        )
    elif not parsed.package_repositories:
        logger.warning(
            "Group %d: YAML has empty package_repositories list — wiping repositories",
            group_id,
        )

    for entry in parsed.package_repositories or []:
        if not entry.url.startswith(("https://", "http://")):
            return ModuleImportResult(
                module="packages",
                error_message=(
                    f"Repository '{entry.name}' has an invalid URL '{entry.url}': "
                    "must start with https:// or http://"
                ),
            )
        desired_repos.append(entry)

    # ------------------------------------------------------------------
    # 3. Diff packages
    # ------------------------------------------------------------------
    current_pkg_result = await db.execute(
        select(PackageRule).where(PackageRule.group_id == group_id)
    )
    current_pkg_rules: list[PackageRule] = list(current_pkg_result.scalars().all())

    current_pkg_tuples = {_rule_to_pkg_tuple(r) for r in current_pkg_rules}
    desired_pkg_tuples = {_yaml_to_pkg_tuple(e) for e in desired_packages}

    pkg_added = len(desired_pkg_tuples - current_pkg_tuples)
    pkg_removed = len(current_pkg_tuples - desired_pkg_tuples)
    pkg_unchanged = len(current_pkg_tuples & desired_pkg_tuples)
    pkg_changed = bool(pkg_added or pkg_removed)

    # ------------------------------------------------------------------
    # 4. Diff repositories
    # ------------------------------------------------------------------
    current_repo_result = await db.execute(
        select(PackageRepository).where(PackageRepository.group_id == group_id)
    )
    current_repos: list[PackageRepository] = list(current_repo_result.scalars().all())

    current_repo_tuples = {_repo_orm_to_tuple(r) for r in current_repos}
    desired_repo_tuples = {_yaml_to_repo_tuple(e) for e in desired_repos}

    repo_added = len(desired_repo_tuples - current_repo_tuples)
    repo_removed = len(current_repo_tuples - desired_repo_tuples)
    repo_unchanged = len(current_repo_tuples & desired_repo_tuples)
    repo_changed = bool(repo_added or repo_removed)

    has_changes = pkg_changed or repo_changed

    module_result = ModuleImportResult(
        module="packages",
        added=pkg_added + repo_added,
        removed=pkg_removed + repo_removed,
        unchanged=pkg_unchanged + repo_unchanged,
        changed=has_changes,
    )

    if has_changes:
        # Capture before-state for audit.
        before_state = {
            "packages": [
                {
                    "package_name": r.package_name,
                    "version": r.version,
                    "state": str(r.state),
                    "package_manager": str(r.package_manager),
                    "priority": r.priority,
                    "comment": r.comment,
                    "hold": r.hold,
                }
                for r in current_pkg_rules
            ],
            "repositories": [
                {
                    "name": r.name,
                    "url": r.url,
                    "key_url": r.key_url,
                    "repo_type": str(r.repo_type),
                    "distribution": r.distribution,
                    "components": r.components,
                    "state": str(r.state),
                }
                for r in current_repos
            ],
        }

        # Delete-and-replace packages.
        if pkg_changed:
            await db.execute(
                delete(PackageRule).where(PackageRule.group_id == group_id)
            )
            for entry in desired_packages:
                rule = PackageRule(
                    group_id=group_id,
                    package_name=entry.package_name,
                    version=entry.version,
                    state=PackageState(entry.state),
                    package_manager=PackageManager(entry.package_manager),
                    priority=entry.priority,
                    comment=entry.comment,
                    hold=entry.hold,
                )
                db.add(rule)

        # Delete-and-replace repositories.
        if repo_changed:
            await db.execute(
                delete(PackageRepository).where(PackageRepository.group_id == group_id)
            )
            for entry in desired_repos:
                repo = PackageRepository(
                    group_id=group_id,
                    name=entry.name,
                    url=entry.url,
                    key_url=entry.key_url,
                    repo_type=RepoType(entry.repo_type),
                    distribution=entry.distribution,
                    components=entry.components,
                    state=PackageState(entry.state),
                )
                db.add(repo)

        after_state = {
            "packages": [
                {
                    "package_name": e.package_name,
                    "version": e.version,
                    "state": e.state,
                    "package_manager": e.package_manager,
                    "priority": e.priority,
                    "comment": e.comment,
                    "hold": e.hold,
                }
                for e in desired_packages
            ],
            "repositories": [
                {
                    "name": e.name,
                    "url": e.url,
                    "key_url": e.key_url,
                    "repo_type": e.repo_type,
                    "distribution": e.distribution,
                    "components": e.components,
                    "state": e.state,
                }
                for e in desired_repos
            ],
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }

        await log_action(
            db=db,
            action="gitops.import.packages",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    logger.info(
        "GitOps packages import for group %d: +%d -%d =%d (SHA: %s)",
        group_id,
        module_result.added,
        module_result.removed,
        module_result.unchanged,
        commit_sha[:8],
    )

    return module_result
