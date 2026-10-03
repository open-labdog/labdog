"""Tables for outbound notifications.

``smtp_settings`` is a single row. There is one mail server per instance,
and modelling it as a list would invite questions ("which one sends?")
with no good answer.

``notifications`` is an outbox, one row per (recipient, event). Rows are
written in the same transaction as whatever caused them — an alert row,
an approval request — so a rolled-back approval cannot leave behind an
email about a request that does not exist. A periodic task drains it.
The row is also the delivery record: status, attempts and the last SMTP
error are kept so "why didn't I get an email?" has an answer in the UI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: How the SMTP connection is secured. ``starttls`` upgrades a plain
#: connection (port 587), ``tls`` is TLS from the first byte (port 465),
#: ``none`` is plaintext — for a relay on localhost or the LAN only.
TLS_MODES = ("none", "starttls", "tls")

#: Outbox row states. ``failed`` is final: retries ran out, or the row
#: can never be sent as written (no public URL for its link).
NOTIFICATION_STATUSES = ("pending", "sent", "failed")

#: The id of the only ``smtp_settings`` row.
SMTP_SETTINGS_ID = 1


class SMTPSettings(Base):
    """The mail server LabDog sends through. At most one row, id 1."""

    __tablename__ = "smtp_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Master switch for email. Off means nothing is queued at all, not
    #: that it is queued and held: turning it on later must not deliver
    #: a backlog of stale alerts.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    tls_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="starttls")
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    # AES-256-GCM under the master key, like every other stored secret, and
    # rotated with them by scripts/rotate_encryption_key.py. Never returned
    # by the API.
    encrypted_password: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, default=None
    )
    from_address: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class NotificationSubscription(Base):
    """One user opted in to one event type on one channel.

    Opt-in rather than opt-out: an install that grows a new event type
    must not start emailing everyone about it.
    """

    __tablename__ = "notification_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "event_type", "channel", name="uq_notification_subscriptions_user_event"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Notification(Base):
    """One message for one recipient — the outbox row and its delivery record."""

    __tablename__ = "notifications"
    __table_args__ = (
        # Events that a periodic scan could raise twice — "this approval
        # expires soon" — carry a key, and a second insert is a no-op.
        # NULL keys never collide, so ordinary events are unaffected.
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The address at the time it was queued. Kept even if the user is
    #: deleted or changes address: it is where this message went.
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Path inside LabDog this message points at, e.g. ``/alerts``. Joined
    #: to ``notifications.public_url`` at send time, never to a request's
    #: Host header — a link built from that is whatever the requester says.
    link_path: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
