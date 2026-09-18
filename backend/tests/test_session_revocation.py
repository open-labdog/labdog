"""SEC-30: logging out, and changing a password, must revoke the session.

LabDog's auth cookie is a stateless JWT. Logout cleared it in the
browser and did nothing else, so a cookie somebody had already copied —
from a shared machine, a proxy log, an XSS payload — stayed valid for
the rest of ``session_lifetime_seconds``, 24 hours by default. Changing
a password did not help either, which makes "change your password"
useless as a response to a suspected compromise, the one situation it
exists for.

``User.token_version`` is a session generation carried in the token as a
``tv`` claim and compared on every request.

The tests below keep a copy of the cookie and replay it *after* the
revoking action, because that is the attack: the browser that was told
to forget the cookie is not the one that matters.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth.token_version import SESSION_GENERATION_CLAIM, VersionedJWTStrategy
from app.models.user import User
from tests.conftest import _CsrfAutoTransport

GOOD = "CorrectHorseBattery1!"
ANOTHER = "AnotherGoodOne1!Xy"


def _auth_cookie(client) -> str:
    """The auth cookie as an attacker would have copied it."""
    value = client.cookies.get("labdog_auth")
    assert value, "no auth cookie on the logged-in client"
    return value


async def _replay(client, cookie: str):
    """Send a request with *cookie*, bypassing the client's own jar."""
    return await client.get("/api/users/me", headers={"Cookie": f"labdog_auth={cookie}"})


class TestLogoutRevokes:
    async def test_a_copied_cookie_stops_working_after_logout(self, regular_user_client):
        stolen = _auth_cookie(regular_user_client)
        assert (await _replay(regular_user_client, stolen)).status_code == 200

        assert (await regular_user_client.post("/api/auth/jwt/logout")).status_code in (
            200,
            204,
        )

        assert (await _replay(regular_user_client, stolen)).status_code == 401

    async def test_logging_out_revokes_other_sessions_too(self, app, db, regular_user_client):
        """Someone clicking "log out" on a machine they do not trust is
        asking for exactly this."""
        stolen = _auth_cookie(regular_user_client)
        await regular_user_client.post("/api/auth/jwt/logout")
        # A second, independent replay — nothing to do with the client's
        # own cookie jar having been cleared.
        assert (await _replay(regular_user_client, stolen)).status_code == 401

    async def test_the_counter_actually_moves(self, db, regular_user_client):
        me = (await regular_user_client.get("/api/users/me")).json()
        user = (await db.execute(select(User).where(User.id == me["id"]))).scalar_one()
        before = user.token_version
        await regular_user_client.post("/api/auth/jwt/logout")
        await db.refresh(user)
        assert user.token_version == before + 1


