import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.serializer import YAMLParseError, parse_yaml, yaml_rules_to_specs
from app.models.firewall_rule import FirewallRule
from app.models.git_repository import GitOpsStatus
from app.models.host_group import HostGroup
from app.rules.converter import firewall_rules_to_specs, spec_to_firewall_rule
from app.sync.diff import RulesetDiff, compute_diff

logger = logging.getLogger(__name__)

_GITOPS_LOCK_OFFSET = 1_000_000


@dataclass
class ImportResult:
    success: bool = False
    rules_added: int = 0
    rules_removed: int = 0
    rules_unchanged: int = 0
    diff: Optional[RulesetDiff] = None
    error_message: Optional[str] = None


async def import_group_from_yaml(
    group_id: int,
    yaml_content: str,
    commit_sha: str,
    db: AsyncSession,
) -> ImportResult:
    """Import firewall rules from YAML into a host group.

    Acquires a per-group advisory lock, validates YAML, computes diff,
    replaces non-system rules if changed, and logs to audit trail.
    On failure: sets gitops_status="error", rules stay unchanged.
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
        parsed = parse_yaml(yaml_content)
    except YAMLParseError as e:
        group.gitops_status = GitOpsStatus.error
        group.gitops_error_message = f"YAML parse error: {e}"
        await db.flush()
        return ImportResult(error_message=str(e))

    if not parsed.firewall or not parsed.firewall.rules:
        desired_specs = []
        logger.warning("Group %d: YAML has empty firewall rules", group_id)
    else:
        try:
            desired_specs = yaml_rules_to_specs(parsed.firewall.rules)
        except YAMLParseError as e:
            group.gitops_status = GitOpsStatus.error
            group.gitops_error_message = f"Rule validation error: {e}"
            await db.flush()
            return ImportResult(error_message=str(e))

    current_result = await db.execute(
        select(FirewallRule).where(
            FirewallRule.group_id == group_id,
            FirewallRule.is_system == False,  # noqa: E712
        )
    )
    current_rules = list(current_result.scalars().all())
    current_specs = firewall_rules_to_specs(current_rules)

    diff = compute_diff(current_specs, desired_specs)

    import_result = ImportResult(
        success=True,
        rules_added=len(diff.rules_to_add),
        rules_removed=len(diff.rules_to_remove),
        rules_unchanged=len(diff.rules_unchanged),
        diff=diff,
    )

    if diff.has_changes:
        await db.execute(
            delete(FirewallRule).where(
                FirewallRule.group_id == group_id,
                FirewallRule.is_system == False,  # noqa: E712
            )
        )

        for i, spec in enumerate(desired_specs):
            rule = spec_to_firewall_rule(spec, group_id)
            rule.priority = i
            db.add(rule)

        # Create audit log entry
        before_state = {
            "rules": [asdict(s) for s in current_specs],
            "count": len(current_specs),
        }
        after_state = {
            "rules": [asdict(s) for s in desired_specs],
            "count": len(desired_specs),
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }
        await log_action(
            db=db,
            action="gitops.import",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    # Update group status
    group.gitops_status = GitOpsStatus.synced
    group.gitops_error_message = None
    group.gitops_last_import_at = datetime.now(timezone.utc)
    await db.flush()

    logger.info(
        "GitOps import for group %d: +%d -%d =%d (SHA: %s)",
        group_id,
        import_result.rules_added,
        import_result.rules_removed,
        import_result.rules_unchanged,
        commit_sha[:8],
    )

    return import_result
