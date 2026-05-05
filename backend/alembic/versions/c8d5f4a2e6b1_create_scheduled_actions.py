"""create scheduled_actions and extend action_runs

Revision ID: c8d5f4a2e6b1
Revises: f7b2d9c4a1e8
Create Date: 2026-05-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d5f4a2e6b1"
down_revision: str | Sequence[str] | None = "f7b2d9c4a1e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the unified ``scheduled_actions`` table and extend
    ``action_runs`` with schedule-aware columns.

    No business logic changes here — the legacy ``update_workflows``
    flow continues to work alongside this until C8 backfills rows and
    drops the old tables.
    """
    op.create_table(
        "scheduled_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_kind", sa.String(length=8), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("schedule_cron", sa.String(length=100), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "snapshot_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "verify_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "auto_rollback", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("batch_size", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "last_dispatched_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(target_kind = 'fleet' AND target_id IS NULL) OR "
            "(target_kind IN ('host','group') AND target_id IS NOT NULL)",
            name="ck_scheduled_actions_target",
        ),
        sa.UniqueConstraint(
            "target_kind",
            "target_id",
            "action_key",
            name="uq_scheduled_actions_target_action",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_actions")),
    )
    op.create_index(
        "ix_scheduled_actions_due",
        "scheduled_actions",
        ["action_key", "enabled"],
    )
    op.create_index(
        "ix_scheduled_actions_target",
        "scheduled_actions",
        ["target_kind", "target_id"],
    )

    # Extend action_runs with the schedule FK plus three universal columns
    # mirrored from ScheduledAction at dispatch time so per-host executors
    # see immutable run-time intent.
    op.add_column(
        "action_runs",
        sa.Column("scheduled_action_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_action_runs_scheduled_action_id_scheduled_actions"),
        "action_runs",
        "scheduled_actions",
        ["scheduled_action_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_action_runs_scheduled_action_id"),
        "action_runs",
        ["scheduled_action_id"],
    )
    op.add_column(
        "action_runs",
        sa.Column(
            "snapshot_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )
    op.add_column(
        "action_runs",
        sa.Column(
            "verify_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )
    op.add_column(
        "action_runs",
        sa.Column(
            "auto_rollback",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )

    # Relax the scope check constraint so fleet runs (host_id NULL AND
    # group_id NULL) are allowed when scheduled_action_id is set. There's
    # no ad-hoc fleet path through POST /api/actions/runs.
    op.drop_constraint("ck_action_runs_scope", "action_runs", type_="check")
    op.create_check_constraint(
        "ck_action_runs_scope",
        "action_runs",
        "(host_id IS NOT NULL AND group_id IS NULL) OR "
        "(host_id IS NULL AND group_id IS NOT NULL) OR "
        "(host_id IS NULL AND group_id IS NULL AND scheduled_action_id IS NOT NULL)",
    )


def downgrade() -> None:
    """Roll back the schema additions."""
    # Restore the strict scope check.
    op.drop_constraint("ck_action_runs_scope", "action_runs", type_="check")
    op.create_check_constraint(
        "ck_action_runs_scope",
        "action_runs",
        "(host_id IS NOT NULL)::int + (group_id IS NOT NULL)::int = 1",
    )

    op.drop_column("action_runs", "auto_rollback")
    op.drop_column("action_runs", "verify_enabled")
    op.drop_column("action_runs", "snapshot_enabled")
    op.drop_index(op.f("ix_action_runs_scheduled_action_id"), table_name="action_runs")
    op.drop_constraint(
        op.f("fk_action_runs_scheduled_action_id_scheduled_actions"),
        "action_runs",
        type_="foreignkey",
    )
    op.drop_column("action_runs", "scheduled_action_id")

    op.drop_index("ix_scheduled_actions_target", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_due", table_name="scheduled_actions")
    op.drop_table("scheduled_actions")
