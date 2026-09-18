from fastapi_users import schemas
from pydantic import field_validator

from app.auth.password_policy import check_password


class UserRead(schemas.BaseUser[int]):
    pass


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
    """
