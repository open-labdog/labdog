"""drop user_group_permissions and grouprole enum

Revision ID: 0003_drop_rbac
Revises: 0002
Create Date: 2026-03-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_drop_rbac"
down_revision: str = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("user_group_permissions")
    op.execute("DROP TYPE IF EXISTS grouprole")


def downgrade() -> None:
    # Recreate the grouprole enum
    grouprole = sa.Enum("admin", "editor", "viewer", name="grouprole")
    grouprole.create(op.get_bind())

    # Recreate the table
    op.create_table(
        "user_group_permissions",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("host_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", grouprole, nullable=False),
    )
