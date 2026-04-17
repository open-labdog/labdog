"""add hosts entries table

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create hosts_entries table
    op.create_table(
        "hosts_entries",
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
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column(
            "aliases",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
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
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_hosts_entries_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("hosts_entries")
