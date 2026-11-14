"""BUG-140: the last active superuser cannot be demoted, deactivated or deleted.

``update_user`` refused demoting the last superuser but not deactivating
them, and counted inactive superusers as others. The fastapi-users router
at ``/api/users`` also offered ``PATCH`` and ``DELETE /api/users/{id}``
with neither guard.
"""

import uuid

import pytest
from fastapi_users.password import PasswordHelper

from app.models.user import User

pytestmark = pytest.mark.integration


async def _me(client) -> int:
    resp = await client.get("/api/users/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _superuser(db, *, active: bool) -> User:
    user = User(
        email=f"su_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=PasswordHelper().hash("TestPass1!"),
        is_active=active,
        is_superuser=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


async def test_the_last_superuser_cannot_be_deactivated(superuser_client, db):
    me = await _me(superuser_client)

    resp = await superuser_client.patch(f"/api/admin/users/{me}", json={"is_active": False})

    # Refused twice over since roles: nobody disables themselves, and the
    # last active admin is never disabled.
    assert resp.status_code == 400
    assert "yourself" in resp.json()["detail"]


async def test_an_inactive_superuser_does_not_count_as_another(superuser_client, db):
    me = await _me(superuser_client)
    await _superuser(db, active=False)

    resp = await superuser_client.patch(f"/api/admin/users/{me}", json={"is_superuser": False})

    assert resp.status_code == 400


async def test_with_another_active_superuser_it_is_allowed(superuser_client, db):
    other = await _superuser(db, active=True)

    resp = await superuser_client.patch(f"/api/admin/users/{other.id}", json={"is_active": False})

    assert resp.status_code == 200, resp.text


async def test_the_fastapi_users_id_routes_are_gone(superuser_client, db):
    me = await _me(superuser_client)

    got = await superuser_client.get(f"/api/users/{me}")
    # Not a user: a 404 without the built frontend, the SPA's index.html
    # with it.
    if got.headers.get("content-type", "").startswith("application/json"):
        assert got.status_code == 404
    else:
        assert "email" not in got.text
    assert (
        await superuser_client.patch(f"/api/users/{me}", json={"is_active": False})
    ).status_code in (404, 405)
    assert (await superuser_client.delete(f"/api/users/{me}")).status_code in (404, 405)
    # /me still works.
    assert (await superuser_client.get("/api/users/me")).status_code == 200
