from datetime import datetime

from fastapi_users import schemas
from pydantic import Field, field_validator

from app.auth.password_policy import check_password


class UserRead(schemas.BaseUser[int]):
    role: str | None = None
    display_name: str | None = None
    last_login_at: datetime | None = None
    must_change_password: bool = False


class MeRead(UserRead):
    """``GET /api/users/me``: the account plus what it may do.

    ``permissions`` is the global set from ``app.auth.permissions``; the
    frontend hides controls from it. An extension that scopes permissions
    to hosts and groups adds its own field beside it.
    """

    permissions: list[str] = []


class UserCreate(schemas.BaseUserCreate):
    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Kept so registration still answers 422 with a field error.

        The policy itself lives in ``UserManager.validate_password``
        (SEC-30), which is what covers update and reset as well. This is
        the friendlier of two identical answers, not a second rule — the
        email is not available to a field validator, so the
        email-equality check happens at the manager.
        """
        check_password(v)
        return v


class UserUpdate(schemas.BaseUserUpdate):
    """No validator here on purpose.

    ``PATCH /api/users/me`` routes through
    ``BaseUserManager._update``, which calls
    ``UserManager.validate_password``. Duplicating the rule here would
    mean two places to keep in step, and the manager is the one that
    cannot be bypassed.

    ``current_password`` is required when a user changes their own
    password through ``PATCH /api/users/me``; ``UserManager.update``
    checks it and strips it before anything is written. Without it a
    session somebody copied could set a new password and lock the owner
    out, which is the one outcome revoking sessions on a password change
    exists to prevent.
    """

    current_password: str | None = None
    display_name: str | None = Field(default=None, max_length=255)
