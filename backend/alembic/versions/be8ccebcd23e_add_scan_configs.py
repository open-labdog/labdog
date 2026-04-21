"""add scan configs

Revision ID: be8ccebcd23e
Revises: c5d8e2f49a1b
Create Date: 2026-04-20 21:05:56.864257

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be8ccebcd23e"
down_revision: str | Sequence[str] | None = "c5d8e2f49a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "scan_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "cidrs", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("ssh_key_id", sa.Integer(), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_user", sa.String(length=32), nullable=False),
        sa.Column(
            "default_group_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("cron_expression", sa.String(length=100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_add", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=20), nullable=True),
        sa.Column("last_run_hosts_added", sa.Integer(), nullable=False),
        sa.Column("last_run_hosts_pending", sa.Integer(), nullable=False),
        sa.Column("last_run_error", sa.Text(), nullable=True),
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
            "(interval_minutes IS NOT NULL) <> (cron_expression IS NOT NULL)",
            name="ck_scan_configs_schedule_one_of",
        ),
        sa.ForeignKeyConstraint(
            ["ssh_key_id"],
            ["ssh_keys.id"],
            name=op.f("fk_scan_configs_ssh_key_id_ssh_keys"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_configs")),
        sa.UniqueConstraint("name", name=op.f("uq_scan_configs_name")),
    )
    op.create_table(
        "pending_hosts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_config_id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=True),
        sa.Column("ssh_verified", sa.Boolean(), nullable=False),
        sa.Column("ssh_error", sa.Text(), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scan_config_id"],
            ["scan_configs.id"],
            name=op.f("fk_pending_hosts_scan_config_id_scan_configs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_hosts")),
        sa.UniqueConstraint("scan_config_id", "ip_address", name="uq_pending_scan_ip"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("pending_hosts")
    op.drop_table("scan_configs")
