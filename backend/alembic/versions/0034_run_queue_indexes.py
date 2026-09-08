"""Index the tables the per-host claim protocol scans.

BUG-73. ``check_host_busy`` runs three queries on every claim — and a
claim happens before every sync, every action run and every drift check.
Two of the three had no index to use:

* ``action_runs`` carried only ``ix_action_runs_scheduled_action_id``, so
  the host-targeted scan (``host_id = :id AND status = 'running'``) and
  the group-membership scan (``status = 'running' AND host_id IS NULL``)
  both sequential-scanned the table.
* ``action_host_runs`` carried none at all, so the group-member scan
  (``host_id = :id AND status = 'running'``) scanned a table whose
  ``output`` column holds up to a mebibyte of transcript per row.

``sync_jobs`` was already covered by ``ix_sync_jobs_host_module_status``,
whose leading column is ``host_id``.

The ``(status, created_at)`` pairs serve the retention job added
alongside this (``app/tasks/run_retention.py``) and the sweepers.
``action_host_runs`` gets ``(status)`` alone — it has no ``created_at``,
and retention reaches it by cascade from its parent run rather than by
date. That one also serves
``metrics/aggregates.get_action_host_run_counts``, which groups every row
by status on each fifteen-second Prometheus scrape: measured on 20k rows
the plan goes from a sequential scan of the heap — 5 MB of it, most of it
transcript — to an index-only scan. "Index-only" needs the visibility map,
so the first scrapes after this migration still touch the heap until
autovacuum has been over the table once.

Built ``CONCURRENTLY``: a plain ``CREATE INDEX`` takes a lock that blocks
writes for its duration, and the whole point of this migration is tables
that are allowed to get large. That requires running outside a
transaction, hence the ``autocommit_block``. The trade is that a failure
mid-build leaves an invalid index behind rather than rolling back;
``IF NOT EXISTS`` makes the retry idempotent, and an invalid index should
be dropped by hand before re-running.

Revision ID: 0034_run_queue_indexes
Revises: 0033_group_priority_unique
"""

from alembic import op

revision = "0034_run_queue_indexes"
down_revision = "0033_group_priority_unique"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_action_runs_host_status", "action_runs", "(host_id, status)"),
    ("ix_action_runs_status_created", "action_runs", "(status, created_at)"),
    ("ix_action_host_runs_host_status", "action_host_runs", "(host_id, status)"),
    ("ix_action_host_runs_status", "action_host_runs", "(status)"),
    ("ix_sync_jobs_status_created", "sync_jobs", "(status, created_at)"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in _INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {columns}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _table, _columns in reversed(_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
