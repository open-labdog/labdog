"""Actions alerts may run, chosen by Grafana labels.

An alert asks for an action with its ``labdog_action`` label;
``alert_actions`` is the list of actions an alert may start, each with
its scope, parameters and options. Runs remember the alert that caused
them, and ``alert_action_dispatches`` records what each alert's request
came to.

Alerts are investigated when their labels ask for it, not by severity, so
the stored ``ai.auto_investigate_min_severity`` row goes, as does
``ai.alert_full_auto_alertnames``, which the full-auto label replaced
before it was released.

Revision ID: 0049_alert_actions
Revises: 0048_alerts_settings
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0049_alert_actions"
down_revision = "0048_alerts_settings"
branch_labels = None
depends_on = None

_RETIRED_SETTINGS = ("ai.auto_investigate_min_severity", "ai.alert_full_auto_alertnames")


def upgrade() -> None:
    op.create_table(
        "alert_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action_key", sa.String(64), nullable=False),
        sa.Column("target_kind", sa.String(8), nullable=False, server_default="fleet"),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("snapshot_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_rollback", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("action_key", name=op.f("uq_alert_actions_action_key")),
        sa.CheckConstraint(
            "(target_kind = 'fleet' AND target_id IS NULL) OR "
            "(target_kind IN ('host','group') AND target_id IS NOT NULL)",
            name=op.f("ck_alert_actions_target"),
        ),
    )

    op.add_column("action_runs", sa.Column("alert_event_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_action_runs_alert_event_id_alert_events",
        "action_runs",
        "alert_events",
        ["alert_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_action_runs_alert_event_id", "action_runs", ["alert_event_id"])

    op.create_table(
        "alert_action_dispatches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_event_id",
            sa.Integer(),
            sa.ForeignKey("alert_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "alert_action_id",
            sa.Integer(),
            sa.ForeignKey("alert_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_key", sa.String(64), nullable=False),
        sa.Column(
            "host_id", sa.Integer(), sa.ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "action_run_id",
            sa.Integer(),
            sa.ForeignKey("action_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("hand_to_ai", sa.Boolean(), nullable=False),
        sa.Column("check_outcome", sa.String(16), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("alert_event_id", "alert_action_id", "action_run_id"):
        op.create_index(f"ix_alert_action_dispatches_{column}", "alert_action_dispatches", [column])

    op.execute(
        sa.text("DELETE FROM app_settings WHERE key IN :keys").bindparams(
            sa.bindparam("keys", value=_RETIRED_SETTINGS, expanding=True)
        )
    )


def downgrade() -> None:
    # The retired settings rows are not restored: their values are gone,
    # and the defaults apply without a row.
    op.drop_table("alert_action_dispatches")
    op.drop_index("ix_action_runs_alert_event_id", table_name="action_runs")
    op.drop_constraint(
        "fk_action_runs_alert_event_id_alert_events", "action_runs", type_="foreignkey"
    )
    op.drop_column("action_runs", "alert_event_id")
    op.drop_table("alert_actions")
