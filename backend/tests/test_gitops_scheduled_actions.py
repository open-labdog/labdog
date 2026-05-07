"""Tests for the scheduled_actions: GitOps importer (C7).

Covers list-shaped, leave-alone-on-absence semantics; per-entry
validation (action_key in registry, supports_group, parameters
shape, cron syntax via Pydantic); add/update/delete idempotency.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.gitops.importers.scheduled_actions import import_scheduled_actions
from app.gitops.schema import LabDogGroupYAML
from app.models.scheduled_action import ScheduledAction
from tests.conftest import create_group

pytestmark = pytest.mark.integration


def _yaml(scheduled_actions: list | None = None) -> LabDogGroupYAML:
    return LabDogGroupYAML(group="g", scheduled_actions=scheduled_actions)


async def test_section_absent_leaves_db_untouched(db):
    group = await create_group(db, name="ga")
    db.add(
        ScheduledAction(
            target_kind="group",
            target_id=group.id,
            action_key="_builtin.collect_state",
            schedule_cron="* * * * *",
            enabled=True,
        )
    )
    await db.flush()

    result = await import_scheduled_actions(group, _yaml(None), None, db)
    assert result.error_message is None
    assert result.changed is False

    rows = (
        (await db.execute(select(ScheduledAction).where(ScheduledAction.target_id == group.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_empty_list_removes_all(db):
    group = await create_group(db, name="gb")
    db.add(
        ScheduledAction(
            target_kind="group",
            target_id=group.id,
            action_key="_builtin.collect_state",
            schedule_cron="* * * * *",
        )
    )
    await db.flush()

    result = await import_scheduled_actions(group, _yaml([]), None, db)
    assert result.error_message is None
    assert result.removed == 1

    rows = (
        (await db.execute(select(ScheduledAction).where(ScheduledAction.target_id == group.id)))
        .scalars()
        .all()
    )
    assert rows == []


async def test_create_new_entry(db):
    group = await create_group(db, name="gc")

    yaml = _yaml(
        [
            {
                "action_key": "_builtin.collect_state",
                "enabled": True,
                "schedule_cron": "0 * * * *",
            }
        ]
    )
    result = await import_scheduled_actions(group, yaml, None, db)
    assert result.error_message is None
    assert result.added == 1

    sa = (
        await db.execute(select(ScheduledAction).where(ScheduledAction.target_id == group.id))
    ).scalar_one()
    assert sa.action_key == "_builtin.collect_state"
    assert sa.enabled is True
    assert sa.schedule_cron == "0 * * * *"


async def test_update_existing_entry(db):
    group = await create_group(db, name="gd")
    sa = ScheduledAction(
        target_kind="group",
        target_id=group.id,
        action_key="_builtin.collect_state",
        schedule_cron="0 * * * *",
        enabled=False,
    )
    db.add(sa)
    await db.flush()

    yaml = _yaml(
        [
            {
                "action_key": "_builtin.collect_state",
                "enabled": True,
                "schedule_cron": "0 3 * * *",
            }
        ]
    )
    result = await import_scheduled_actions(group, yaml, None, db)
    assert result.error_message is None
    assert result.changed is True

    await db.refresh(sa)
    assert sa.enabled is True
    assert sa.schedule_cron == "0 3 * * *"


async def test_unchanged_entry_is_idempotent(db):
    group = await create_group(db, name="ge")
    yaml = _yaml(
        [
            {
                "action_key": "_builtin.collect_state",
                "enabled": True,
                "schedule_cron": "0 * * * *",
                "parameters": {},
                "batch_size": 1,
                "snapshot_enabled": True,
                "verify_enabled": True,
                "auto_rollback": True,
            }
        ]
    )
    first = await import_scheduled_actions(group, yaml, None, db)
    assert first.added == 1

    second = await import_scheduled_actions(group, yaml, None, db)
    assert second.error_message is None
    assert second.added == 0
    assert second.unchanged == 1
    assert second.changed is False


async def test_unknown_action_key_rejected(db):
    group = await create_group(db, name="gf")
    yaml = _yaml([{"action_key": "no-such-action", "schedule_cron": "0 * * * *"}])
    result = await import_scheduled_actions(group, yaml, None, db)
    assert result.error_message is not None
    assert "Unknown action_key" in result.error_message


async def test_host_only_action_rejected_for_group(db, monkeypatch):
    """An action with supports_group=False can't bind to a group."""
    from pathlib import Path

    from app.actions.registry import ACTION_REGISTRY
    from app.actions.types import ActionDefinition

    fake = ActionDefinition(
        key="host-only-fixture",
        name="Host-Only Fixture",
        description="",
        icon="ArrowUpFromLine",
        playbook_path=Path("/tmp/labdog-test-host-only.yml"),
        version="1.0",
        estimated_duration="1 min",
        destructive=False,
        supports_group=False,
        supports_host=True,
        supports_fleet=False,
    )
    Path("/tmp/labdog-test-host-only.yml").touch()
    monkeypatch.setitem(ACTION_REGISTRY, "host-only-fixture", fake)

    group = await create_group(db, name="gg")
    yaml = _yaml([{"action_key": "host-only-fixture", "schedule_cron": "0 * * * *"}])
    result = await import_scheduled_actions(group, yaml, None, db)
    assert result.error_message is not None
    assert "does not support group runs" in result.error_message


async def test_duplicate_action_key_rejected(db):
    group = await create_group(db, name="gh")
    yaml = _yaml(
        [
            {"action_key": "_builtin.collect_state", "schedule_cron": "0 * * * *"},
            {"action_key": "_builtin.collect_state", "schedule_cron": "0 4 * * *"},
        ]
    )
    result = await import_scheduled_actions(group, yaml, None, db)
    assert result.error_message is not None
    assert "Duplicate" in result.error_message


async def test_invalid_cron_rejected_at_schema(db):
    group = await create_group(db, name="gi")
    # Pydantic field_validator on ScheduledActionYAML rejects invalid cron
    # before this importer is even called.
    with pytest.raises(Exception) as exc:
        _yaml([{"action_key": "_builtin.collect_state", "schedule_cron": "garbage"}])
    assert "cron" in str(exc.value).lower()
    assert group.id is not None  # silence ARG001-style unused warnings
