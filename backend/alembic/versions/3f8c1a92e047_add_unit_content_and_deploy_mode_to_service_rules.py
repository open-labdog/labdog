"""add unit_content and deploy_mode to service_rules

Revision ID: 3f8c1a92e047
Revises: d87b771ba89d
Create Date: 2026-04-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f8c1a92e047"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    deploymode_enum = sa.Enum("full", "override", name="deploymode")
    deploymode_enum.create(op.get_bind())
    op.add_column("service_rules", sa.Column("unit_content", sa.Text(), nullable=True))
    op.add_column(
        "service_rules",
        sa.Column(
            "deploy_mode",
            sa.Enum("full", "override", name="deploymode"),
            nullable=False,
            server_default="override",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("service_rules", "deploy_mode")
    op.drop_column("service_rules", "unit_content")
    sa.Enum(name="deploymode").drop(op.get_bind())
