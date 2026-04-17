"""
Authentication endpoint tests for Barricade.

Tests cover:
- User registration (POST /api/auth/register)
- JWT login with cookie (POST /api/auth/jwt/login)
- Current user retrieval (GET /api/users/me)
- Login error handling
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi_users.password import PasswordHelper

from app.models.user import User as UserModel

pytestmark = pytest.mark.integration


def _mock_session_with_count(count_value):
    """Create a mock AsyncSessionLocal context that returns the given user count."""
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = count_value
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = AsyncMock(return_value=None)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


class TestAuth:
    """Test suite for authentication endpoints."""

    async def test_register_user(self, client):
        """Test user registration with valid credentials."""
        email = f"test_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!Secure"

        # Mock the separate session used for user count check (returns 0 users)
        mock_session_factory = MagicMock(side_effect=lambda: _mock_session_with_count(0))
        with (
            patch(
                "app.api.auth_setup.AsyncSessionLocal",
                return_value=_mock_session_with_count(0),
            ),
            patch(
                "app.auth.users.AsyncSessionLocal",
                mock_session_factory,
            ),
        ):
            resp = await client.post(
                "/api/auth/register",
                json={"email": email, "password": password},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == email
        assert "id" in data

    async def test_login_sets_cookie(self, client, db):
        """Test that login sets the barricade_auth cookie."""
        email = f"test_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!Secure"

        # Create user directly in DB
        ph = PasswordHelper()
        user = UserModel(
            email=email,
            hashed_password=ph.hash(password),
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        # Login with form data (FastAPI-Users requirement)
        resp = await client.post(
            "/api/auth/jwt/login",
            data={"username": email, "password": password},
        )

        assert resp.status_code in (200, 204)
        # Check that the cookie is set in the response
        assert "barricade_auth" in resp.cookies

    async def test_get_current_user(self, superuser_client):
        """Test retrieving current user info via GET /api/users/me."""
        resp = await superuser_client.get("/api/users/me")

        assert resp.status_code == 200
        data = resp.json()
        assert "email" in data
        assert "id" in data
        assert data["is_superuser"] is True

    async def test_login_wrong_password(self, client, db):
        """Test login fails with incorrect password."""
        email = f"test_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!Secure"

        # Create user directly in DB
        ph = PasswordHelper()
        user = UserModel(
            email=email,
            hashed_password=ph.hash(password),
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        # Attempt login with wrong password
        resp = await client.post(
            "/api/auth/jwt/login",
            data={"username": email, "password": "WrongPassword123!"},
        )

        assert resp.status_code in (400, 401, 403)
