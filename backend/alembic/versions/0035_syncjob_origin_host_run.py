"""Link a SyncJob back to the built-in run that created it.

BUG-81. ``_builtin.sync`` creates a ``SyncJob`` and drives it inline. When
the host turns out to be busy the inner sync defers — the job stays
``pending`` and the host queue re-dispatches it later — but the built-in
had no way to hear about that, so it finished its ``ActionHostRun``
``succeeded`` and the run history said the sync happened at a time it did
not.

The honest shape is for the built-in's row to stay ``pending`` until the
queued job actually runs, which means the job has to know whose row to
close when it does. Nullable because every sync started from the API or
the scheduler has no originating run, and ``ON DELETE SET NULL`` because
run retention (BUG-73) deletes those rows on a schedule and must not take
sync history with them.

Indexed: retention deletes ``action_host_runs`` in batches, and an
unindexed inbound FK makes each one scan ``sync_jobs`` to enforce the
``SET NULL``.

Revision ID: 0035_syncjob_origin_host_run
Revises: 0034_run_queue_indexes
"""

import sqlalchemy as sa

from alembic import op

revision = "0035_syncjob_origin_host_run"
down_revision = "0034_run_queue_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_jobs",
        sa.Column("origin_action_host_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_sync_jobs_origin_action_host_run_id_action_host_runs",
        "sync_jobs",
        "action_host_runs",
        ["origin_action_host_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sync_jobs_origin_action_host_run_id",
        "sync_jobs",
        ["origin_action_host_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_sync_jobs_origin_action_host_run_id", table_name="sync_jobs")
    op.drop_constraint(
        "fk_sync_jobs_origin_action_host_run_id_action_host_runs",
        "sync_jobs",
        type_="foreignkey",
    )
    op.drop_column("sync_jobs", "origin_action_host_run_id")
