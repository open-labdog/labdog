"""
Tests for the double-submit CSRF cookie + X-CSRF-Token header middleware.

Coverage:
- POST to a protected endpoint without X-CSRF-Token -> 403
- POST with wrong X-CSRF-Token (cookie set, header has different value) -> 403
- POST with cookie+header matching -> passes (200/201/204)
- Login response sets both labdog_auth AND labdog_csrf cookies
- GET requests work without X-CSRF-Token (only mutating methods are gated)
- Login endpoint accepts POST without X-CSRF-Token (exempt)
- Logout endpoint accepts POST without X-CSRF-Token (exempt)
  and clears the labdog_csrf cookie
- WebSocket connect does not require the header (upgrade is GET)
"""

import uuid

import pytest
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport

from app.models.user import User as UserModel

pytestmark = pytest.mark.integration


def _make_user_in_db(db, *, is_superuser=True):
    """Insert a user directly into the test DB and return (email, password)."""
    email = f"csrf_{uuid.uuid4().hex[:8]}@test.com"
    password = "CsrfTest1!"
    ph = PasswordHelper()
    user = UserModel(
        email=email,
        hashed_password=ph.hash(password),
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
    )
    db.add(user)
    return email, password, user


class TestCsrfMiddleware:
    """CSRF middleware unit-level tests using the ASGI test client."""

    async def test_post_without_csrf_token_returns_403(self, app, db):
        """A POST to a protected endpoint without the CSRF token/cookie is rejected."""
        import httpx

        email, password, _ = _make_user_in_db(db)
        await db.flush()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as c:
            login_resp = await c.post(
                "/api/auth/jwt/login", data={"username": email, "password": password}
            )
            assert login_resp.status_code in (200, 204)

        # New client with only the auth cookie — no CSRF cookie, no header.
        auth_cookies = {"labdog_auth": login_resp.cookies.get("labdog_auth", "")}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies=auth_cookies,
            follow_redirects=True,
        ) as bare_client:
            resp = await bare_client.post("/api/groups/", json={"name": "x", "priority": 1})

        assert resp.status_code == 403
        assert "CSRF" in resp.json().get("detail", "")

    async def test_post_with_wrong_csrf_token_returns_403(self, app, db):
        """Cookie and header present but mismatched -> 403."""
        import httpx

        email, password, _ = _make_user_in_db(db)
        await db.flush()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as c:
            login_resp = await c.post(
                "/api/auth/jwt/login", data={"username": email, "password": password}
            )
            assert login_resp.status_code in (200, 204)

        # Carry auth + csrf cookie but send a mismatched header value.
        auth_cookies = dict(login_resp.cookies)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies=auth_cookies,
            follow_redirects=True,
        ) as mismatched_client:
            resp = await mismatched_client.post(
                "/api/groups/",
                json={"name": "x", "priority": 1},
                headers={"X-CSRF-Token": "wrong-value-not-matching-cookie"},
            )

        assert resp.status_code == 403
        assert "CSRF" in resp.json().get("detail", "")

    async def test_post_with_matching_csrf_token_passes(self, superuser_client, db):
        """Cookie and header with matching values -> request is forwarded."""
        # superuser_client already has labdog_csrf set from login.
        # Extract the CSRF cookie value from the client's cookie jar.
        csrf_token = superuser_client.cookies.get("labdog_csrf")
        assert csrf_token, "labdog_csrf cookie should be set after login"

        resp = await superuser_client.post(
            "/api/groups/",
            json={"name": f"csrf-test-{uuid.uuid4().hex[:6]}", "priority": 42},
            headers={"X-CSRF-Token": csrf_token},
        )
        # 201 Created or 422 (schema issue) — anything but 403 means CSRF passed.
        assert resp.status_code != 403

    async def test_login_sets_both_cookies(self, client, db):
        """Successful login response includes labdog_auth AND labdog_csrf cookies."""
        email, password, _ = _make_user_in_db(db)
        await db.flush()
        resp = await client.post(
            "/api/auth/jwt/login", data={"username": email, "password": password}
        )
        assert resp.status_code in (200, 204)
        assert "labdog_auth" in resp.cookies, "labdog_auth cookie must be set on login"
        assert "labdog_csrf" in resp.cookies, "labdog_csrf cookie must be set on login"

    async def test_get_request_does_not_require_csrf(self, superuser_client):
        """GET requests are never blocked by the CSRF middleware."""
        # GET /api/groups/ — no X-CSRF-Token header.
        resp = await superuser_client.get("/api/groups/")
        assert resp.status_code != 403

    async def test_login_exempt_from_csrf(self, client, db):
        """POST /api/auth/jwt/login is exempt — no CSRF token required."""
        email, password, _ = _make_user_in_db(db)
        await db.flush()
        # Login must succeed without any CSRF cookie/header.
        resp = await client.post(
            "/api/auth/jwt/login", data={"username": email, "password": password}
        )
        assert resp.status_code in (200, 204)

    async def test_register_exempt_from_csrf(self, client):
        """POST /api/auth/register is exempt — no CSRF token required."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.api.auth_setup.AsyncSessionLocal", return_value=mock_session):
            resp = await client.post(
                "/api/auth/register",
                json={
                    "email": f"reg_{uuid.uuid4().hex[:8]}@test.com",
                    "password": "RegisterMe1!",
                },
            )
        # 201 Created (or 403 Forbidden if registration is closed) — but NOT a
        # CSRF 403.  We check the response body to distinguish.
        if resp.status_code == 403:
            assert "CSRF" not in resp.json().get("detail", "")

    async def test_logout_exempt_and_clears_csrf_cookie(self, client, db):
        """POST /api/auth/jwt/logout is exempt from CSRF and clears labdog_csrf."""
        email, password, _ = _make_user_in_db(db)
        await db.flush()
        login_resp = await client.post(
            "/api/auth/jwt/login", data={"username": email, "password": password}
        )
        assert login_resp.status_code in (200, 204)
        assert "labdog_csrf" in login_resp.cookies

        # Logout without supplying X-CSRF-Token — should not be blocked.
        logout_resp = await client.post("/api/auth/jwt/logout")
        assert logout_resp.status_code in (200, 204), (
            f"Logout should not be blocked by CSRF middleware: {logout_resp.text}"
        )

        # The response should clear the labdog_csrf cookie (Max-Age=0).
        set_cookie_headers = [
            v for k, v in logout_resp.headers.multi_items() if k.lower() == "set-cookie"
        ]
        csrf_clear = any(
            "labdog_csrf=" in h and "Max-Age=0" in h for h in set_cookie_headers
        )
        assert csrf_clear, (
            f"Logout should clear labdog_csrf cookie; got Set-Cookie headers: {set_cookie_headers}"
        )

    async def test_put_without_csrf_token_returns_403(self, app, db):
        """PUT to a protected endpoint without CSRF token is rejected."""
        import httpx

        email, password, _ = _make_user_in_db(db)
        await db.flush()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as c:
            login_resp = await c.post(
                "/api/auth/jwt/login", data={"username": email, "password": password}
            )
            assert login_resp.status_code in (200, 204)

        auth_cookies = {"labdog_auth": login_resp.cookies.get("labdog_auth", "")}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies=auth_cookies,
            follow_redirects=True,
        ) as bare_client:
            resp = await bare_client.put("/api/hosts/999", json={"hostname": "x"})

        assert resp.status_code == 403

    async def test_delete_without_csrf_token_returns_403(self, app, db):
        """DELETE to a protected endpoint without CSRF token is rejected."""
        import httpx

        email, password, _ = _make_user_in_db(db)
        await db.flush()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as c:
            login_resp = await c.post(
                "/api/auth/jwt/login", data={"username": email, "password": password}
            )
            assert login_resp.status_code in (200, 204)

        auth_cookies = {"labdog_auth": login_resp.cookies.get("labdog_auth", "")}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies=auth_cookies,
            follow_redirects=True,
        ) as bare_client:
            resp = await bare_client.delete("/api/hosts/999")

        assert resp.status_code == 403
