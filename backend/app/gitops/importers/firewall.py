"""Firewall module GitOps import handler."""

import logging
from dataclasses import asdict, dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.schema import LabDogGroupYAML
from app.gitops.serializer import YAMLParseError, yaml_rules_to_specs
from app.models.firewall_rule import FirewallRule
from app.models.host_group import HostGroup
from app.rules.converter import firewall_rules_to_specs, spec_to_firewall_rule

logger = logging.getLogger(__name__)


@dataclass
class ModuleImportResult:
    """Result produced by a single per-module import handler.

    Attributes:
        module: Module name (e.g. ``"firewall"``).
        added: Number of rows added.
        removed: Number of rows removed.
        unchanged: Number of rows that were already correct.
        changed: ``True`` when any mutations were performed.
        error_message: Non-``None`` when the handler failed.
    """

    module: str
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    changed: bool = False
    error_message: str | None = None


async def import_firewall(
    group: HostGroup,
    parsed: LabDogGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import firewall rules from *parsed* YAML into *group*.

    Derives desired specs from ``parsed.firewall``, diffs against current
    non-system rules, replaces them when there are changes, updates chain
    policies, and emits a ``gitops.import.firewall`` audit event.

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

    # Derive desired specs from the YAML firewall section.
    if not parsed.firewall or not parsed.firewall.rules:
        desired_specs = []
        if not parsed.firewall:
            logger.warning("Group %d: YAML has no firewall section — wiping rules", group_id)
        else:
            logger.warning("Group %d: YAML has empty firewall rules — wiping rules", group_id)
    else:
        try:
            desired_specs = yaml_rules_to_specs(parsed.firewall.rules)
        except YAMLParseError as e:
            return ModuleImportResult(
                module="firewall",
                error_message=f"Rule validation error: {e}",
            )

    # Fetch current non-system rules.
    current_result = await db.execute(
        select(FirewallRule).where(
            FirewallRule.group_id == group_id,
            FirewallRule.is_system == False,  # noqa: E712
        )
    )
    current_rules = list(current_result.scalars().all())

    from app.sync.diff import compute_diff

    current_specs = firewall_rules_to_specs(current_rules)
    diff = compute_diff(current_specs, desired_specs)

    # Determine chain policy changes.
    new_input_policy = parsed.firewall.input_policy if parsed.firewall else None
    new_output_policy = parsed.firewall.output_policy if parsed.firewall else None
    policies_changed = (
        group.input_policy != new_input_policy or group.output_policy != new_output_policy
    )

    module_result = ModuleImportResult(
        module="firewall",
        added=len(diff.rules_to_add),
        removed=len(diff.rules_to_remove),
        unchanged=len(diff.rules_unchanged),
        changed=diff.has_changes or policies_changed,
    )

    if diff.has_changes or policies_changed:
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

        # Capture before-state for audit (policies may have just changed).
        before_input_policy = group.input_policy
        before_output_policy = group.output_policy

        # Update chain policies.
        group.input_policy = new_input_policy
        group.output_policy = new_output_policy

        before_state = {
            "rules": [asdict(s) for s in current_specs],
            "count": len(current_specs),
            "input_policy": before_input_policy,
            "output_policy": before_output_policy,
        }
        after_state = {
            "rules": [asdict(s) for s in desired_specs],
            "count": len(desired_specs),
            "input_policy": new_input_policy,
            "output_policy": new_output_policy,
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }
        await log_action(
            db=db,
            action="gitops.import.firewall",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    logger.info(
        "GitOps firewall import for group %d: +%d -%d =%d (SHA: %s)",
        group_id,
        module_result.added,
        module_result.removed,
        module_result.unchanged,
        commit_sha[:8],
    )

    return module_result
