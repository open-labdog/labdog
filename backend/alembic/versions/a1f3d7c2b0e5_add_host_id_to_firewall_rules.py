"""add host_id to firewall_rules

Revision ID: a1f3d7c2b0e5
Revises: b2e4f8a31c9d
Create Date: 2026-04-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3d7c2b0e5"
down_revision: str | Sequence[str] | None = "b2e4f8a31c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("firewall_rules", sa.Column("host_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_firewall_rules_host_id",
        "firewall_rules",
        "hosts",
        ["host_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("firewall_rules", "group_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        "ck_firewall_rules_scope",
        "firewall_rules",
        "(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)",
    )
    op.create_index("ix_firewall_rules_host_id", "firewall_rules", ["host_id"])


def downgrade() -> None:
    op.drop_index("ix_firewall_rules_host_id", "firewall_rules")
    op.drop_constraint("ck_firewall_rules_scope", "firewall_rules", type_="check")
    op.alter_column("firewall_rules", "group_id", existing_type=sa.Integer(), nullable=False)
    op.drop_constraint("fk_firewall_rules_host_id", "firewall_rules", type_="foreignkey")
    op.drop_column("firewall_rules", "host_id")
