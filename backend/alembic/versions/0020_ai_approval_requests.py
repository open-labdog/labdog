"""Approval requests: one paused mutating call, awaiting a human.

Phase 3 lets the agent make changes, which means it has to be able to
stop and ask. This table is the parked state a person needs to see — what
was asked for, on which host, and why the classifier called it a write.

It deliberately does not hold the session's conversational state. That
already lives where each runner keeps it: the ``ai_messages`` transcript
for the HTTP providers, the Claude CLI's own session file (referenced by
``ai_sessions.resume_state``) for the Agent SDK. Duplicating it here
would give a resumed session two sources of truth about what was said.

The ``ai_tool_calls.approval_id`` column has existed since 0015 as a bare
integer, waiting for this table; it becomes a real foreign key here.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_ai_approval_requests"
down_revision = "0019_drop_provider_egress_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_approval_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("ai_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=True),
        sa.Column(
            "target_host_id",
            sa.Integer(),
            sa.ForeignKey("hosts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("command_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "classification", sa.String(length=16), nullable=False, server_default="mutating"
        ),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "decided_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_approval_requests_session_id", "ai_approval_requests", ["session_id"])
    op.create_index("ix_ai_approval_requests_status", "ai_approval_requests", ["status"])
    op.create_index("ix_ai_approval_requests_created_at", "ai_approval_requests", ["created_at"])
    # The reaper sweeps on (status, expires_at); indexing expires_at alone
    # is enough because pending rows are a small minority.
    op.create_index("ix_ai_approval_requests_expires_at", "ai_approval_requests", ["expires_at"])

    op.create_foreign_key(
        "fk_ai_tool_calls_approval_id",
        "ai_tool_calls",
        "ai_approval_requests",
        ["approval_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ai_tool_calls_approval_id", "ai_tool_calls", type_="foreignkey")
    op.drop_index("ix_ai_approval_requests_expires_at", table_name="ai_approval_requests")
    op.drop_index("ix_ai_approval_requests_created_at", table_name="ai_approval_requests")
    op.drop_index("ix_ai_approval_requests_status", table_name="ai_approval_requests")
    op.drop_index("ix_ai_approval_requests_session_id", table_name="ai_approval_requests")
    op.drop_table("ai_approval_requests")
