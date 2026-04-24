"""add action_packs table

Revision ID: a7c3e9f2d1b8
Revises: 65ffee5f8790
Create Date: 2026-04-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f2d1b8"
down_revision: str | Sequence[str] | None = "65ffee5f8790"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


pack_auth_type = postgresql.ENUM(
    "none",
    "ssh",
    "https_token",
    name="packauthtype",
    create_type=False,
)


def upgrade() -> None:
    pack_auth_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "action_packs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("ref", sa.String(length=200), nullable=False, server_default="main"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auth_type", pack_auth_type, nullable=False, server_default="none"),
        sa.Column("encrypted_ssh_key", sa.LargeBinary(), nullable=True),
        sa.Column("ssh_known_hosts", sa.Text(), nullable=True),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("current_sha", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_packs")),
        sa.UniqueConstraint("name", name=op.f("uq_action_packs_name")),
        # Enforce auth/credential consistency at the DB level too.
        # Schemas already enforce on the API side; this is belt-and-braces
        # for rogue UPDATEs and for inserts made by future code paths.
        sa.CheckConstraint(
            "(auth_type = 'none' AND encrypted_ssh_key IS NULL AND encrypted_token IS NULL)"
            " OR (auth_type = 'ssh' AND encrypted_ssh_key IS NOT NULL AND encrypted_token IS NULL)"
            " OR (auth_type = 'https_token' AND encrypted_token IS NOT NULL AND encrypted_ssh_key IS NULL)",
            name="ck_action_packs_auth_consistency",
        ),
    )
    op.create_index(
        op.f("ix_action_packs_name"),
        "action_packs",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_action_packs_name"), table_name="action_packs")
    op.drop_table("action_packs")
    pack_auth_type.drop(op.get_bind(), checkfirst=True)
