"""External identities: SSO-only accounts and the identity link table.

Revision ID: 0059_external_identities
Revises: 0058_user_roles

Additive only. ``users.hashed_password`` becomes nullable, which no
existing row notices, and ``user_identities`` is new. Community unbork
writes neither; the Enterprise SSO package does, through
``app.auth.external``.
"""

import sqlalchemy as sa

from alembic import op

revision = "0059_external_identities"
down_revision = "0058_user_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=True)
    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    # Accounts that only ever signed in through a directory have no password
    # to restore. They are disabled with an empty hash rather than deleted, so
    # their audit history and ownership survive the downgrade; password login
    # treats an empty hash like a missing one and refuses it.
    op.execute(
        "UPDATE users SET hashed_password = '', is_active = false WHERE hashed_password IS NULL"
    )
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=False)
