"""
Authentication endpoint tests for Barricade.

Tests cover:
- User registration (POST /api/auth/register)
- JWT login with cookie (POST /api/auth/jwt/login)
- Current user retrieval (GET /api/users/me)
- Login error handling
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


class TestAuth:
    """Test suite for authentication endpoints."""

    async def test_register_user(self, client):
        """Test user registration with valid credentials."""
        email = f"test_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!"

        resp = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == email
        assert "id" in data

    async def test_login_sets_cookie(self, client):
        """Test that login sets the barricade_auth cookie."""
        email = f"test_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!"

        # Register user
        resp = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201

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

    async def test_login_wrong_password(self, client):
        """Test login fails with incorrect password."""
        email = f"test_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!"

        # Register user
        resp = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201

        # Attempt login with wrong password
        resp = await client.post(
            "/api/auth/jwt/login",
            data={"username": email, "password": "WrongPassword123!"},
        )

        assert resp.status_code in (400, 401, 403)
