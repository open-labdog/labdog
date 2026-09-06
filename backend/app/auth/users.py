import logging
import secrets

from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users import schemas as fu_schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password_policy import PasswordPolicyError, check_password
from app.auth.token_version import VersionedJWTStrategy, bump_token_version
from app.config import settings
from app.db import get_db
from app.models.user import User

_CSRF_COOKIE_NAME = "labdog_csrf"

logger = logging.getLogger(__name__)

# ── Cookie transport — httpOnly for XSS protection ──────────────────────────
cookie_transport = CookieTransport(
    cookie_name="labdog_auth",
    cookie_max_age=settings.security.session_lifetime_seconds,
    cookie_secure=settings.security.cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
    cookie_domain=settings.security.cookie_domain or None,
)


def get_jwt_strategy() -> JWTStrategy:
    """SEC-30: the versioned strategy, so a token can be revoked.

    ``VersionedJWTStrategy`` stamps ``User.token_version`` into the
    token and checks it on every request, which is what makes logout and
    a password change revoke sessions rather than merely forget them.
    """
    return VersionedJWTStrategy(
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

    async def validate_password(
        self,
        password: str,
        user: fu_schemas.UC | User,
    ) -> None:
        """Enforce the password policy on every path through the manager.

        SEC-30. fastapi-users calls this from ``create``, from ``_update``
        (which is what ``PATCH /api/users/me`` and ``PATCH
        /api/users/{id}`` reach) and from ``reset_password``. The rule
        used to live only on the ``UserCreate`` schema, so every one of
        those paths skipped it — a user could set a one-character
        password on themselves.

        The admin endpoints in ``app/api/admin_users.py`` build ``User``
        rows directly and never come through here, so they call
        ``check_password`` themselves.
        """
        try:
            check_password(password, email=getattr(user, "email", None))
        except PasswordPolicyError as exc:
            raise InvalidPasswordException(reason=str(exc)) from exc

    async def create(
        self,
        user_create: fu_schemas.UC,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        """Create a user, atomically promoting the first one to superuser.

        The advisory lock at key 8675309 serialises concurrent first-register
        attempts across processes.  The lock is acquired on the same session
        that fastapi-users uses to INSERT the user row, so the lock, the count
        check, and the INSERT all live in a single transaction.  If anything
        raises after the lock is acquired but before the commit, the whole
        transaction rolls back and no half-created user persists.

        Callers pass ``safe=False`` so that the promotion below survives —
        fastapi-users' ``safe=True`` path routes through
        ``create_update_dict()``, which strips ``is_superuser`` /
        ``is_active`` / ``is_verified`` and would therefore also strip the
        flag this method just set.  Because ``safe=False`` otherwise honours
        those fields straight from the request body, privilege flags are
        reset from client input *first* and only then decided here.  The
        registration endpoint refuses once any user exists, so this is
        defence in depth rather than a live hole — but the request body must
        never be what decides privilege.
        """
        session = self.user_db.session
        # Neutralise anything the client put in the body before deciding.
        user_create.is_superuser = False  # type: ignore[attr-defined]
        user_create.is_verified = False  # type: ignore[attr-defined]
        # Advisory lock serialises concurrent registrations (same key as the
        # former on_after_register path and the auth_setup outer gate).
        await session.execute(text("SELECT pg_advisory_xact_lock(8675309)"))
        result = await session.execute(select(func.count()).select_from(User))
        count = result.scalar_one()
        if count == 0:
            user_create.is_superuser = True  # type: ignore[attr-defined]
            user_create.is_verified = True  # type: ignore[attr-defined]
            logger.info("First registration detected — will promote to superuser.")
        return await super().create(user_create, safe=safe, request=request)

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        """Set the double-submit CSRF cookie alongside the auth cookie."""
        if response is None:
            return
        token = secrets.token_urlsafe(settings.security.csrf_token_bytes)
        response.set_cookie(
            _CSRF_COOKIE_NAME,
            token,
            max_age=settings.security.session_lifetime_seconds,
            path="/",
            domain=settings.security.cookie_domain or None,
            secure=settings.security.cookie_secure,
            httponly=False,
            samesite="lax",
        )
        logger.debug("CSRF cookie set for user %d.", user.id)

    async def on_after_update(
        self,
        user: User,
        update_dict: dict,
        request: Request | None = None,
    ) -> None:
        """SEC-30: a password change revokes every existing session.

        Reached by ``PATCH /api/users/me`` and ``PATCH /api/users/{id}``.
        Before this, changing a password left a cookie somebody had
        already copied valid for up to ``session_lifetime_seconds`` —
        which makes "change your password" useless as a response to a
        suspected compromise, the one situation it exists for.

        This signs the user out of their current browser too. That is
        the intended behaviour and worth the friction: the alternative
        is deciding which of several indistinguishable sessions is the
        "real" one.
        """
        if "password" in update_dict and update_dict["password"]:
            await bump_token_version(user)
            logger.info("User %d changed password — existing sessions revoked.", user.id)

    async def on_after_register(self, user: User, request: Request | None = None):
        logger.info("User %d (%s) registered.", user.id, user.email)

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
