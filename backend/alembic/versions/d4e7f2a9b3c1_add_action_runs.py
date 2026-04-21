"""add action runs

Revision ID: d4e7f2a9b3c1
Revises: be8ccebcd23e
Create Date: 2026-04-20 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7f2a9b3c1"
down_revision: str | Sequence[str] | None = "be8ccebcd23e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "action_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column("action_version", sa.String(length=32), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("parallelism", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(host_id IS NOT NULL)::int + (group_id IS NOT NULL)::int = 1",
            name="ck_action_runs_scope",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            name=op.f("fk_action_runs_host_id_hosts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["host_groups.id"],
            name=op.f("fk_action_runs_group_id_host_groups"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_user_id"],
            ["users.id"],
            name=op.f("fk_action_runs_triggered_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_runs")),
    )
    op.create_table(
        "action_host_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action_run_id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("output", sa.Text(), server_default="''", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_run_id"],
            ["action_runs.id"],
            name=op.f("fk_action_host_runs_action_run_id_action_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            name=op.f("fk_action_host_runs_host_id_hosts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_host_runs")),
        sa.UniqueConstraint("action_run_id", "host_id", name="uq_action_host_run"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("action_host_runs")
    op.drop_table("action_runs")
