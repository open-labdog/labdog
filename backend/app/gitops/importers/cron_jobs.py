"""Cron jobs module GitOps import handler."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.cron.models import CronJob, CronState
from app.cron.validators import validate_cron_expression
from app.gitops.importers._common import (
    ModuleImportResult,
    TupleDiff,
    log_import_summary,
    replace_group_rows,
    warn_if_section_empty,
)
from app.gitops.schema import CronJobYAML, LabDogGroupYAML
from app.models.host_group import HostGroup

logger = logging.getLogger(__name__)


def _cron_tuple(
    name: str,
    user: str,
    schedule: str,
    command: str,
    environment: dict[str, str],
    state: str,
    priority: int,
    comment: str | None,
) -> tuple:
    """Return a comparable tuple of cron job fields for diffing.

    Environment dict is compared by sorted items so that key ordering in the
    YAML or the DB does not produce spurious diffs.
    """
    return (
        name,
        user,
        schedule,  # preserved byte-identical — NOT normalised
        command,
        tuple(sorted(environment.items())),
        state,
        priority,
        comment,
    )


def _orm_to_tuple(job: CronJob) -> tuple:
    """Extract a comparable tuple from a ``CronJob`` ORM instance."""
    return _cron_tuple(
        name=job.name,
        user=job.user,
        schedule=job.schedule,
        command=job.command,
        environment=dict(job.environment or {}),
        state=str(job.state),
        priority=job.priority,
        comment=job.comment,
    )


def _yaml_to_tuple(entry: CronJobYAML) -> tuple:
    """Extract a comparable tuple from a ``CronJobYAML`` schema instance."""
    return _cron_tuple(
        name=entry.name,
        user=entry.user,
        schedule=entry.schedule,
        command=entry.command,
        environment=dict(entry.environment),
        state=entry.state,
        priority=entry.priority,
        comment=entry.comment,
    )


def _new_job(entry: CronJobYAML, group_id: int) -> CronJob:
    return CronJob(
        group_id=group_id,
        name=entry.name,
        user=entry.user,
        schedule=entry.schedule,
        command=entry.command,
        environment=dict(entry.environment),
        state=CronState(entry.state),
        priority=entry.priority,
        comment=entry.comment,
    )


def _job_state(j: CronJob) -> dict:
    return {
        "name": j.name,
        "user": j.user,
        "schedule": j.schedule,
        "command": j.command,
        "environment": dict(j.environment or {}),
        "state": str(j.state),
        "priority": j.priority,
        "comment": j.comment,
    }


def _yaml_state(e: CronJobYAML) -> dict:
    return {
        "name": e.name,
        "user": e.user,
        "schedule": e.schedule,
        "command": e.command,
        "environment": dict(e.environment),
        "state": e.state,
        "priority": e.priority,
        "comment": e.comment,
    }


async def import_cron_jobs(
    group: HostGroup,
    parsed: LabDogGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import cron jobs from *parsed* YAML into *group*.

    Derives desired cron job entries from ``parsed.cron_jobs``, diffs against
    current group-scoped ``CronJob`` rows, replaces them when there are
    changes, and emits a ``gitops.import.cron_jobs`` audit event.

    Missing or ``None`` ``cron_jobs`` section and an empty list both trigger
    wipe semantics — all existing group-scoped rows are deleted.

    Schedule strings are preserved byte-identical and are never normalised.
    This guarantees that re-importing the same YAML does not produce spurious
    diffs even when two expressions are semantically equivalent (e.g. ``*/5``
    vs ``0/5``).

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``LabDogGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` describing what changed (or the error).
    """
    group_id = group.id

    raw_entries = parsed.cron_jobs or []  # None → wipe (same as [])
    warn_if_section_empty(logger, group_id, parsed.cron_jobs, "cron_jobs", "cron jobs")

    # Validate all schedules before mutating the DB.
    desired_entries: list[CronJobYAML] = []
    for entry in raw_entries:
        try:
            validate_cron_expression(entry.schedule)
        except ValueError as exc:
            return ModuleImportResult(
                module="cron_jobs",
                error_message=(f"Invalid cron schedule for job '{entry.name}': {exc}"),
            )
        desired_entries.append(entry)

    # Fetch current group-scoped rows (host-level rows are not GitOps-managed).
    current_result = await db.execute(select(CronJob).where(CronJob.group_id == group_id))
    current_jobs: list[CronJob] = list(current_result.scalars().all())

    # Diff by comparing sets of field tuples.
    diff = TupleDiff.of(
        {_orm_to_tuple(j) for j in current_jobs},
        {_yaml_to_tuple(e) for e in desired_entries},
    )
    module_result = diff.result("cron_jobs")

    if diff.changed:
        # Capture before state for audit.
        before_state = {
            "jobs": [_job_state(j) for j in current_jobs],
            "count": len(current_jobs),
        }

        # Delete-and-replace, inserting desired entries in list order.
        await replace_group_rows(db, CronJob, group_id, desired_entries, _new_job)

        after_state = {
            "jobs": [_yaml_state(e) for e in desired_entries],
            "count": len(desired_entries),
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }

        await log_action(
            db=db,
            action="gitops.import.cron_jobs",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    log_import_summary(logger, "cron_jobs", group_id, module_result, commit_sha)

    return module_result