class TestAPasswordChangeRevokes:
    async def test_a_copied_cookie_stops_working_after_a_self_service_change(
        self, regular_user_client
    ):
        stolen = _auth_cookie(regular_user_client)
        resp = await regular_user_client.patch("/api/users/me", json={"password": GOOD})
        assert resp.status_code == 200, resp.text
        assert (await _replay(regular_user_client, stolen)).status_code == 401

    async def test_an_update_that_does_not_touch_the_password_leaves_the_session_alone(
        self, regular_user_client
    ):
        """Revoking on every profile edit would train people to ignore
        being logged out."""
        cookie = _auth_cookie(regular_user_client)
        resp = await regular_user_client.patch(
            "/api/users/me", json={"email": f"moved_{uuid.uuid4().hex[:8]}@test.com"}
        )
        assert resp.status_code == 200, resp.text
        assert (await _replay(regular_user_client, cookie)).status_code == 200

    async def test_an_admin_reset_revokes_the_target_users_sessions(
        self, superuser_client, app, db
    ):
        """The compromise-response path: the attacker holds the cookie,
        an administrator resets the password. A reset that leaves the
        cookie alive only inconveniences the legitimate user."""
        from fastapi_users.password import PasswordHelper

        email = f"victim_{uuid.uuid4().hex[:8]}@test.com"
        ph = PasswordHelper()
        victim = User(
            email=email,
            hashed_password=ph.hash(GOOD),
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        db.add(victim)
        await db.flush()

        import httpx

        async with httpx.AsyncClient(
            transport=_CsrfAutoTransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as victim_client:
            login = await victim_client.post(
                "/api/auth/jwt/login", data={"username": email, "password": GOOD}
            )
            assert login.status_code in (200, 204), login.text
            stolen = victim_client.cookies.get("labdog_auth")
            assert (await _replay(victim_client, stolen)).status_code == 200

            reset = await superuser_client.post(
                f"/api/admin/users/{victim.id}/reset-password", json={"password": ANOTHER}
            )
            assert reset.status_code == 200, reset.text

            assert (await _replay(victim_client, stolen)).status_code == 401

    async def test_the_admins_own_session_survives_resetting_someone_else(
        self, superuser_client, db
    ):
        """Bumping the wrong row would log the administrator out every
        time they helped someone."""
        create = await superuser_client.post(
            "/api/admin/users",
            json={"email": f"other_{uuid.uuid4().hex[:8]}@test.com", "password": GOOD},
        )
        user_id = create.json()["id"]
        await superuser_client.post(
            f"/api/admin/users/{user_id}/reset-password", json={"password": ANOTHER}
        )
        assert (await superuser_client.get("/api/users/me")).status_code == 200


class TestTheClaimItself:
    @pytest.fixture
    def strategy(self):
        return VersionedJWTStrategy(secret="unit-test-secret", lifetime_seconds=3600)

    class _FakeUser:
        def __init__(self, id_=1, version=0):
            self.id = id_
            self.token_version = version
            self.is_active = True

    async def test_the_token_carries_the_current_generation(self, strategy):
        from fastapi_users.jwt import decode_jwt

        token = await strategy.write_token(self._FakeUser(version=7))
        data = decode_jwt(token, strategy.decode_key, strategy.token_audience)
        assert data[SESSION_GENERATION_CLAIM] == 7

    async def test_a_token_with_no_claim_is_refused(self, strategy):
        """Every session made before this column existed. Accepting them
        would leave exactly the tokens this exists to revoke working
        until they expired on their own."""
        from fastapi_users.authentication.strategy.jwt import JWTStrategy

        plain = JWTStrategy(secret="unit-test-secret", lifetime_seconds=3600)
        user = self._FakeUser()
        legacy = await plain.write_token(user)

        class _Manager:
            def parse_id(self, v):
                return int(v)

            async def get(self, _id):
                return user

        assert await strategy.read_token(legacy, _Manager()) is None

    async def test_a_stale_generation_is_refused(self, strategy):
        user = self._FakeUser(version=1)
        token = await strategy.write_token(user)

        class _Manager:
            def parse_id(self, v):
                return int(v)

            async def get(self, _id):
                return user

        assert await strategy.read_token(token, _Manager()) is not None
        user.token_version = 2
        assert await strategy.read_token(token, _Manager()) is None

    async def test_a_garbage_token_is_refused_without_raising(self, strategy):
        class _Manager:
            def parse_id(self, v):
                return int(v)

            async def get(self, _id):
                raise AssertionError("should not reach the database")

        assert await strategy.read_token("not-a-jwt", _Manager()) is None
        assert await strategy.read_token(None, _Manager()) is None


class TestLoggingBackInWorks:
    """The regression half. A revocation mechanism that also blocks the
    next legitimate login is an outage, not a fix."""

    async def test_a_fresh_login_after_logout_is_accepted(self, app, db):
        import httpx
        from fastapi_users.password import PasswordHelper

        email = f"relogin_{uuid.uuid4().hex[:8]}@test.com"
        ph = PasswordHelper()
        db.add(
            User(
                email=email,
                hashed_password=ph.hash(GOOD),
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        await db.flush()

        async with httpx.AsyncClient(
            transport=_CsrfAutoTransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as c:
            await c.post("/api/auth/jwt/login", data={"username": email, "password": GOOD})
            await c.post("/api/auth/jwt/logout")
            again = await c.post("/api/auth/jwt/login", data={"username": email, "password": GOOD})
            assert again.status_code in (200, 204), again.text
            assert (await c.get("/api/users/me")).status_code == 200

    async def test_a_login_right_after_a_password_change_works(self, app, db):
        import httpx
        from fastapi_users.password import PasswordHelper

        email = f"changed_{uuid.uuid4().hex[:8]}@test.com"
        ph = PasswordHelper()
        db.add(
            User(
                email=email,
                hashed_password=ph.hash(GOOD),
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        await db.flush()

        async with httpx.AsyncClient(
            transport=_CsrfAutoTransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as c:
            await c.post("/api/auth/jwt/login", data={"username": email, "password": GOOD})
            await c.patch("/api/users/me", json={"password": ANOTHER})
            again = await c.post(
                "/api/auth/jwt/login", data={"username": email, "password": ANOTHER}
            )
            assert again.status_code in (200, 204), again.text
            assert (await c.get("/api/users/me")).status_code == 200
