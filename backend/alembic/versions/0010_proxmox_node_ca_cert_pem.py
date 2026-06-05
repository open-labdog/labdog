"""Add ``ca_cert_pem`` column to ``proxmox_nodes``.

Holds an optional PEM-encoded CA certificate used to verify a node's TLS
certificate per-node (BUG-52). Stored as plaintext ``TEXT`` — CA certs are
public, not secrets, so this column is explicitly NOT encrypted. The column
is nullable with no default; existing rows become ``NULL``, which preserves
current behavior (verify against the system trust store when
``verify_ssl=True``).

Revision ID: 0010_proxmox_node_ca_cert_pem
Revises: 0009_vm_mapping_type
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op

revision = "0010_proxmox_node_ca_cert_pem"
down_revision = "0009_vm_mapping_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE proxmox_nodes ADD COLUMN ca_cert_pem TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE proxmox_nodes DROP COLUMN ca_cert_pem")
