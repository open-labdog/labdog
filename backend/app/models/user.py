from datetime import UTC, datetime

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, event
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import Base

#: Values ``users.role`` may hold. Kept here rather than imported from
#: ``app.auth.permissions`` so the model has no dependency on the auth
#: layer; ``tests/test_permissions.py`` checks the two agree.
ROLE_VALUES = ("admin", "operator", "viewer")


class User(SQLAlchemyBaseUserTable[int], Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IS NULL OR role IN ('admin', 'operator', 'viewer')", name="ck_users_role"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    #: NULL for an account that signs in only through a directory (SSO):
    #: password login refuses it (``UserManager.authenticate``).
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Mirror of ``role == "admin"``, written by :meth:`_mirror_role`.
    #: Kept so fastapi-users' own superuser checks and the
    #: ``labdog_superusers`` metric keep working. New code reads ``role``
    #: through ``app.auth.permissions``, never this.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    #: ``admin`` | ``operator`` | ``viewer``, or NULL for no direct role
    #: (an account whose rights come only from an extension, such as RBAC
    #: group bindings). NULL grants nothing in the Community edition.
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when an administrator chose the password (a new account or a
    #: reset). Until the user picks their own, every route but
    #: ``/api/users/me`` answers 403. Ignored for accounts with no password.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: SEC-30. Session generation. The auth cookie carries this value as
    #: a ``tv`` claim and every request checks it, so bumping it here
    #: invalidates every token issued before the bump. Without it the
    #: JWT is stateless and nothing — not logout, not a password change —
    #: can revoke a cookie somebody already copied.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    @validates("role")
    def _mirror_role(self, _key: str, value: str | None) -> str | None:
        self.is_superuser = value == "admin"
        return value


@event.listens_for(User, "before_insert")
def _role_from_legacy_flag(_mapper, _connection, target: User) -> None:
    """A row created with only ``is_superuser=True`` becomes an admin.

    Covers code written before roles existed (scripts, older tests). A
    row created with ``is_superuser=False`` and no role keeps NULL.
    """
    if target.role is None and target.is_superuser:
        target.role = "admin"
