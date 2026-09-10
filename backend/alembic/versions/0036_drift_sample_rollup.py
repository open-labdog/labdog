"""Rollup table so drift retention can delete rows without resetting counters.

``drift_samples`` had no retention job and grew unbounded. The obvious fix
— delete old rows — breaks the exporter, because ``labdog_drift_checks_total``
and ``labdog_drift_changes_total`` are ``COUNT(*)``/``SUM()`` over the whole
table. Deleting rows makes a *counter* decrease, which Prometheus reads as a
process restart: ``rate()`` copes, ``increase()`` across the deletion
silently under-reports, and nobody is told.

So the retention job folds each row it is about to delete into this table,
in the same transaction as the delete, and the exporter reports live rows
plus rollup. The totals then only ever move forward, which is the one thing
a counter has to promise.

Keyed on ``(module_type, status)``, the finest grain any of the three
affected families needs: ``labdog_drift_checks_total`` is per
module_type+status, and the change sums and the duration histogram are per
module_type, so both are recovered by summing across statuses.

The histogram is included even though the original TODO listed only the two
counters. ``labdog_drift_check_duration_seconds`` is a histogram, and
``_bucket`` / ``_sum`` / ``_count`` are counters too — rolling up two of the
three families and leaving the third to reset would have fixed the visible
half of the problem.

``duration_bounds`` stores the bucket boundaries the counts in
``duration_buckets`` were computed against. If ``_BUCKETS_DRIFT`` is ever
changed, previously-rolled-up counts are not comparable to new ones, and
the exporter needs to be able to notice that rather than add mismatched
arrays element-wise.

Revision ID: 0036_drift_sample_rollup
Revises: 0035_syncjob_origin_host_run
"""

import sqlalchemy as sa

from alembic import op

revision = "0036_drift_sample_rollup"
down_revision = "0035_syncjob_origin_host_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_sample_rollup",
        sa.Column("module_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("add_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("remove_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("policy_change_count", sa.BigInteger(), nullable=False, server_default="0"),
        # Only samples that recorded a duration; `duration_ms` is nullable
        # because not every call site can cheaply time itself, and the
        # histogram already excludes those. Counting them here would make
        # `_count` disagree with the live query it is added to.
        sa.Column("duration_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "duration_sum_seconds",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "duration_buckets",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "duration_bounds",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.PrimaryKeyConstraint("module_type", "status", name="pk_drift_sample_rollup"),
    )


def downgrade() -> None:
    # Lossy: the rolled-up totals are the only remaining record of the
    # deleted samples. Dropping this table makes every drift counter fall
    # back to whatever live rows survive, which is the counter reset this
    # migration exists to avoid.
    op.drop_table("drift_sample_rollup")
