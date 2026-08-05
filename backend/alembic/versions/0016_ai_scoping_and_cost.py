"""Add per-session tool/action allowlists and per-tool result size.

``allowed_tools`` and ``allowed_action_keys`` bound what one session may
do: the first controls which capabilities it can reach at all, the second
which remediations it may perform. NULL on ``allowed_tools`` means every
registered tool (the phase-1 behaviour, so existing rows keep working);
NULL or empty on ``allowed_action_keys`` means it may not change anything.

``result_chars`` records how much text each tool call fed back to the
model, which is what makes tool cost measurable — notably whether reading
logs through Loki is cheaper than reading them over SSH, which depends on
the query and is worth having data on rather than assuming.

Revision ID: 0016_ai_scoping_and_cost
Revises: 0015_ai_sessions
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_ai_scoping_and_cost"
down_revision = "0015_ai_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_sessions", sa.Column("allowed_tools", postgresql.JSONB(), nullable=True))
    op.add_column(
        "ai_sessions", sa.Column("allowed_action_keys", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "ai_tool_calls",
        sa.Column("result_chars", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ai_tool_calls", "result_chars")
    op.drop_column("ai_sessions", "allowed_action_keys")
    op.drop_column("ai_sessions", "allowed_tools")
