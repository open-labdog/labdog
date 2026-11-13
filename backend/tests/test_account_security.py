"""Changing your own password needs the old one; user admin is audited.

Two gaps found while planning local accounts, fixed on their own so they
can be backported to older release lines without the role work:

* ``PATCH /api/users/me`` accepted a new password with no proof of the
  old one, so a copied session cookie was enough to take the account
  over and lock its owner out.
* ``/api/admin/users`` created, edited, deleted and reset accounts
  without writing a single ``AuditLog`` row.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.user import User

pytestmark = pytest.mark.integration

FIXTURE_PASSWORD = "TestPass1!"
NEW_PASSWORD = "a-perfectly-good-passphrase"


class TestSelfServicePasswordChange:
    async def test_without_the_current_password_it_is_refused(self, regular_user_client):
        resp = await regular_user_client.patch("/api/users/me", json={"password": NEW_PASSWORD})
        assert resp.status_code == 400, resp.text
        assert "Current password" in resp.text

    async def test_with_a_wrong_current_password_it_is_refused(self, regular_user_client):
        resp = await regular_user_client.patch(
            "/api/users/me",
            json={"current_password": "not-the-password", "password": NEW_PASSWORD},
        )
        assert resp.status_code == 400, resp.text

    async def test_with_the_current_password_it_works(self, regular_user_client):
        resp = await regular_user_client.patch(
            "/api/users/me",
            json={"current_password": FIXTURE_PASSWORD, "password": NEW_PASSWORD},
        )
        assert resp.status_code == 200, resp.text

    async def test_the_current_password_is_not_echoed_back(self, regular_user_client):
        resp = await regular_user_client.patch(
            "/api/users/me",
            json={"current_password": FIXTURE_PASSWORD, "password": NEW_PASSWORD},
        )
        assert "current_password" not in resp.json()

    async def test_other_fields_need_no_current_password(self, regular_user_client):
        resp = await regular_user_client.patch(
            "/api/users/me", json={"email": f"moved_{uuid.uuid4().hex[:8]}@test.com"}
        )
        assert resp.status_code == 200, resp.text

    async def test_an_admin_reset_needs_no_current_password(self, superuser_client, db):
        target = User(
            email=f"target_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password="x",
            is_active=True,
            is_superuser=False,
            role="operator",
            is_verified=True,
        )
        db.add(target)
        await db.flush()
        resp = await superuser_client.post(
            f"/api/admin/users/{target.id}/reset-password", json={"password": NEW_PASSWORD}
        )
        assert resp.status_code == 200, resp.text


async def _audit_rows(db, user_id: int) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == "user", AuditLog.entity_id == user_id)
        .order_by(AuditLog.id)
    )
    return list(result.scalars())


class TestUserAdminIsAudited:
    async def _create(self, client) -> int:
        resp = await client.post(
            "/api/admin/users",
            json={"email": f"new_{uuid.uuid4().hex[:8]}@test.com", "password": NEW_PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def test_create(self, superuser_client, db):
        user_id = await self._create(superuser_client)
        rows = await _audit_rows(db, user_id)
        assert [r.action for r in rows] == ["create"]
        assert rows[0].user_id is not None
        assert "hashed_password" not in rows[0].after_state

    async def test_update_records_before_and_after(self, superuser_client, db):
        user_id = await self._create(superuser_client)
        resp = await superuser_client.patch(
            f"/api/admin/users/{user_id}", json={"is_active": False}
        )
        assert resp.status_code == 200, resp.text
        update = (await _audit_rows(db, user_id))[-1]
        assert update.action == "update"
        assert update.before_state["is_active"] is True
        assert update.after_state["is_active"] is False

    async def test_reset_password_records_no_secret(self, superuser_client, db):
        user_id = await self._create(superuser_client)
        resp = await superuser_client.post(
            f"/api/admin/users/{user_id}/reset-password", json={"password": NEW_PASSWORD + "2"}
        )
        assert resp.status_code == 200, resp.text
        reset = (await _audit_rows(db, user_id))[-1]
        assert reset.action == "reset_password"
        assert reset.before_state is None and reset.after_state is None

    async def test_delete_keeps_the_row_after_the_user_is_gone(self, superuser_client, db):
        user_id = await self._create(superuser_client)
        resp = await superuser_client.delete(f"/api/admin/users/{user_id}")
        assert resp.status_code == 204, resp.text
        rows = await _audit_rows(db, user_id)
        assert rows[-1].action == "delete"
        assert rows[-1].before_state["email"].startswith("new_")
