"""add host os facts

Revision ID: a9f3c1b8d2e7
Revises: e3f8a1b2c4d5
Create Date: 2026-04-21
"""
import sqlalchemy as sa

from alembic import op

revision = "a9f3c1b8d2e7"
down_revision = "e3f8a1b2c4d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hosts", sa.Column("os_codename", sa.String(64), nullable=True))
    op.add_column("hosts", sa.Column("os_pretty_name", sa.String(255), nullable=True))
    op.add_column(
        "hosts",
        sa.Column("os_facts_collected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("hosts", "os_facts_collected_at")
    op.drop_column("hosts", "os_pretty_name")
    op.drop_column("hosts", "os_codename")
