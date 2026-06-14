"""Add ``grafana_instances`` table.

A registered Grafana-stack endpoint — Mimir (metrics) or Loki (logs),
distinguished by ``kind`` and registered separately. A single ``url``
(the ingest/remote-write URL) is stored; LabDog derives the query URL
from it. ``encrypted_token`` is an optional AES-256-GCM bearer token;
``ca_cert_pem`` is plaintext (CA certs are public). At most one row per
kind is the default (enforced in the API layer).

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
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("org_id", sa.String(length=200), nullable=True),
        sa.Column("auth_type", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("username", sa.String(length=255), nullable=True),
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
