"""
Tests for SEC-17: atomic first-user superuser promotion via UserManager.create.

Verifies that user creation and superuser promotion happen in the same
transaction so there is no window where the first user exists without
is_superuser=True.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.user import User as UserModel

pytestmark = pytest.mark.integration


def _mock_auth_setup_session(count_value: int):
    """Mock AsyncSessionLocal context for the auth_setup count/lock gate."""
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = count_value
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


class TestFirstUserPromotion:
    """SEC-17: atomic first-user superuser promotion."""

    async def test_first_user_is_promoted_to_superuser(self, client, db):
        """Happy path: the first registered user gets is_superuser=True atomically."""
        email = f"first_{uuid.uuid4().hex[:8]}@test.com"
        password = "TestPass1!Secure"

        # The auth_setup gate uses AsyncSessionLocal (separate session) —
        # mock it to report 0 existing users so registration is allowed.
        with patch(
            "app.api.auth_setup.AsyncSessionLocal",
            return_value=_mock_auth_setup_session(0),
        ):
            resp = await client.post(
                "/api/auth/register",
                json={"email": email, "password": password},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == email

        # Verify the user row in the DB actually has is_superuser=True.
        result = await db.execute(select(UserModel).where(UserModel.email == email))
        user = result.scalar_one()
        assert user.is_superuser is True, (
            "First registered user must be promoted to superuser atomically"
        )
        assert user.is_verified is True, "First registered user must also be marked verified"

    async def test_second_user_is_not_promoted(self, client, db):
        """A second registration must NOT get superuser status."""
        from fastapi_users.password import PasswordHelper

        # Seed an existing superuser so count > 0 from the UserManager's
        # perspective (using the savepoint session that UserManager.create sees).
        ph = PasswordHelper()
        existing = UserModel(
            email=f"existing_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=ph.hash("TestPass1!Secure"),
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        db.add(existing)
        await db.flush()

        second_email = f"second_{uuid.uuid4().hex[:8]}@test.com"

        with patch(
            "app.api.auth_setup.AsyncSessionLocal",
            return_value=_mock_auth_setup_session(0),
        ):
            resp = await client.post(
                "/api/auth/register",
                json={"email": second_email, "password": "TestPass1!Secure"},
            )

        assert resp.status_code == 201

        result = await db.execute(select(UserModel).where(UserModel.email == second_email))
        second_user = result.scalar_one()
        assert second_user.is_superuser is False, (
            "Second registered user must not be promoted to superuser"
        )

    async def test_atomicity_superuser_flag_set_before_insert(self, db):
        """is_superuser=True is baked into the INSERT, not applied after.

        The SEC-17 fix moves the promotion to happen before super().create()
        is called, so the flag is part of the INSERT statement itself.  Even
        if on_after_register subsequently raises, the user row (if it exists)
        already has is_superuser=True — there is no window where the row
        exists with is_superuser=False and the promotion has not yet happened.

        This test drives UserManager.create() directly (bypassing HTTP) so
        the simulated on_after_register failure propagates as a Python
        exception rather than an HTTP response.
        """
        from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

        from app.auth import users as users_module
        from app.auth.schemas import UserCreate

        email = f"atomic_{uuid.uuid4().hex[:8]}@test.com"

        user_db = SQLAlchemyUserDatabase(db, UserModel)
        manager = users_module.UserManager(user_db)

        async def raising_on_after_register(self_inner, user, request=None):
            raise RuntimeError("simulated post-create failure")

        user_create = UserCreate(email=email, password="TestPass1!Secure")

        with patch.object(
            users_module.UserManager,
            "on_after_register",
            raising_on_after_register,
        ):
            with pytest.raises(RuntimeError, match="simulated post-create failure"):
                await manager.create(user_create)

        # After the exception the savepoint-session still reflects the INSERT
        # (the savepoint was released by user_db.create's session.commit()).
        # The key invariant: if the user was inserted, is_superuser is True.
        result = await db.execute(select(UserModel).where(UserModel.email == email))
        user = result.scalar_one_or_none()
        assert user is not None, "User should have been inserted"
        assert user.is_superuser is True, (
            "is_superuser must be True on INSERT (set before super().create), "
            "not applied in a separate post-create step"
        )
