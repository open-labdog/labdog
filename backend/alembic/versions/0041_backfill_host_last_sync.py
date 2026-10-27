"""Backfill ``hosts.last_sync_at`` from the per-module sync history.

Nothing wrote ``hosts.last_sync_at`` until ``4c5b2377``: a sync run
stamped only the ``host_module_status`` rows it covered. That fix writes
forward, so every host synced before it still read "never" (BUG-99).
This copies each host's newest module ``last_sync_at`` up to the host,
the value the fix would have written at the end of that run.

Only moves a value forward, so re-running it changes nothing. The
downgrade leaves the values alone: they are true, and clearing them
would only bring back "never".

Revision ID: 0041_backfill_host_last_sync
Revises: 0040_host_run_output_default
"""

from __future__ import annotations

from alembic import op

revision = "0041_backfill_host_last_sync"
down_revision = "0040_host_run_output_default"
branch_labels = None
depends_on = None


BACKFILL = """
UPDATE hosts h
SET last_sync_at = m.newest
FROM (
    SELECT host_id, max(last_sync_at) AS newest
    FROM host_module_status
    WHERE last_sync_at IS NOT NULL
    GROUP BY host_id
) m
WHERE m.host_id = h.id
  AND (h.last_sync_at IS NULL OR h.last_sync_at < m.newest)
"""


def upgrade() -> None:
    op.execute(BACKFILL)


def downgrade() -> None:
    pass
