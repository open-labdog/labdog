"""Repair host_module_status rows written under the wrong module name.

``hosts/dependents.py`` wrote ``module_type="hosts_entries"`` for the
/etc/hosts module, while every consumer — the drift API and task, the
state API, the metrics aggregates, the orchestrator's type mapping and
the frontend — reads ``"hosts_file"``. So when a referenced host's IP
changed, its dependants got a *new, invisible* row marked ``out_of_sync``
while the row they actually read still said ``in_sync``, and their
``/etc/hosts`` kept pointing at the old address (BUG-68).

The rows are not merely misfiled: each one is a drift signal that was
raised correctly and then delivered to the wrong address. This migration
delivers them.

* Where a host has a stale row and no real one, the stale row is renamed
  and becomes the real one.
* Where a host has both, the real row wins — it holds the actual sync
  history, ``collected_state`` and timestamps — but if the stale row says
  ``out_of_sync``, that status is carried across first, because it is the
  signal the bug swallowed. Then the stale row is deleted.

The unique constraint on ``(host_id, module_type)`` is why the two cases
have to be separated rather than done in one UPDATE.

Revision ID: 0028_hosts_module_type
Revises: 0027_syncjob_module_filter
"""

from alembic import op

revision = "0028_hosts_module_type"
down_revision = "0027_syncjob_module_filter"
branch_labels = None
depends_on = None

_WRONG = "hosts_entries"
_RIGHT = "hosts_file"


def upgrade() -> None:
    # 1. Carry the swallowed drift signal onto the row that is actually read.
    #    Only ever makes a row *more* pessimistic: a host reported in sync
    #    while its /etc/hosts is stale is the failure being repaired, and the
    #    next drift check corrects an over-cautious flag on its own.
    op.execute(
        f"""
        UPDATE host_module_status AS real_row
        SET sync_status = 'out_of_sync',
            error_message = COALESCE(
                real_row.error_message,
                'Marked out of sync by migration 0028: a referenced host changed '
                'while this status was recorded under the wrong module name (BUG-68).'
            )
        FROM host_module_status AS stale
        WHERE real_row.host_id = stale.host_id
          AND real_row.module_type = '{_RIGHT}'
          AND stale.module_type = '{_WRONG}'
          AND stale.sync_status = 'out_of_sync'
          AND real_row.sync_status <> 'out_of_sync'
        """
    )

    # 2. Drop the stale rows that now have a real counterpart.
    op.execute(
        f"""
        DELETE FROM host_module_status AS stale
        WHERE stale.module_type = '{_WRONG}'
          AND EXISTS (
              SELECT 1 FROM host_module_status AS real_row
              WHERE real_row.host_id = stale.host_id
                AND real_row.module_type = '{_RIGHT}'
          )
        """
    )

    # 3. Whatever is left has no counterpart; rename it in place so the
    #    host keeps its drift history instead of starting from 'unknown'.
    op.execute(
        f"UPDATE host_module_status SET module_type = '{_RIGHT}' WHERE module_type = '{_WRONG}'"
    )


def downgrade() -> None:
    """Deliberately a no-op.

    Renaming rows back would recreate the invisible-status bug, and there
    is no record of which of the surviving ``hosts_file`` rows arrived by
    which route. Leaving the data correct is the only sane direction.
    """
