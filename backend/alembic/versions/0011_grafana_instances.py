"""Add ``grafana_instances`` table.

A registered Grafana Mimir/Loki (Prometheus-compatible) backend. LabDog
queries ``prometheus_query_url`` to render instant host metrics and hands
the push URLs to the Alloy install action. ``encrypted_token`` is an
optional AES-256-GCM bearer token; ``ca_cert_pem`` is plaintext (CA certs
are public). Exactly one row is the default (enforced in the API layer).

Revision ID: 0011_grafana_instances
Revises: 0010_proxmox_node_ca_cert_pem
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_grafana_instances"
down_revision = "0010_proxmox_node_ca_cert_pem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grafana_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prometheus_query_url", sa.String(length=500), nullable=False),
        sa.Column("prometheus_push_url", sa.String(length=500), nullable=False),
        sa.Column("loki_push_url", sa.String(length=500), nullable=True),
        sa.Column("org_id", sa.String(length=200), nullable=True),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=True),
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ca_cert_pem", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_grafana_instances_name", "grafana_instances", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_grafana_instances_name", table_name="grafana_instances")
    op.drop_table("grafana_instances")
