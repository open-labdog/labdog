"""An audit row for a user deleted meanwhile still commits.

Background tasks log for whoever started them. The sync orchestrator
writes ``sync_completed`` with the triggering user's id when the run
ends; if that account was deleted while the sync ran, the insert broke
``fk_audit_log_user_id_users``, rolled back the finalise, and left the
job ``running`` until the sweeper failed it 30 minutes later.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit.logger import log_action
from app.models.audit_log import AuditLog
from app.models.user import User

pytestmark = pytest.mark.integration


async def test_a_missing_user_is_logged_as_nobody(db):
    await log_action(db, action="sync_completed", entity_type="host", entity_id=1, user_id=987654)
    await db.commit()
    row = (
        await db.execute(select(AuditLog).where(AuditLog.action == "sync_completed"))
    ).scalar_one()
    assert row.user_id is None


async def test_an_existing_user_is_kept(db):
    user = User(email="audited@test.com", hashed_password="x", is_active=True, is_verified=True)
    db.add(user)
    await db.flush()
    await log_action(db, action="update", entity_type="host", entity_id=1, user_id=user.id)
    await db.commit()
    row = (await db.execute(select(AuditLog).where(AuditLog.action == "update"))).scalar_one()
    assert row.user_id == user.id
