"""A user's account at an outside identity provider (SSO, directory login).

Community unbork has no login method that creates these rows; it only
defines the table so an extension (the Enterprise SSO package) can link a
directory identity to a ``users`` row without a schema of its own for the
link. See :mod:`app.auth.external`.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserIdentity(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    #: Stable key of the provider that vouches for this identity, chosen by
    #: the extension (``oidc:corp``, ``ldap:ad``). Not a foreign key: the
    #: provider table, if any, belongs to the extension.
    provider: Mapped[str] = mapped_column(String(128))
    #: The provider's immutable id for the person: OIDC ``sub``, LDAP
    #: ``entryUUID`` / ``objectGUID``. Never a username or an email, which
    #: can be renamed or reassigned to somebody else.
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
