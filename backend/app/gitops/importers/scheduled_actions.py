"""Per-group ``scheduled_actions:`` GitOps import handler.

Replaces the legacy ``workflow:`` singleton importer. Multiple
``ScheduledAction`` rows per group are supported now (one per
action_key); the YAML is a list and each entry is upserted by the
composite key ``(target_kind='group', target_id=<group.id>,
action_key)``.

Semantics — list-shaped, **leave-alone-on-absence**:

- Section absent ⇒ no-op. Existing rows are left untouched. (This is
  *different* from the firewall/services list-shape semantics, which
  wipe-and-replace on absence. The reason: deleting a scheduled
  action is destructive — if you want it gone, remove the entry from
  the YAML; if the whole file is unmanaged, disable GitOps on the
  group rather than have all schedules silently vanish.)
- Section present (even ``[]``) ⇒ delete-and-replace among rows where
  ``target_kind='group' AND target_id=group.id``. An empty list
  removes every schedule for the group.

Each entry is validated:

- ``action_key`` must be in ``ACTION_REGISTRY``.
- ``action.supports_group`` must be True (group-bound entries can't
  schedule a host-only action).
- ``parameters`` validates against the action's manifest schema.
- ``schedule_cron`` is checked syntactically by ``ScheduledActionYAML``.

Audit emission per change (``scheduled_action.created`` /
``scheduled_action.updated`` / ``scheduled_action.deleted``).
"""

from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY
from app.actions.validation import build_param_model
from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import LabDogGroupYAML, ScheduledActionYAML
from app.models.host_group import HostGroup
from app.models.scheduled_action import ScheduledAction

logger = logging.getLogger(__name__)


_TRACKED_FIELDS = (
    "enabled",
    "schedule_cron",
    "parameters",
    "batch_size",
    "snapshot_enabled",
    "verify_enabled",
    "auto_rollback",
)


def _row_snapshot(sa: ScheduledAction) -> dict:
    return {f: getattr(sa, f) for f in _TRACKED_FIELDS} | {"action_key": sa.action_key}


def _entry_to_dict(entry: ScheduledActionYAML) -> dict:
    return {
        "enabled": entry.enabled,
        "schedule_cron": entry.schedule_cron,
        "parameters": entry.parameters,
        "batch_size": entry.batch_size,
        "snapshot_enabled": entry.snapshot_enabled,
        "verify_enabled": entry.verify_enabled,
        "auto_rollback": entry.auto_rollback,
    }


def _entries_match(sa: ScheduledAction, entry: ScheduledActionYAML) -> bool:
    return all(getattr(sa, f) == getattr(entry, f) for f in _TRACKED_FIELDS)


def _validate_entry(entry: ScheduledActionYAML) -> str | None:
    """Return a human-readable error or ``None`` for OK."""
    action = ACTION_REGISTRY.get(entry.action_key)
    if action is None:
        return f"Unknown action_key: {entry.action_key!r}"
    if not action.supports_group:
        return (
            f"Action {entry.action_key!r} does not support group runs and "
            "cannot be bound to a group via GitOps"
        )
    try:
        build_param_model(action).model_validate(entry.parameters)
    except ValidationError as exc:
        return f"Invalid parameters for {entry.action_key!r}: {exc.errors()}"
    return None


async def import_scheduled_actions(
    group: HostGroup,
    parsed: LabDogGroupYAML,
    commit_sha: str | None,
    db: AsyncSession,
) -> ModuleImportResult:
    """Diff-and-upsert the ``scheduled_actions`` section for one group."""
    if parsed.scheduled_actions is None:
        return ModuleImportResult(module="scheduled_actions")

    desired = list(parsed.scheduled_actions)

    # Reject duplicates within the YAML — the same action_key twice is
    # a typo, not "two slightly different schedules" (we'd have to pick
    # one).
    seen: set[str] = set()
    duplicates = [e.action_key for e in desired if (e.action_key in seen) or seen.add(e.action_key)]
    if duplicates:
        return ModuleImportResult(
            module="scheduled_actions",
            error_message=(f"Duplicate action_key in scheduled_actions: {duplicates[0]!r}"),
        )

    # Validate every entry against the live registry up front.
    for entry in desired:
        err = _validate_entry(entry)
        if err is not None:
            return ModuleImportResult(module="scheduled_actions", error_message=err)

    existing = (
        (
            await db.execute(
                select(ScheduledAction).where(
                    ScheduledAction.target_kind == "group",
                    ScheduledAction.target_id == group.id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_key: dict[str, ScheduledAction] = {sa.action_key: sa for sa in existing}

    desired_keys = {e.action_key for e in desired}
    removed = 0
    updated = 0
    added = 0
    unchanged = 0

    # Drop any row whose key is no longer in the desired list.
    for sa in existing:
        if sa.action_key not in desired_keys:
            before = _row_snapshot(sa)
            await log_action(
                db,
                action="scheduled_action.deleted",
                entity_type="scheduled_action",
                entity_id=sa.id,
                before_state=before,
            )
            await db.delete(sa)
            removed += 1

    # Upsert each desired entry.
    for entry in desired:
        sa = by_key.get(entry.action_key)
        if sa is None:
            sa = ScheduledAction(
                target_kind="group",
                target_id=group.id,
                action_key=entry.action_key,
                **_entry_to_dict(entry),
            )
            db.add(sa)
            await db.flush()
            await log_action(
                db,
                action="scheduled_action.created",
                entity_type="scheduled_action",
                entity_id=sa.id,
                after_state=_row_snapshot(sa),
            )
            added += 1
            continue

        if _entries_match(sa, entry):
            unchanged += 1
            continue

        before = _row_snapshot(sa)
        for field in _TRACKED_FIELDS:
            setattr(sa, field, getattr(entry, field))
        await db.flush()
        await log_action(
            db,
            action="scheduled_action.updated",
            entity_type="scheduled_action",
            entity_id=sa.id,
            before_state=before,
            after_state=_row_snapshot(sa),
        )
        updated += 1

    return ModuleImportResult(
        module="scheduled_actions",
        added=added,
        removed=removed,
        unchanged=unchanged,
        changed=(added + updated + removed) > 0,
    )
