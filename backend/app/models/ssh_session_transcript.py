from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SSHSessionTranscript(Base):
    """Per-line transcript of bytes sent from user to SSH host (stdin capture).

    Rows are written by the ssh_terminal WebSocket handler as newline-delimited
    commands arrive.  The ``session_id`` matches the UUID already present in
    ``session_start`` / ``session_end`` audit_log rows so a full session can be
    reconstructed by ordering rows by ``recorded_at``.

    ``command_text`` holds the UTF-8 decoded (errors="replace") content of one
    newline-terminated chunk, stripped of trailing ``\\r`` / ``\\n``.  For
    sessions that exceed the 1 MiB cap the last row is the truncation sentinel
    string defined in ``app.ssh_terminal.transcript``.

    The table is append-only.  Do not add UPDATE or DELETE endpoints.
    """

    __tablename__ = "ssh_session_transcripts"
    __table_args__ = (
        Index("ix_ssh_session_transcripts_session_id", "session_id"),
        Index("ix_ssh_session_transcripts_recorded_at", "recorded_at"),
        {"comment": "Append-only SSH stdin transcript.  No updates or deletes."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Matches the session_id in the audit_log session_start/session_end rows.",
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    command_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="UTF-8 decoded (errors=replace) command bytes, trailing \\r/\\n stripped.",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    # NO updated_at -- append-only by design
