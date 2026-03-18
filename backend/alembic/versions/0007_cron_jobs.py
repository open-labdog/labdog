"""add cron jobs table

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create cron_jobs table (cronstate enum created implicitly by create_table)
    op.create_table(
        "cron_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer,
            sa.ForeignKey("host_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "host_id",
            sa.Integer,
            sa.ForeignKey("hosts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("user", sa.String(32), nullable=False, server_default="root"),
        sa.Column("schedule", sa.String(100), nullable=False),
        sa.Column("command", sa.Text, nullable=False),
        sa.Column(
            "environment",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "state",
            sa.Enum("present", "absent", name="cronstate"),
            nullable=False,
            server_default="present",
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_cron_jobs_scope",
        ),
    )

    # Create partial unique indexes
    op.create_index(
        "uq_cron_jobs_group_name_user",
        "cron_jobs",
        ["group_id", "name", "user"],
        unique=True,
        postgresql_where=sa.text("group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_cron_jobs_host_name_user",
        "cron_jobs",
        ["host_id", "name", "user"],
        unique=True,
        postgresql_where=sa.text("host_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_cron_jobs_host_name_user", table_name="cron_jobs")
    op.drop_index("uq_cron_jobs_group_name_user", table_name="cron_jobs")
    op.drop_table("cron_jobs")
    sa.Enum("present", "absent", name="cronstate").drop(
        op.get_bind(), checkfirst=True
    )
