import logging

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal, get_db
from app.models.user import User

logger = logging.getLogger(__name__)

# ── Cookie transport — httpOnly for XSS protection ──────────────────────────
cookie_transport = CookieTransport(
    cookie_name="barricade_auth",
    cookie_max_age=settings.security.session_lifetime_seconds,
    cookie_secure=settings.security.cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
    cookie_domain=settings.security.cookie_domain or None,
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.security.secret_key,
        lifetime_seconds=settings.security.session_lifetime_seconds,
    )


auth_backend = AuthenticationBackend(
    name="jwt_cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)


# ── User database dependency ─────────────────────────────────────────────────
async def get_user_db(session: AsyncSession = Depends(get_db)):
    yield SQLAlchemyUserDatabase(session, User)  # type: ignore[type-var]  # SQLAlchemy Mapped vs fastapi-users protocol


# ── User manager ─────────────────────────────────────────────────────────────
class UserManager(IntegerIDMixin, BaseUserManager[User, int]):  # type: ignore[type-var]  # SQLAlchemy Mapped vs fastapi-users protocol
    reset_password_token_secret = settings.security.secret_key
    verification_token_secret = settings.security.secret_key

    async def on_after_register(self, user: User, request: Request | None = None):
        logger.info("User %d (%s) registered.", user.id, user.email)
        async with AsyncSessionLocal() as session:
            # Advisory lock serializes with the register endpoint to prevent
            # multiple first-user superuser promotions
            await session.execute(text("SELECT pg_advisory_xact_lock(8675309)"))
            result = await session.execute(select(func.count()).select_from(User))
            count = result.scalar_one()
            if count == 1:
                # Re-fetch the user within this session to avoid cross-session
                # object sharing (the `user` arg belongs to fastapi_users' session).
                fresh = await session.get(User, user.id)
                if fresh:
                    fresh.is_superuser = True
                    fresh.is_verified = True
                    await session.commit()
                    logger.info("First user %d promoted to superuser.", user.id)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ):
        logger.info("User %d forgot password.", user.id)

    async def on_after_request_verify(self, user: User, token: str, request: Request | None = None):
        logger.info("Verification requested for user %d.", user.id)


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


# ── FastAPIUsers instance ────────────────────────────────────────────────────
fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])  # type: ignore[type-var]  # SQLAlchemy Mapped vs fastapi-users protocol

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
