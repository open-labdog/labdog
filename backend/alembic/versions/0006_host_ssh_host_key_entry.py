"""Add ``ssh_host_key_entry`` to hosts.

Persists the SSH host public key seen on first successful connection
(TOFU — Trust On First Use).  Subsequent connections pass this value
as the ``known_hosts`` source to asyncssh so key changes are detected
rather than silently accepted.

The column is nullable; NULL means the host has never been contacted
successfully by the new helper, or the operator has explicitly cleared
it via ``POST /api/hosts/{id}/trust-host-key`` to re-TOFU after a
legitimate host re-key (OS reinstall, key rotation, etc.).

Revision ID: 0006_host_ssh_host_key_entry
Revises: 0005_syncjob_pending_reason
Create Date: 2026-05-21
"""
from __future__ import annotations

from alembic import op

revision = "0006_host_ssh_host_key_entry"
down_revision = "0005_syncjob_pending_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE hosts ADD COLUMN ssh_host_key_entry TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE hosts DROP COLUMN ssh_host_key_entry")
