"""add service management tables

Revision ID: 0004
Revises: 0003_drop_rbac
Create Date: 2026-03-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str = "0003_drop_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create service_rules table (enum created implicitly by create_table)
    op.create_table(
        "service_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer,
            sa.ForeignKey("host_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "host_id",
            sa.Integer,
            sa.ForeignKey("hosts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column(
            "state",
            sa.Enum("running", "stopped", name="servicestate"),
            nullable=False,
            server_default="running",
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_service_rules_scope",
        ),
    )

    # 3. Create host_module_status table
    op.create_table(
        "host_module_status",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "host_id",
            sa.Integer,
            sa.ForeignKey("hosts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_type", sa.String(50), nullable=False),
        sa.Column(
            "sync_status", sa.String(20), nullable=False, server_default="unknown"
        ),
        sa.Column(
            "drift_check_enabled", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_drift_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "host_id",
            "module_type",
            name="uq_host_module_status_host_id_module_type",
        ),
    )

    # 4. Add module_type to sync_jobs
    op.add_column(
        "sync_jobs",
        sa.Column("module_type", sa.String(50), nullable=False, server_default="firewall"),
    )


def downgrade() -> None:
    op.drop_column("sync_jobs", "module_type")
    op.drop_table("host_module_status")
    op.drop_table("service_rules")
    sa.Enum("running", "stopped", name="servicestate").drop(op.get_bind(), checkfirst=True)
