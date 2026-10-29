"""Store post-run syncs the way the host queue reads them.

``dispatch_post_run_sync`` wrote the orchestrator's canonical module name
(``packages``, ``services``, ``hosts-file``, ``linux-users``) to
``sync_jobs.module_type`` and recorded no ``module_filter``. The queue
re-dispatches a deferred row from those two columns, knew only the short
names, and read anything else as "every module": two such rows ran a full
sync of valheim, ``/etc/hosts`` and firewall included (BUG-116). The queue
now leaves rows it cannot read alone, so these have to be renamed or they
would sit ``pending`` for good.

A pending row whose short name another active sync on its host already
holds cannot be renamed (``uq_sync_job_active``) and duplicates that sync
anyway; it is cancelled. A running one in the same position keeps its name
and gets its filter recorded.

Revision ID: 0051_post_run_sync_module_type
Revises: 0050_host_reported_hostname
"""

from __future__ import annotations

from alembic import op

revision = "0051_post_run_sync_module_type"
down_revision = "0050_host_reported_hostname"
branch_labels = None
depends_on = None

#: Canonical name → the short name every other writer uses.
_RENAMES = {
    "services": "service",
    "packages": "package",
    "hosts-file": "hosts_file",
    "linux-users": "linux_user",
}

_ACTIVE = "('pending', 'running')"


def upgrade() -> None:
    for canonical, short in _RENAMES.items():
        taken = f"""
            EXISTS (
                SELECT 1 FROM sync_jobs AS other
                WHERE other.host_id = sync_jobs.host_id
                  AND other.module_type = '{short}'
                  AND other.status IN {_ACTIVE}
            )
        """
        op.execute(
            f"""
            UPDATE sync_jobs
            SET status = 'cancelled',
                completed_at = now(),
                error_message = 'Cancelled by migration 0051: the same module was '
                                'already queued for this host (BUG-116).'
            WHERE module_type = '{canonical}'
              AND status = 'pending'
              AND {taken}
            """
        )
        op.execute(
            f"""
            UPDATE sync_jobs
            SET module_type = '{short}',
                module_filter = COALESCE(module_filter, '["{canonical}"]'::jsonb)
            WHERE module_type = '{canonical}'
              AND NOT (status IN {_ACTIVE} AND {taken})
            """
        )
        op.execute(
            f"""
            UPDATE sync_jobs
            SET module_filter = COALESCE(module_filter, '["{canonical}"]'::jsonb)
            WHERE module_type = '{canonical}'
            """
        )


def downgrade() -> None:
    """Deliberately a no-op.

    Writing the canonical names back would only recreate the rows the old
    queue widened to every module.
    """
