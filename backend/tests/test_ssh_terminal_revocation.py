"""SEC-44: an open terminal must not outlive its user's session."""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.api import ssh_terminal
from app.models.user import User


@pytest.fixture
def same_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch.object(ssh_terminal, "AsyncSessionLocal", new=_fake):
        yield


async def _user(db, **kw) -> User:
    user = User(
        email=f"u{id(kw)}@example.com",
        hashed_password="x",
        is_active=kw.get("is_active", True),
        is_superuser=False,
        is_verified=True,
        token_version=kw.get("token_version", 0),
    )
    db.add(user)
    await db.flush()
    return user


async def test_an_unchanged_session_stays_valid(db, same_session):
    user = await _user(db)
    assert await ssh_terminal._session_still_valid(user.id, 0) is True


async def test_deactivation_ends_it(db, same_session):
    user = await _user(db, is_active=False)
    assert await ssh_terminal._session_still_valid(user.id, 0) is False


async def test_a_new_session_generation_ends_it(db, same_session):
    """A password change or "log out everywhere" bumps token_version."""
    user = await _user(db, token_version=1)
    assert await ssh_terminal._session_still_valid(user.id, 0) is False


async def test_a_deleted_user_ends_it(db, same_session):
    assert await ssh_terminal._session_still_valid(987654, 0) is False
