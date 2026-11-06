"""Index ``action_host_runs.host_id``.

A host's Overview reads its own run history: every run that reached it,
whether it targeted the host, one of its groups or the fleet. That is a
lookup by ``host_id`` alone, and the only index on the column was the
second half of ``uq_action_host_run (action_run_id, host_id)``, which a
lookup by host cannot use.

Revision ID: 0054_action_host_runs_host_id
Revises: 0053_ai_session_archived_at
"""

from __future__ import annotations

from alembic import op

revision = "0054_action_host_runs_host_id"
down_revision = "0053_ai_session_archived_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_action_host_runs_host_id", "action_host_runs", ["host_id"])


def downgrade() -> None:
    op.drop_index("ix_action_host_runs_host_id", table_name="action_host_runs")
