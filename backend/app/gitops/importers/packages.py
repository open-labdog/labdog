"""Packages module GitOps import handler.

Handles both ``PackageRule`` (packages) and ``PackageRepository``
(package_repositories) in a single call so they share one audit event and one
:class:`ModuleImportResult`.
"""

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers._common import (
    ModuleImportResult,
    TupleDiff,
    log_import_summary,
    replace_group_rows,
    warn_if_section_empty,
)
from app.gitops.schema import LabDogGroupYAML, PackageRepositoryYAML, PackageYAML
from app.models.host_group import HostGroup
from app.packages.constants import is_protected
from app.packages.models import (
    PackageManager,
    PackageRepository,
    PackageRule,
    PackageState,
    RepoType,
)
from app.packages.schemas import PackageRepositoryCreate, PackageRuleCreate

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


def _new_package(entry: PackageYAML, group_id: int) -> PackageRule:
    return PackageRule(
        group_id=group_id,
        package_name=entry.package_name,
        version=entry.version,
        state=PackageState(entry.state),
        package_manager=PackageManager(entry.package_manager),
        priority=entry.priority,
        comment=entry.comment,
        hold=entry.hold,
    )


def _new_repo(entry: PackageRepositoryYAML, group_id: int) -> PackageRepository:
    return PackageRepository(
        group_id=group_id,
        name=entry.name,
        url=entry.url,
        key_url=entry.key_url,
        repo_type=RepoType(entry.repo_type),
        distribution=entry.distribution,
        components=entry.components,
        state=PackageState(entry.state),
    )


def _package_state(r: PackageRule) -> dict:
    return {
        "package_name": r.package_name,
        "version": r.version,
        "state": str(r.state),
        "package_manager": str(r.package_manager),
        "priority": r.priority,
        "comment": r.comment,
        "hold": r.hold,
    }


def _repo_state(r: PackageRepository) -> dict:
    return {
        "name": r.name,
        "url": r.url,
        "key_url": r.key_url,
        "repo_type": str(r.repo_type),
        "distribution": r.distribution,
        "components": r.components,
        "state": str(r.state),
    }


def _yaml_package_state(e: PackageYAML) -> dict:
    return {
        "package_name": e.package_name,
        "version": e.version,
        "state": e.state,
        "package_manager": e.package_manager,
        "priority": e.priority,
        "comment": e.comment,
        "hold": e.hold,
    }


def _yaml_repo_state(e: PackageRepositoryYAML) -> dict:
    return {
        "name": e.name,
        "url": e.url,
        "key_url": e.key_url,
        "repo_type": e.repo_type,
        "distribution": e.distribution,
        "components": e.components,
        "state": e.state,
    }


async def import_packages(
    group: HostGroup,
    parsed: LabDogGroupYAML,
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
        parsed: Validated ``LabDogGroupYAML`` from the current commit.
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

    warn_if_section_empty(logger, group_id, parsed.packages, "packages", "package rules")

    for entry in parsed.packages or []:
        if is_protected(entry.package_name):
            logger.warning(
                "Group %d: skipping protected package '%s' in GitOps YAML",
                group_id,
                entry.package_name,
            )
            continue
        # SEC-43: one rule per field. Round-trip through the API's create
        # schema, so a value the API refuses cannot arrive through Git instead.
        try:
            PackageRuleCreate.model_validate(entry.model_dump())
        except ValidationError as exc:
            return ModuleImportResult(
                module="packages",
                error_message=f"Invalid package '{entry.package_name}': {exc}",
            )
        desired_packages.append(entry)

    # ------------------------------------------------------------------
    # 2. Build desired package-repository entries (with URL validation)
    # ------------------------------------------------------------------
    desired_repos: list[PackageRepositoryYAML] = []

    warn_if_section_empty(
        logger, group_id, parsed.package_repositories, "package_repositories", "repositories"
    )

    for entry in parsed.package_repositories or []:
        try:
            PackageRepositoryCreate.model_validate(entry.model_dump())
        except ValidationError as exc:
            return ModuleImportResult(
                module="packages",
                error_message=f"Invalid repository '{entry.name}': {exc}",
            )
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

    pkg_diff = TupleDiff.of(
        {_rule_to_pkg_tuple(r) for r in current_pkg_rules},
        {_yaml_to_pkg_tuple(e) for e in desired_packages},
    )

    # ------------------------------------------------------------------
    # 4. Diff repositories
    # ------------------------------------------------------------------
    current_repo_result = await db.execute(
        select(PackageRepository).where(PackageRepository.group_id == group_id)
    )
    current_repos: list[PackageRepository] = list(current_repo_result.scalars().all())

    repo_diff = TupleDiff.of(
        {_repo_orm_to_tuple(r) for r in current_repos},
        {_yaml_to_repo_tuple(e) for e in desired_repos},
    )

    diff = pkg_diff + repo_diff
    module_result = diff.result("packages")

    if diff.changed:
        # Capture before-state for audit.
        before_state = {
            "packages": [_package_state(r) for r in current_pkg_rules],
            "repositories": [_repo_state(r) for r in current_repos],
        }

        # Delete-and-replace each table only if it changed.
        if pkg_diff.changed:
            await replace_group_rows(db, PackageRule, group_id, desired_packages, _new_package)
        if repo_diff.changed:
            await replace_group_rows(db, PackageRepository, group_id, desired_repos, _new_repo)

        after_state = {
            "packages": [_yaml_package_state(e) for e in desired_packages],
            "repositories": [_yaml_repo_state(e) for e in desired_repos],
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

    log_import_summary(logger, "packages", group_id, module_result, commit_sha)

    return module_result
