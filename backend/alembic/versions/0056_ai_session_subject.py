"""Add ``ai_sessions.subject_kind`` and ``subject_key``.

What an "Investigate with AI" button outside the run page started a
session about — a resource hot spot, a failed sync, a stuck operation,
drift, or a whole host — so a second press reopens it. See
:mod:`app.ai.subject_investigation`.

Revision ID: 0056_ai_session_subject
Revises: 0055_metrics_destinations
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0056_ai_session_subject"
down_revision = "0055_metrics_destinations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_sessions", sa.Column("subject_kind", sa.String(16), nullable=True))
    op.add_column("ai_sessions", sa.Column("subject_key", sa.String(120), nullable=True))
    op.create_index("ix_ai_sessions_subject_key", "ai_sessions", ["subject_key"])


def downgrade() -> None:
    op.drop_index("ix_ai_sessions_subject_key", table_name="ai_sessions")
    op.drop_column("ai_sessions", "subject_key")
    op.drop_column("ai_sessions", "subject_kind")
