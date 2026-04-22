"""add os_family, default_nic, kernel_version, kernel_release to hosts

Revision ID: a569a9c1d2b6
Revises: 8e4e4bd1e533
Create Date: 2026-04-22 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a569a9c1d2b6"
down_revision: str | Sequence[str] | None = "8e4e4bd1e533"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add host fact columns collected at host-add time.

    See plans/host-facts-collection.md for rationale. All nullable — a host
    that pre-dates this migration will have NULLs until the next facts
    collection runs.
    """
    op.add_column("hosts", sa.Column("os_family", sa.String(length=32), nullable=True))
    op.add_column("hosts", sa.Column("default_nic", sa.String(length=32), nullable=True))
    op.add_column("hosts", sa.Column("kernel_version", sa.String(length=64), nullable=True))
    op.add_column("hosts", sa.Column("kernel_release", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("hosts", "kernel_release")
    op.drop_column("hosts", "kernel_version")
    op.drop_column("hosts", "default_nic")
    op.drop_column("hosts", "os_family")
