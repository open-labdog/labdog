"""Check automatic fixes afterwards, and record rolling a host back.

A full-auto alert session ended when the model said it was done, and
nothing looked at whether the alert cleared. ``alert_events`` gains the
outcome of that check: ``fixed``, ``not_effective``, ``made_worse``, or
``unchecked`` when it could not run in time, with ``checking`` while it
does.

``ai_rollbacks`` records each time LabDog restored a host to a snapshot
the assistant took: automatically, after a fix made the host worse, or
because someone pressed the button. One row per attempt, refused ones
included, so the history says what was tried as well as what happened.
At most one rollback per session and host may be running or have
succeeded: a second one would undo whatever has happened since the first.

Existing rows start with NULL outcomes. The check only looks at sessions
that finished within the last day, so upgrading does not judge, or roll
back, fixes made long before it.

Revision ID: 0046_remediation_check
Revises: 0045_notifications
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0046_remediation_check"
down_revision = "0045_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_events",
        sa.Column("remediation_outcome", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "alert_events",
        sa.Column("remediation_detail", sa.Text(), nullable=True),
    )
    op.add_column(
        "alert_events",
        sa.Column("remediation_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alert_events_remediation_outcome", "alert_events", ["remediation_outcome"])

    op.create_table(
        "ai_rollbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("ai_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "host_id",
            sa.Integer(),
            sa.ForeignKey("hosts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "alert_event_id",
            sa.Integer(),
            sa.ForeignKey("alert_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("hostname", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("snapshot_name", sa.String(length=200), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column(
            "requested_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_rollbacks_session_id", "ai_rollbacks", ["session_id"])
    op.create_index("ix_ai_rollbacks_host_id", "ai_rollbacks", ["host_id"])
    op.create_index(
        "uq_ai_rollbacks_session_host_active",
        "ai_rollbacks",
        ["session_id", "host_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'succeeded')"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_rollbacks_session_host_active", table_name="ai_rollbacks")
    op.drop_index("ix_ai_rollbacks_host_id", table_name="ai_rollbacks")
    op.drop_index("ix_ai_rollbacks_session_id", table_name="ai_rollbacks")
    op.drop_table("ai_rollbacks")
    op.drop_index("ix_alert_events_remediation_outcome", table_name="alert_events")
    op.drop_column("alert_events", "remediation_checked_at")
    op.drop_column("alert_events", "remediation_detail")
    op.drop_column("alert_events", "remediation_outcome")
