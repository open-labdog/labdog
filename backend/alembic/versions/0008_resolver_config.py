"""add resolver_configs table

Revision ID: 0008
Revises: 263ff4c0e96c
Create Date: 2026-03-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "263ff4c0e96c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE resolvertype AS ENUM ('resolv_conf', 'systemd_resolved', 'networkmanager');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.create_table(
        "resolver_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("host_id", sa.Integer(), nullable=True),
        sa.Column(
            "nameservers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "search_domains",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "resolver_type",
            postgresql.ENUM(
                "resolv_conf",
                "systemd_resolved",
                "networkmanager",
                name="resolvertype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("dns_over_tls", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_resolver_configs_scope",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["host_groups.id"],
            name=op.f("fk_resolver_configs_group_id_host_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            name=op.f("fk_resolver_configs_host_id_hosts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolver_configs")),
    )
    op.create_index(
        "ix_resolver_config_group_unique",
        "resolver_configs",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("group_id IS NOT NULL"),
    )
    op.create_index(
        "ix_resolver_config_host_unique",
        "resolver_configs",
        ["host_id"],
        unique=True,
        postgresql_where=sa.text("host_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_resolver_config_host_unique", table_name="resolver_configs")
    op.drop_index("ix_resolver_config_group_unique", table_name="resolver_configs")
    op.drop_table("resolver_configs")
    op.execute("DROP TYPE resolvertype")
