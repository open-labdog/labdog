"""workflow action key

Revision ID: e3f8a1b2c4d5
Revises: d4e7f2a9b3c1
Create Date: 2026-04-20 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f8a1b2c4d5"
down_revision: str | Sequence[str] | None = "d4e7f2a9b3c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "update_workflows",
        sa.Column(
            "action_key",
            sa.String(64),
            nullable=False,
            server_default="linux-upgrade",
        ),
    )
    op.add_column(
        "update_workflows",
        sa.Column(
            "action_parameters",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("update_workflows", "action_parameters")
    op.drop_column("update_workflows", "action_key")
