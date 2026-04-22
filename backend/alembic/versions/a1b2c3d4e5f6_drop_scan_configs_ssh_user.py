"""drop ssh_user from scan_configs

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-04-21 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the redundant ssh_user column from scan_configs.

    The SSH user is already stored on the linked ssh_keys row; reading it
    from there eliminates the silent mismatch described in BUG-28.
    """
    op.drop_column("scan_configs", "ssh_user")


def downgrade() -> None:
    """Re-add ssh_user to scan_configs with the historic default of 'root'."""
    op.add_column(
        "scan_configs",
        sa.Column(
            "ssh_user",
            sa.String(length=32),
            server_default="root",
            nullable=False,
        ),
    )
