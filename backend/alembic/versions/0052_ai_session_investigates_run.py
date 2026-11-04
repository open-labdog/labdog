"""Add ``ai_sessions.investigates_action_run_id``.

The run an investigation started from the run page is about. Its own
column rather than ``action_run_id``: that one names the run that *owns*
a session (``_builtin.ai_task``), and the busy guard passes it as
``exclude_action_run_id``, so a session pointed at the run it is
investigating would be let past that run's hold on the host.

``SET NULL`` on delete, so run retention can prune a run without taking
the investigation with it.

Revision ID: 0052_ai_session_investigates_run
Revises: 0051_post_run_sync_module_type
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0052_ai_session_investigates_run"
down_revision = "0051_post_run_sync_module_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_sessions", sa.Column("investigates_action_run_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ai_sessions_investigates_action_run_id_action_runs",
        "ai_sessions",
        "action_runs",
        ["investigates_action_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ai_sessions_investigates_action_run_id",
        "ai_sessions",
        ["investigates_action_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_sessions_investigates_action_run_id", table_name="ai_sessions")
    op.drop_constraint(
        "fk_ai_sessions_investigates_action_run_id_action_runs", "ai_sessions", type_="foreignkey"
    )
    op.drop_column("ai_sessions", "investigates_action_run_id")
