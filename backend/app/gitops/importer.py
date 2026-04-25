import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.gitops.importers.cron_jobs import import_cron_jobs
from app.gitops.importers.firewall import ModuleImportResult, import_firewall
from app.gitops.importers.hosts_entries import import_hosts_entries
from app.gitops.importers.packages import import_packages
from app.gitops.importers.resolver import import_resolver
from app.gitops.importers.services import import_services
from app.gitops.importers.users import import_users
from app.gitops.importers.workflow import import_workflow
from app.gitops.serializer import YAMLParseError, parse_yaml
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup

logger = logging.getLogger(__name__)

_GITOPS_LOCK_OFFSET = 1_000_000


@dataclass
class ImportResult:
    """Top-level result returned by :func:`import_group_from_yaml`.

    Attributes:
        success: ``True`` when all module handlers completed without error.
        modules: Per-module sub-results (one entry per handler called).
        error_message: Set when the import failed at the dispatcher level or
            when any module handler reported an error.
    """

    success: bool = False
    modules: list[ModuleImportResult] = field(default_factory=list)
    error_message: str | None = None

    def any_changes(self) -> bool:
        """Return ``True`` if any module handler applied mutations."""
        return any(m.changed for m in self.modules)


async def import_group_from_yaml(
    group_id: int,
    yaml_content: str,
    commit_sha: str,
    db: AsyncSession,
) -> ImportResult:
    """Import all GitOps-managed modules from YAML into a host group.

    Acquires a per-group PostgreSQL advisory lock, validates the YAML,
    calls each enabled module handler in sequence, and updates the group's
    GitOps status.  On any failure the group status is set to ``error`` and
    the partial transaction is rolled back by the caller.

    Args:
        group_id: Primary key of the target host group.
        yaml_content: Raw YAML string read from the git repository.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session (caller owns the transaction).

    Returns:
        An :class:`ImportResult` describing what happened.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": group_id + _GITOPS_LOCK_OFFSET},
    )

    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        return ImportResult(error_message=f"Group {group_id} not found")
    if not group.gitops_enabled:
        return ImportResult(error_message=f"Group {group_id} does not have GitOps enabled")

    group.gitops_status = GitOpsStatus.importing
    await db.flush()

    try:
        try:
            parsed = parse_yaml(yaml_content)
        except YAMLParseError as e:
            group.gitops_status = GitOpsStatus.error
            group.gitops_error_message = f"YAML parse error: {e}"
            await db.flush()
            return ImportResult(error_message=str(e))

        # --- Module handlers (called in sequence) ---
        module_results: list[ModuleImportResult] = []

        firewall_result = await import_firewall(group, parsed, commit_sha, db)
        module_results.append(firewall_result)

        services_result = await import_services(group, parsed, commit_sha, db)
        module_results.append(services_result)

        packages_result = await import_packages(group, parsed, commit_sha, db)
        module_results.append(packages_result)

        hosts_entries_result = await import_hosts_entries(group, parsed, commit_sha, db)
        module_results.append(hosts_entries_result)

        cron_jobs_result = await import_cron_jobs(group, parsed, commit_sha, db)
        module_results.append(cron_jobs_result)

        resolver_result = await import_resolver(group, parsed, commit_sha, db)
        module_results.append(resolver_result)

        users_result = await import_users(group, parsed, commit_sha, db)
        module_results.append(users_result)

        workflow_result = await import_workflow(group, parsed, commit_sha, db)
        module_results.append(workflow_result)

        # If any handler reported an error, abort with error status.
        failed = [m for m in module_results if m.error_message]
        if failed:
            first_error = failed[0].error_message
            group.gitops_status = GitOpsStatus.error
            group.gitops_error_message = first_error
            await db.flush()
            return ImportResult(
                modules=module_results,
                error_message=first_error,
            )

        # All handlers succeeded — mark group as synced.
        group.gitops_status = GitOpsStatus.synced
        group.gitops_error_message = None
        group.gitops_last_import_at = datetime.now(UTC)
        await db.flush()

        import_result = ImportResult(success=True, modules=module_results)

        logger.info(
            "GitOps import for group %d complete: %d module(s) (SHA: %s)",
            group_id,
            len(module_results),
            commit_sha[:8],
        )

        return import_result

    except Exception as e:
        group.gitops_status = GitOpsStatus.error
        group.gitops_error_message = f"Unexpected error: {e}"
        await db.flush()
        raise
