"""Add ``pending_reason`` to sync_jobs.

Mirrors the action-side ``pending_reason`` added in 0003. When
``host_sync_orchestrator._claim_or_defer`` defers a job because
another op on the same host is in flight, it now writes a short
human-readable description of the blocker (formatted via
``app.tasks.host_lock.format_pending_reason``) alongside the status
flip to ``pending``. The UI renders the string as a tooltip on the
amber "Host busy" badge so operators see *what* is ahead of them
in the per-host queue -- matching the parity that already exists
on the action queue.

The column is nullable; existing rows and any deferred row that
predates this change get NULL and the UI degrades to the old
badge-only behaviour.

Revision ID: 0005_syncjob_pending_reason
Revises: 0004_drop_pack_position
Create Date: 2026-05-19
"""

from __future__ import annotations

from alembic import op

revision = "0005_syncjob_pending_reason"
down_revision = "0004_drop_pack_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sync_jobs ADD COLUMN pending_reason VARCHAR(255)")


def downgrade() -> None:
    op.execute("ALTER TABLE sync_jobs DROP COLUMN pending_reason")
