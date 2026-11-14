"""A schedule runs only while its owner may still run it (RBAC plan, decision 4)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi_users.password import PasswordHelper
from sqlalchemy import select

from app.auth.permissions import set_permission_resolver
from app.models.action_run import ActionRun
from app.models.audit_log import AuditLog
from app.models.scheduled_action import ScheduledAction
from app.models.user import User
from app.tasks.scheduled_action_schedule import _check_due_async, owner_block_reason
from tests.conftest import create_host
from tests.test_permissions import _client

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture(autouse=True)
def stub_celery_dispatch():
    with patch("celery.app.base.Celery.send_task"):
        yield


async def _owner(db, role="operator", active=True):
    user = User(
        email=f"own-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=PasswordHelper().hash("x"),
        is_active=active,
        is_verified=True,
    )
    user.role = role
    db.add(user)
    await db.flush()
    return user


async def _schedule(db, host, owner=None, required=True):
    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="* * * * *",
        enabled=True,
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
        owner_user_id=owner.id if owner else None,
        owner_required=required,
    )
    db.add(sa)
    await db.commit()
    return sa


class TestOwnerBlockReason:
    async def test_legacy_schedules_need_no_owner(self, db):
        sa = await _schedule(db, await create_host(db), owner=None, required=False)
        assert await owner_block_reason(db, sa) is None

    async def test_each_reason(self, db):
        host = await create_host(db)
        assert await owner_block_reason(db, await _schedule(db, host)) == "owner_removed"
        off = await _owner(db, active=False)
        sa = await _schedule(db, await create_host(db), off)
        assert await owner_block_reason(db, sa) == "owner_inactive"
        viewer = await _owner(db, role="viewer")
        sa = await _schedule(db, await create_host(db), viewer)
        assert await owner_block_reason(db, sa) == "owner_lacks_permission"
        operator = await _owner(db)
        sa = await _schedule(db, await create_host(db), operator)
        assert await owner_block_reason(db, sa) is None

    async def test_the_resolver_is_asked_with_the_target(self, db):
        host = await create_host(db)
        owner = await _owner(db, role=None)
        seen = []

        async def only_this_host(user, scope):
            target = await scope.resolve()
            seen.append(target)
            return frozenset({"actions.run"}) if target.id == host.id else frozenset()

        set_permission_resolver(only_this_host)
        try:
            sa = await _schedule(db, host, owner)
            assert await owner_block_reason(db, sa) is None
        finally:
            set_permission_resolver(None)
        assert seen[0].kind == "host" and seen[0].id == host.id


class TestTheTick:
    async def test_a_blocked_schedule_is_skipped_flagged_and_audited_once(self, db):
        viewer = await _owner(db, role="viewer")
        sa = await _schedule(db, await create_host(db), viewer)

        result = await _check_due_async()
        assert result["skipped_owner"] == 1 and result["dispatched"] == 0
        await db.refresh(sa)
        assert sa.owner_blocked_reason == "owner_lacks_permission"
        runs = await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa.id))
        assert runs.scalars().all() == []

        sa.last_dispatched_at = datetime.now(UTC) - timedelta(minutes=2)
        await db.commit()
        await _check_due_async()
        audits = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "scheduled_action.owner_blocked",
                AuditLog.entity_id == sa.id,
            )
        )
        assert len(audits.scalars().all()) == 1

    async def test_runs_carry_the_owner(self, db):
        operator = await _owner(db)
        sa = await _schedule(db, await create_host(db), operator)
        result = await _check_due_async()
        assert result["dispatched"] == 1
        run = (
            await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa.id))
        ).scalar_one()
        assert run.triggered_by_user_id == operator.id


class TestTheApi:
    async def test_creating_records_the_owner_and_taking_over_clears_the_flag(self, app, db):
        host = await create_host(db)
        client, me = await _client(app, db, "operator")
        try:
            resp = await client.post(
                "/api/scheduled-actions",
                json={
                    "target_kind": "host",
                    "target_id": host.id,
                    "action_key": "_builtin.collect_state",
                    "schedule_cron": "0 3 * * *",
                },
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["owner_user_id"] == me.id and body["owner_email"] == me.email

            sa = await db.get(ScheduledAction, body["id"])
            sa.owner_user_id = None
            sa.owner_blocked_reason = "owner_removed"
            await db.commit()

            took = await client.post(f"/api/scheduled-actions/{sa.id}/take-ownership")
            assert took.status_code == 200, took.text
            assert took.json()["owner_user_id"] == me.id
            assert took.json()["owner_blocked_reason"] is None
        finally:
            await client.aclose()

    async def test_a_viewer_cannot_take_ownership(self, app, db):
        sa = await _schedule(db, await create_host(db), await _owner(db))
        client, _ = await _client(app, db, "viewer")
        try:
            resp = await client.post(f"/api/scheduled-actions/{sa.id}/take-ownership")
            assert resp.status_code == 403
        finally:
            await client.aclose()
