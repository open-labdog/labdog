"""Alert-triggered schedules.

A schedule can fire when an alert fires, not only on cron or by hand:
``scheduled_actions.trigger`` (``cron`` | ``alert``) and the alert
columns, the unique key widened by ``trigger`` so one action can have a
cron schedule and an alert trigger on the same target. Runs remember the
alert that caused them, and ``alert_action_dispatches`` records what each
matching schedule did with each alert.

Revision ID: 0049_alert_triggered_actions
Revises: 0048_alerts_settings
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0049_alert_triggered_actions"
down_revision = "0048_alerts_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_actions",
        sa.Column("trigger", sa.String(8), nullable=False, server_default="cron"),
    )
    op.add_column("scheduled_actions", sa.Column("alert_match", sa.Text(), nullable=True))
    op.add_column(
        "scheduled_actions",
        sa.Column("alert_cooldown_minutes", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "scheduled_actions",
        sa.Column("investigate_on_failure", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_check_constraint(
        op.f("ck_scheduled_actions_trigger"),
        "scheduled_actions",
        "(trigger = 'cron' AND alert_match IS NULL) OR "
        "(trigger = 'alert' AND alert_match IS NOT NULL AND schedule_cron IS NULL)",
    )
    op.drop_constraint("uq_scheduled_actions_target_action", "scheduled_actions", type_="unique")
    op.create_unique_constraint(
        "uq_scheduled_actions_target_action",
        "scheduled_actions",
        ["target_kind", "target_id", "action_key", "trigger"],
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
            "scheduled_action_id",
            sa.Integer(),
            sa.ForeignKey("scheduled_actions.id", ondelete="SET NULL"),
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
        sa.Column("investigate_on_failure", sa.Boolean(), nullable=False),
        sa.Column("check_outcome", sa.String(16), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("alert_event_id", "scheduled_action_id", "action_run_id"):
        op.create_index(f"ix_alert_action_dispatches_{column}", "alert_action_dispatches", [column])


def downgrade() -> None:
    op.drop_table("alert_action_dispatches")
    op.drop_index("ix_action_runs_alert_event_id", table_name="action_runs")
    op.drop_constraint(
        "fk_action_runs_alert_event_id_alert_events", "action_runs", type_="foreignkey"
    )
    op.drop_column("action_runs", "alert_event_id")
    # Alert triggers have no meaning before this revision.
    op.execute(sa.text("DELETE FROM scheduled_actions WHERE trigger = 'alert'"))
    op.drop_constraint("uq_scheduled_actions_target_action", "scheduled_actions", type_="unique")
    op.create_unique_constraint(
        "uq_scheduled_actions_target_action",
        "scheduled_actions",
        ["target_kind", "target_id", "action_key"],
    )
    op.drop_constraint(op.f("ck_scheduled_actions_trigger"), "scheduled_actions", type_="check")
    for column in ("investigate_on_failure", "alert_cooldown_minutes", "alert_match", "trigger"):
        op.drop_column("scheduled_actions", column)
