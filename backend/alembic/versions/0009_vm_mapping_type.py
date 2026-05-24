"""Add ``vm_type`` column to ``vm_mappings``.

Distinguishes QEMU VMs (``"qemu"``) from LXC containers (``"lxc"``) so
that snapshot, rollback, and status API calls use the correct Proxmox
endpoint path. Existing rows default to ``"qemu"``; re-run discovery
against any LXC-backed hosts to update them.

Revision ID: 0009_vm_mapping_type
Revises: 0008_dismissed_hosts
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op

revision = "0009_vm_mapping_type"
down_revision = "0008_dismissed_hosts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE vm_mappings
        ADD COLUMN vm_type VARCHAR(10) NOT NULL DEFAULT 'qemu'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE vm_mappings DROP COLUMN vm_type")
