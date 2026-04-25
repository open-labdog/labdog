"""Update-workflow GitOps import handler (singleton shape).

Imports the per-group update workflow (``UpdateWorkflow``) from the
``workflow:`` section of a group YAML file. One workflow row exists per
group (``group_id`` is a unique FK), so this importer follows the same
**leave-alone** singleton pattern as :mod:`app.gitops.importers.resolver`:

* Section absent or ``null`` ⇒ DB row left untouched, no audit emitted.
* Section present ⇒ upsert on diff, idempotent on identical re-imports.

Beyond the structural validation already done by ``WorkflowYAML``, this
handler also validates ``action_key`` against the live action registry
and enforces per-action parameter requirements that mirror the
``PUT /groups/{id}/workflow`` API endpoint (specifically the
``linux-os-upgrade`` ``current_version``/``next_version`` rule).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import LabDogGroupYAML, WorkflowYAML
from app.models.host_group import HostGroup
from app.workflows.models import UpdateWorkflow

logger = logging.getLogger(__name__)


_TRACKED_FIELDS = (
    "enabled",
    "schedule_cron",
    "batch_size",
    "pre_update_snapshot",
    "auto_rollback",
    "auto_reboot",
    "verification_prompt",
    "action_key",
    "action_parameters",
)


def _workflow_snapshot(workflow: UpdateWorkflow) -> dict:
    """Plain-dict snapshot of an ``UpdateWorkflow`` for audit trails."""
    return {field: getattr(workflow, field) for field in _TRACKED_FIELDS}


def _workflow_matches(workflow: UpdateWorkflow, desired: WorkflowYAML) -> bool:
    """Return ``True`` when *workflow* already matches *desired*."""
    for field in _TRACKED_FIELDS:
        if getattr(workflow, field) != getattr(desired, field):
            return False
    return True


def _validate_action(desired: WorkflowYAML) -> str | None:
    """Validate ``action_key`` + per-action params. Returns error string or None.

    Mirrors the checks in ``PUT /groups/{id}/workflow`` so YAML and the UI
    enforce the same invariants. Imported lazily to avoid a circular
    import at module load time (the action registry pulls in pack code
    which transitively imports gitops bits).
    """
    from app.actions.registry import ACTION_REGISTRY

    if desired.action_key not in ACTION_REGISTRY:
        return f"Unknown action_key: {desired.action_key!r}"

    if desired.action_key == "linux-os-upgrade":
        params = desired.action_parameters or {}
        missing = [k for k in ("current_version", "next_version") if not params.get(k)]
        if missing:
            return f"linux-os-upgrade requires action_parameters: {missing}"

    return None


async def import_workflow(
    group: HostGroup,
    parsed: LabDogGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import update-workflow config from *parsed* YAML into *group*.

    Singleton **leave-alone** semantics: if ``parsed.workflow`` is ``None``
    (key absent or explicitly ``null``), the existing ``UpdateWorkflow`` row
    is left untouched and no audit event is emitted. Only when a non-null
    ``workflow:`` section is present does this handler compare against the
    DB and upsert on difference.

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

    if parsed.workflow is None:
        logger.debug("Group %d: workflow section absent/null — leaving DB state alone", group_id)
        return ModuleImportResult(
            module="workflow",
            added=0,
            removed=0,
            unchanged=0,
            changed=False,
        )

    desired = parsed.workflow

    error = _validate_action(desired)
    if error is not None:
        return ModuleImportResult(module="workflow", error_message=error)

    result = await db.execute(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id))
    existing: UpdateWorkflow | None = result.scalar_one_or_none()

    if existing is not None and _workflow_matches(existing, desired):
        logger.info(
            "GitOps workflow import for group %d: unchanged (SHA: %s)",
            group_id,
            commit_sha[:8],
        )
        return ModuleImportResult(
            module="workflow",
            added=0,
            removed=0,
            unchanged=1,
            changed=False,
        )

    before_state: dict | None = None

    if existing is None:
        workflow = UpdateWorkflow(group_id=group_id)
        for field in _TRACKED_FIELDS:
            setattr(workflow, field, getattr(desired, field))
        db.add(workflow)
        added = 1
        removed = 0
    else:
        before_state = {"workflow": _workflow_snapshot(existing)}
        for field in _TRACKED_FIELDS:
            setattr(existing, field, getattr(desired, field))
        workflow = existing
        added = 0
        removed = 0

    await db.flush()

    after_state = {
        "workflow": _workflow_snapshot(workflow),
        "commit_sha": commit_sha,
        "file_path": group.gitops_file_path,
    }

    await log_action(
        db=db,
        action="gitops.import.workflow",
        entity_type="update_workflow",
        entity_id=workflow.id,
        before_state=before_state,
        after_state=after_state,
    )

    logger.info(
        "GitOps workflow import for group %d: %s (SHA: %s)",
        group_id,
        "created" if existing is None else "updated",
        commit_sha[:8],
    )

    return ModuleImportResult(
        module="workflow",
        added=added,
        removed=removed,
        unchanged=0,
        changed=True,
    )
