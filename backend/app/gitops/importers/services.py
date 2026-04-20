"""Services module GitOps import handler."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import BarricadeGroupYAML, ServiceYAML
from app.models.host_group import HostGroup
from app.services.constants import PROTECTED_SERVICES
from app.services.models import DeployMode, ServiceRule, ServiceState

logger = logging.getLogger(__name__)


def _normalize_unit_content(value: str | None) -> str | None:
    """Strip trailing whitespace per line and enforce a final newline.

    This prevents whitespace-only drift from churning imports unnecessarily.
    """
    if value is None:
        return None
    lines = value.rstrip("\n").splitlines()
    stripped = "\n".join(line.rstrip() for line in lines) + "\n"
    return stripped


def _service_tuple(
    service_name: str,
    state: str,
    enabled: bool,
    priority: int,
    comment: str | None,
    unit_content: str | None,
    deploy_mode: str,
) -> tuple:
    """Return a comparable tuple of service fields for diffing."""
    return (
        service_name,
        state,
        enabled,
        priority,
        comment,
        _normalize_unit_content(unit_content),
        deploy_mode,
    )


def _rule_to_tuple(rule: ServiceRule) -> tuple:
    """Extract a comparable tuple from a ``ServiceRule`` ORM instance."""
    return _service_tuple(
        service_name=rule.service_name,
        state=str(rule.state),
        enabled=rule.enabled,
        priority=rule.priority,
        comment=rule.comment,
        unit_content=rule.unit_content,
        deploy_mode=str(rule.deploy_mode),
    )


def _yaml_to_tuple(entry: ServiceYAML) -> tuple:
    """Extract a comparable tuple from a ``ServiceYAML`` schema instance."""
    return _service_tuple(
        service_name=entry.service_name,
        state=entry.state,
        enabled=entry.enabled,
        priority=entry.priority,
        comment=entry.comment,
        unit_content=entry.unit_content,
        deploy_mode=entry.deploy_mode,
    )


async def import_services(
    group: HostGroup,
    parsed: BarricadeGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import service rules from *parsed* YAML into *group*.

    Derives desired service entries from ``parsed.services``, diffs against
    current group-scoped ``ServiceRule`` rows, replaces them when there are
    changes, and emits a ``gitops.import.services`` audit event.

    Missing or ``None`` ``services`` section and an empty list both trigger
    wipe semantics — all existing group-scoped rows are deleted.

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``BarricadeGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` describing what changed (or the error).
    """
    group_id = group.id

    # Build desired list, applying protected-service filter and validation.
    desired_entries: list[ServiceYAML] = []

    raw_entries = parsed.services or []  # None → wipe (same as [])
    if parsed.services is None:
        logger.warning(
            "Group %d: YAML has no services section — wiping service rules", group_id
        )
    elif not parsed.services:
        logger.warning(
            "Group %d: YAML has empty services list — wiping service rules", group_id
        )

    for entry in raw_entries:
        if entry.service_name in PROTECTED_SERVICES:
            logger.warning(
                "Group %d: skipping protected service '%s' in GitOps YAML",
                group_id,
                entry.service_name,
            )
            continue

        if entry.deploy_mode == "full" and entry.unit_content is None:
            return ModuleImportResult(
                module="services",
                error_message=(
                    f"Service '{entry.service_name}' has deploy_mode=full "
                    "but unit_content is null — unit_content is required for full deploy"
                ),
            )

        desired_entries.append(entry)

    # Fetch current group-scoped rows (host-level rows are not GitOps-managed).
    current_result = await db.execute(
        select(ServiceRule).where(ServiceRule.group_id == group_id)
    )
    current_rules: list[ServiceRule] = list(current_result.scalars().all())

    # Diff by comparing sets of field tuples.
    current_tuples = {_rule_to_tuple(r) for r in current_rules}
    desired_tuples = {_yaml_to_tuple(e) for e in desired_entries}

    tuples_added = desired_tuples - current_tuples
    tuples_removed = current_tuples - desired_tuples
    tuples_unchanged = current_tuples & desired_tuples

    has_changes = bool(tuples_added or tuples_removed)

    module_result = ModuleImportResult(
        module="services",
        added=len(tuples_added),
        removed=len(tuples_removed),
        unchanged=len(tuples_unchanged),
        changed=has_changes,
    )

    if has_changes:
        # Capture before state for audit.
        before_state = {
            "services": [
                {
                    "service_name": r.service_name,
                    "state": str(r.state),
                    "enabled": r.enabled,
                    "priority": r.priority,
                    "comment": r.comment,
                    "unit_content": r.unit_content,
                    "deploy_mode": str(r.deploy_mode),
                }
                for r in current_rules
            ],
            "count": len(current_rules),
        }

        # Delete-and-replace: remove all existing group-scoped rows.
        await db.execute(
            delete(ServiceRule).where(ServiceRule.group_id == group_id)
        )

        # Insert desired entries in list order.
        for entry in desired_entries:
            rule = ServiceRule(
                group_id=group_id,
                service_name=entry.service_name,
                state=ServiceState(entry.state),
                enabled=entry.enabled,
                priority=entry.priority,
                comment=entry.comment,
                unit_content=entry.unit_content,
                deploy_mode=DeployMode(entry.deploy_mode),
            )
            db.add(rule)

        after_state = {
            "services": [
                {
                    "service_name": e.service_name,
                    "state": e.state,
                    "enabled": e.enabled,
                    "priority": e.priority,
                    "comment": e.comment,
                    "unit_content": e.unit_content,
                    "deploy_mode": e.deploy_mode,
                }
                for e in desired_entries
            ],
            "count": len(desired_entries),
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }

        await log_action(
            db=db,
            action="gitops.import.services",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    logger.info(
        "GitOps services import for group %d: +%d -%d =%d (SHA: %s)",
        group_id,
        module_result.added,
        module_result.removed,
        module_result.unchanged,
        commit_sha[:8],
    )

    return module_result
