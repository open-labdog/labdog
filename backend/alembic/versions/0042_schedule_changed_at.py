"""Add ``scheduled_actions.schedule_changed_at``.

The scheduler looked for a schedule's next run after its last dispatch,
or after its creation if it had never run. Re-enabling a schedule that
had been off for a week, or changing its expression, therefore fired it
on the next tick for a run time that had passed meanwhile or that only
the new expression matched, while the dialog previewed a time in the
future. The column records when the expression or the enabled flag last
changed, and the walk starts from the later of it and the last dispatch.

Existing rows start with NULL, which leaves their walk as it was.

Revision ID: 0042_schedule_changed_at
Revises: 0041_backfill_host_last_sync
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0042_schedule_changed_at"
down_revision = "0041_backfill_host_last_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_actions",
        sa.Column("schedule_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_actions", "schedule_changed_at")
