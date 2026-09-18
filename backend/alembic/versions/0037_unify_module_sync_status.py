"""Fold the legacy ``drifted`` module status into ``out_of_sync``.

``host_module_status.sync_status`` is free text, and three of the seven
modules — packages, cron and linux users — wrote ``"drifted"`` where the
other four wrote ``"out_of_sync"``. Nothing was broken by that: the host
rollup in ``api/host_state.refresh_host_sync_status`` treated the two as
equivalent, and each of the three drift tasks translated to
``out_of_sync`` before recording its metrics sample. It was two spellings
of one state, and every consumer had to know both.

This is the data half of removing the second spelling; the writers change
in the same commit, so after this migration nothing produces ``drifted``.

The value is not enum-constrained at the database level (it is a
``String(20)``), which is why this is an ``UPDATE`` and not an enum
alteration — and also why the exporter treats the column as
observed-values-only. That has one operator-visible consequence:
``labdog_host_modules{sync_status="drifted"}`` stops being emitted. It was
never zero-filled — absent, not zero — so an alert keyed on it goes from
matching some series to matching none.

Downgrade cannot restore the split. It does not know which rows the three
legacy modules wrote versus the four that always said ``out_of_sync``, and
guessing by ``module_type`` would rewrite rows this migration never
touched. It is deliberately a no-op.

Revision ID: 0037_unify_module_sync_status
Revises: 0036_drift_sample_rollup
"""

from alembic import op

revision = "0037_unify_module_sync_status"
down_revision = "0036_drift_sample_rollup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE host_module_status SET sync_status = 'out_of_sync' WHERE sync_status = 'drifted'"
    )


def downgrade() -> None:
    # Intentionally empty — see the module docstring. The information
    # needed to split `out_of_sync` back into two spellings does not
    # survive the upgrade, and inventing it from `module_type` would
    # corrupt rows that were always `out_of_sync`.
    pass
