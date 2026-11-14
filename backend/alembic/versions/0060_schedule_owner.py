"""Scheduled actions get an owner whose rights each run is checked against.

Revision ID: 0060_schedule_owner
Revises: 0059_external_identities

Additive. Existing schedules get ``owner_required = false`` and keep
running as before, with no owner; a schedule created from now on, or one
someone takes ownership of, has its owner's permission re-checked at
every dispatch.
"""

import sqlalchemy as sa

from alembic import op

revision = "0060_schedule_owner"
down_revision = "0059_external_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_actions",
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "scheduled_actions",
        sa.Column("owner_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "scheduled_actions",
        sa.Column("owner_blocked_reason", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_actions", "owner_blocked_reason")
    op.drop_column("scheduled_actions", "owner_required")
    op.drop_column("scheduled_actions", "owner_user_id")
