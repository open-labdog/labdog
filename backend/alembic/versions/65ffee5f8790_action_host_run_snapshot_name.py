"""add snapshot_name to action_host_runs

Revision ID: 65ffee5f8790
Revises: a569a9c1d2b6
Create Date: 2026-04-22 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "65ffee5f8790"
down_revision: str | Sequence[str] | None = "a569a9c1d2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Track the Proxmox snapshot that an action-run took (if any).

    Destructive actions now snapshot the host's Proxmox VM before running,
    roll back to that snapshot on failure, and delete it on success —
    mirroring the behaviour of scheduled update workflows. This column lets
    the API and UI expose which snapshot was captured.
    """
    op.add_column(
        "action_host_runs", sa.Column("snapshot_name", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("action_host_runs", "snapshot_name")
