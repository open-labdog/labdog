"""Add the AI session tables: sessions, messages, tool calls, usage ledger.

``ai_sessions`` is one agentic run — a chat, a scheduled mission, a
verify judgement, or an alert investigation. ``action_run_id`` links a
run driven by the ``_builtin.ai_task`` pseudo-action back to the action
subsystem so it inherits scheduling, history, and cancellation.

``ai_usage_days`` is a rolled-up spend ledger rather than a view over
sessions: budget checks stay a single cheap aggregate, and the numbers
survive session deletion or retention pruning.

``alert_event_id`` / ``approval_id`` are plain integers here — their
foreign keys arrive with the tables that own them (0016, 0017).

Revision ID: 0015_ai_sessions
Revises: 0014_ai_providers
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_ai_sessions"
down_revision = "0014_ai_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="chat"),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("mission", sa.Text(), nullable=False),
        sa.Column(
            "autonomy_level", sa.String(length=16), nullable=False, server_default="read_only"
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("target_host_ids", postgresql.JSONB(), nullable=True),
        sa.Column("action_run_id", sa.Integer(), nullable=True),
        sa.Column("alert_event_id", sa.Integer(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("command_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_markdown", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("resume_state", postgresql.JSONB(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ai_providers.id"],
            name="fk_ai_sessions_provider_id_ai_providers",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["action_run_id"],
            ["action_runs.id"],
            name="fk_ai_sessions_action_run_id_action_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_ai_sessions_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_ai_sessions_provider_id", "ai_sessions", ["provider_id"])
    op.create_index("ix_ai_sessions_status", "ai_sessions", ["status"])
    op.create_index("ix_ai_sessions_action_run_id", "ai_sessions", ["action_run_id"])
    op.create_index("ix_ai_sessions_created_at", "ai_sessions", ["created_at"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["ai_sessions.id"],
            name="fk_ai_messages_session_id_ai_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_ai_messages_session_id_seq"),
    )
    op.create_index("ix_ai_messages_session_id", "ai_messages", ["session_id"])

    op.create_table(
        "ai_tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("message_seq", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=True),
        sa.Column(
            "classification", sa.String(length=16), nullable=False, server_default="unknown"
        ),
        sa.Column("target_host_id", sa.Integer(), nullable=True),
        sa.Column("approval_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("snapshot_name", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["ai_sessions.id"],
            name="fk_ai_tool_calls_session_id_ai_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_host_id"],
            ["hosts.id"],
            name="fk_ai_tool_calls_target_host_id_hosts",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_ai_tool_calls_session_id", "ai_tool_calls", ["session_id"])

    op.create_table(
        "ai_usage_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ai_providers.id"],
            name="fk_ai_usage_days_provider_id_ai_providers",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("usage_date", "provider_id", name="uq_ai_usage_days_usage_date"),
    )
    op.create_index("ix_ai_usage_days_usage_date", "ai_usage_days", ["usage_date"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_days_usage_date", table_name="ai_usage_days")
    op.drop_table("ai_usage_days")
    op.drop_index("ix_ai_tool_calls_session_id", table_name="ai_tool_calls")
    op.drop_table("ai_tool_calls")
    op.drop_index("ix_ai_messages_session_id", table_name="ai_messages")
    op.drop_table("ai_messages")
    for idx in (
        "ix_ai_sessions_created_at",
        "ix_ai_sessions_action_run_id",
        "ix_ai_sessions_status",
        "ix_ai_sessions_provider_id",
    ):
        op.drop_index(idx, table_name="ai_sessions")
    op.drop_table("ai_sessions")
