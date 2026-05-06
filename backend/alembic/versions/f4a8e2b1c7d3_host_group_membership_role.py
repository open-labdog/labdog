"""host_group_memberships: nullable role column

Revision ID: f4a8e2b1c7d3
Revises: e7b2c4f9a3d1
Create Date: 2026-05-06 16:00:00.000000

Adds an optional ``role`` field to the host↔group join table so cluster-
scoped actions (initially ``k8s-upgrade``) can route work by node role.
Two values are accepted: ``control_plane`` and ``worker``. ``NULL``
means "no role assigned" — the orchestrator rejects cluster-mode runs
when any required member has a NULL role.

Constraints:
- ``CHECK`` clamps the column to the two allowed strings or NULL. Using
  a string + check rather than a Postgres enum keeps schema migrations
  cheap (enum value adds need separate ``ALTER TYPE`` rounds).
- No default. Existing rows are NULL after upgrade — that's the
  honest state, and any host accidentally targeted with a cluster
  action surfaces a clear error rather than getting silently
  classified.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a8e2b1c7d3"
down_revision: str | Sequence[str] | None = "e7b2c4f9a3d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "host_group_memberships",
        sa.Column("role", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_host_group_memberships_role_valid",
        "host_group_memberships",
        "role IS NULL OR role IN ('control_plane', 'worker')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_host_group_memberships_role_valid",
        "host_group_memberships",
        type_="check",
    )
    op.drop_column("host_group_memberships", "role")
