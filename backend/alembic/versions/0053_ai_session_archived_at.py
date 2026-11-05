"""Add ``ai_sessions.archived_at``.

An archived session is left out of the assistant's list and keeps
everything else: its transcript, its rollback, its alert's link, and its
place in the full-auto safeguards' counts, which deleting it would lose.
See :mod:`app.ai.session_housekeeping`.

Revision ID: 0053_ai_session_archived_at
Revises: 0052_ai_session_investigates_run
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0053_ai_session_archived_at"
down_revision = "0052_ai_session_investigates_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_sessions", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_ai_sessions_archived_at", "ai_sessions", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_sessions_archived_at", table_name="ai_sessions")
    op.drop_column("ai_sessions", "archived_at")
