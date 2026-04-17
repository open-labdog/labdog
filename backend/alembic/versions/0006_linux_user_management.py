"""add linux user management tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create linux_users table (userstate enum created implicitly by create_table)
    op.create_table(
        "linux_users",
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
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("uid", sa.Integer, nullable=True),
        sa.Column("shell", sa.String(100), nullable=False, server_default="/bin/bash"),
        sa.Column("home_dir", sa.String(200), nullable=True),
        sa.Column(
            "state",
            sa.Enum("present", "absent", name="userstate"),
            nullable=False,
            server_default="present",
        ),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("sudo_rule", sa.Text, nullable=True),
        sa.Column(
            "authorized_keys",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "supplementary_groups",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
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
            name="ck_linux_users_scope",
        ),
    )

    # Create linux_groups table (reuses userstate enum already created above)
    op.create_table(
        "linux_groups",
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
        sa.Column("groupname", sa.String(32), nullable=False),
        sa.Column("gid", sa.Integer, nullable=True),
        sa.Column(
            "state",
            sa.Enum("present", "absent", name="userstate", create_type=False),
            nullable=False,
            server_default="present",
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
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
            name="ck_linux_groups_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("linux_groups")
    op.drop_table("linux_users")
    sa.Enum("present", "absent", name="userstate").drop(op.get_bind(), checkfirst=True)
