"""Keeping — and eventually removing — the agent's rollback points.

Found by running the software, not by CI: 0020 created snapshots and
nothing ever deleted them, so two accumulated on one VM inside ten
minutes of live testing and every later change would have added another.

The rules that matter are the two that pull against each other. A
snapshot must outlive the session that took it, because the whole point
is that a person can undo the agent's work after reading what it did —
so cleanup-on-success, which is what the action-run path does, is wrong
here. And it must not outlive it forever, because the datastore is
finite.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.ai.models import AIToolCall
from app.ai.snapshots import AI_SNAPSHOT_PREFIX, SnapshotTarget, snapshots_enabled
from app.models.app_setting import AppSetting
from app.settings_service import invalidate_cache
from tests.conftest import create_host, create_ssh_key


async def set_setting(db, key: str, value: str) -> None:
    """Write a setting and drop it from the in-process cache."""
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


def _session_cm(db):
    @asynccontextmanager
    async def _cm():
        yield db

    return _cm


async def _call_with_snapshot(db, session, host, *, age_days: float, name="labdog-ai-1-1700"):
    call = AIToolCall(
        session_id=session.id,
        tool_name="run_ssh_command",
        arguments={"host_id": host.id, "command": "systemctl restart nginx"},
        classification="mutating",
        target_host_id=host.id,
        status="executed",
        snapshot_name=name,
        started_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(call)
    await db.flush()
    return call


class TestTheSweep:
    async def test_an_expired_snapshot_is_deleted(self, db, make_session) -> None:
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        call = await _call_with_snapshot(db, session, host, age_days=30)
        await set_setting(db, "ai.snapshot_retention_days", "7")
        await db.commit()

        deleted: list[str] = []

        async def fake_delete(client, node, vmid, name, vm_type="qemu"):
            deleted.append(name)
            return {"success": True}

        async def fake_target(_db, host_id):
            return SnapshotTarget(client=object(), pve_node="pve", vmid=101, vm_type="qemu")

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.ai.snapshots.resolve_target", fake_target),
            patch("app.workflows.steps.cleanup.delete_snapshot", fake_delete),
        ):
            result = await _prune_ai_snapshots()

        await db.refresh(call)
        assert result["deleted"] == 1
        assert deleted == [call.snapshot_name]
        assert call.snapshot_pruned_at is not None

    async def test_the_name_survives_the_deletion(self, db, make_session) -> None:
        """ "There was a rollback point and it expired" is a different thing
        for a reader to know than "there never was one"."""
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        call = await _call_with_snapshot(db, session, host, age_days=30)
        await set_setting(db, "ai.snapshot_retention_days", "7")
        await db.commit()

        async def fake_delete(*a, **kw):
            return {"success": True}

        async def fake_target(_db, host_id):
            return SnapshotTarget(client=object(), pve_node="pve", vmid=101, vm_type="qemu")

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.ai.snapshots.resolve_target", fake_target),
            patch("app.workflows.steps.cleanup.delete_snapshot", fake_delete),
        ):
            await _prune_ai_snapshots()

        await db.refresh(call)
        assert call.snapshot_name is not None

    async def test_a_recent_snapshot_is_kept(self, db, make_session) -> None:
        """The session succeeding is not a reason to delete it. That is the
        whole difference from the action-run path."""
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        call = await _call_with_snapshot(db, session, host, age_days=1)
        await set_setting(db, "ai.snapshot_retention_days", "7")
        await db.commit()

        async def boom(*a, **kw):  # pragma: no cover - must not be reached
            raise AssertionError("deleted a snapshot inside the retention window")

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.workflows.steps.cleanup.delete_snapshot", boom),
        ):
            result = await _prune_ai_snapshots()

        await db.refresh(call)
        assert result["deleted"] == 0
        assert call.snapshot_pruned_at is None

    async def test_zero_days_keeps_forever(self, db, make_session) -> None:
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        await _call_with_snapshot(db, session, host, age_days=3650)
        await set_setting(db, "ai.snapshot_retention_days", "0")
        await db.commit()

        async def boom(*a, **kw):  # pragma: no cover - must not be reached
            raise AssertionError("swept with retention disabled")

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.workflows.steps.cleanup.delete_snapshot", boom),
        ):
            result = await _prune_ai_snapshots()

        assert result == {"deleted": 0, "failed": 0, "retention_days": 0}

    async def test_a_failed_delete_is_retried_next_sweep(self, db, make_session) -> None:
        """Left unmarked deliberately. Forgetting a rollback point that is
        still occupying the datastore is the worse of the two failures."""
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        call = await _call_with_snapshot(db, session, host, age_days=30)
        await set_setting(db, "ai.snapshot_retention_days", "7")
        await db.commit()

        async def fake_target(_db, host_id):
            return SnapshotTarget(client=object(), pve_node="pve", vmid=101, vm_type="qemu")

        async def fails(*a, **kw):
            raise RuntimeError("proxmox unreachable")

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.ai.snapshots.resolve_target", fake_target),
            patch("app.workflows.steps.cleanup.delete_snapshot", fails),
        ):
            result = await _prune_ai_snapshots()

        await db.refresh(call)
        assert result["failed"] == 1
        assert call.snapshot_pruned_at is None, "must be retried, not forgotten"

    async def test_a_host_that_lost_its_mapping_is_not_retried_forever(
        self, db, make_session
    ) -> None:
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        call = await _call_with_snapshot(db, session, host, age_days=30)
        await set_setting(db, "ai.snapshot_retention_days", "7")
        await db.commit()

        async def no_mapping(_db, host_id):
            return None

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.ai.snapshots.resolve_target", no_mapping),
        ):
            await _prune_ai_snapshots()

        await db.refresh(call)
        assert call.snapshot_pruned_at is not None

    async def test_reads_are_never_swept(self, db, make_session) -> None:
        """A read has no snapshot. Selecting it at all would be a bug that
        only shows up as wasted Proxmox calls."""
        from app.tasks.ai_snapshots import _prune_ai_snapshots

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(target_host_ids=[host.id])
        db.add(
            AIToolCall(
                session_id=session.id,
                tool_name="run_ssh_command",
                classification="read_only",
                target_host_id=host.id,
                status="executed",
                started_at=datetime.now(UTC) - timedelta(days=90),
            )
        )
        await set_setting(db, "ai.snapshot_retention_days", "7")
        await db.commit()

        async def boom(*a, **kw):  # pragma: no cover - must not be reached
            raise AssertionError("swept a call that never had a snapshot")

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.workflows.steps.cleanup.delete_snapshot", boom),
        ):
            result = await _prune_ai_snapshots()

        assert result["deleted"] == 0


class TestTheBypass:
    @pytest.mark.parametrize(
        ("setting", "skip", "expected"),
        [
            ("1", False, True),
            ("1", True, False),
            ("0", False, False),
            ("0", True, False),
        ],
    )
    async def test_both_must_agree(self, db, setting: str, skip: bool, expected: bool) -> None:
        """Composition is by agreement, not override. A session cannot
        re-enable snapshots an operator turned off instance-wide."""
        await set_setting(db, "ai.snapshot_before_mutating", setting)
        assert await snapshots_enabled(db, skip=skip) is expected

    async def test_a_session_can_opt_out_at_creation(
        self, superuser_client, db, ai_provider
    ) -> None:
        from app.ai.models import AISession

        # The kill switch defaults closed, so a session cannot be created
        # through the API without it. Every other test in this file builds
        # sessions directly and never meets the gate.
        await set_setting(db, "ai.enabled", "1")
        await db.commit()

        with patch("app.tasks.celery_app.send_task"):
            resp = await superuser_client.post(
                "/api/ai/sessions",
                json={
                    "mission": "Fix nginx.",
                    "autonomy_level": "full_auto",
                    "provider_id": ai_provider.id,
                    "skip_snapshots": True,
                },
            )

        assert resp.status_code == 201
        session = await db.get(AISession, resp.json()["id"])
        assert session.skip_snapshots is True

    async def test_it_defaults_to_taking_them(self, superuser_client, db, ai_provider) -> None:
        await set_setting(db, "ai.enabled", "1")
        await db.commit()

        with patch("app.tasks.celery_app.send_task"):
            resp = await superuser_client.post(
                "/api/ai/sessions",
                json={"mission": "Look around.", "provider_id": ai_provider.id},
            )
        assert resp.status_code == 201
        assert resp.json()["skip_snapshots"] is False


class TestNaming:
    def test_ai_snapshots_are_distinguishable(self) -> None:
        """An action run deletes its own snapshot on success; an agent's is
        kept for a retention window. An operator clearing space by hand has
        to be able to tell which is which."""
        assert AI_SNAPSHOT_PREFIX == "labdog-ai"
        assert not AI_SNAPSHOT_PREFIX == "labdog"

    async def test_the_prefix_reaches_the_snapshot_name(self, db, make_session) -> None:
        from app.ai.snapshots import snapshot_before_change

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        await set_setting(db, "ai.snapshot_before_mutating", "1")
        await db.commit()

        seen: dict = {}

        async def fake_create(client, node, vmid, run_id, vm_type="qemu", **kw):
            seen.update(kw)
            return f"{kw['name_prefix']}-{run_id}-1700"

        async def fake_target(_db, host_id):
            return SnapshotTarget(client=object(), pve_node="pve", vmid=101, vm_type="qemu")

        with (
            patch("app.ai.snapshots.resolve_target", fake_target),
            patch("app.workflows.steps.snapshot.create_snapshot", fake_create),
        ):
            name = await snapshot_before_change(db, host_id=host.id, session_id=session.id)

        assert seen["name_prefix"] == AI_SNAPSHOT_PREFIX
        assert name.startswith("labdog-ai-")


class TestTheApprovalCardTellsTheTruth:
    async def test_no_vm_mapping_means_no_rollback_point(
        self, superuser_client, db, make_session
    ) -> None:
        """The operator is authorising a change. Whether it can be undone
        is part of what they are deciding, so it cannot be left implicit."""
        from app.ai import approvals
        from app.ai.safety import classify_command

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="approval", target_host_ids=[host.id])
        await approvals.park(
            db,
            session,
            tool_name="run_ssh_command",
            arguments={"host_id": host.id, "command": "systemctl restart nginx"},
            verdict=classify_command("systemctl restart nginx"),
        )
        await set_setting(db, "ai.snapshot_before_mutating", "1")
        await db.commit()

        resp = await superuser_client.get("/api/ai/approvals")
        assert resp.json()[0]["snapshot_expected"] is False

    async def test_an_opted_out_session_says_so(self, superuser_client, db, make_session) -> None:
        from app.ai import approvals
        from app.ai.safety import classify_command

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="approval", target_host_ids=[host.id])
        session.skip_snapshots = True
        await approvals.park(
            db,
            session,
            tool_name="run_ssh_command",
            arguments={"host_id": host.id, "command": "systemctl restart nginx"},
            verdict=classify_command("systemctl restart nginx"),
        )
        await db.commit()

        resp = await superuser_client.get("/api/ai/approvals")
        assert resp.json()[0]["snapshot_expected"] is False


class TestDeletingASessionWithSnapshots:
    async def test_the_names_survive_in_the_audit_trail(
        self, superuser_client, db, make_session
    ) -> None:
        """The tool-call rows cascade away, taking the only thing the sweep
        works from. The snapshots are still on the hypervisor, so somebody
        has to be able to find out that they exist."""
        from app.models.audit_log import AuditLog

        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        session = await make_session(autonomy_level="full_auto", target_host_ids=[host.id])
        await _call_with_snapshot(db, session, host, age_days=0, name="labdog-ai-9-1700")
        session.status = "succeeded"
        await db.commit()

        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")
        assert resp.status_code == 204

        rows = await db.execute(select(AuditLog).where(AuditLog.action == "ai_session_deleted"))
        entry = rows.scalars().all()[-1]
        assert entry.before_state["snapshots_left_behind"] == ["labdog-ai-9-1700"]


def test_the_sweep_is_scheduled() -> None:
    """A retention policy nothing runs is a comment."""
    import app.tasks.ai_snapshots  # noqa: F401
    from app.tasks import celery_app

    assert "app.tasks.ai_snapshots.prune_ai_snapshots" in celery_app.tasks
