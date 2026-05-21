"""Add ``ssh_session_transcripts`` table (SEC-09).

Captures newline-delimited command text from SSH terminal sessions so
operators can audit what was actually run.  The ``session_id`` column
carries the same UUID already present in the ``session_start`` /
``session_end`` ``audit_log`` rows, allowing full session reconstruction
by selecting rows ordered by ``recorded_at``.

Revision ID: 0007_ssh_session_transcript
Revises: 0006_host_ssh_host_key_entry
Create Date: 2026-05-21
"""
from __future__ import annotations

from alembic import op

revision = "0007_ssh_session_transcript"
down_revision = "0006_host_ssh_host_key_entry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ssh_session_transcripts (
            id           SERIAL       PRIMARY KEY,
            session_id   VARCHAR(64)  NOT NULL,
            host_id      INTEGER      REFERENCES hosts(id) ON DELETE CASCADE,
            user_id      INTEGER      REFERENCES users(id) ON DELETE SET NULL,
            command_text TEXT         NOT NULL,
            recorded_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_ssh_session_transcripts_session_id"
        " ON ssh_session_transcripts (session_id)"
    )
    op.execute(
        "CREATE INDEX ix_ssh_session_transcripts_recorded_at"
        " ON ssh_session_transcripts (recorded_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ssh_session_transcripts")
