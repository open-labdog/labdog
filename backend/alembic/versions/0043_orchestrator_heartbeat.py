"""Let an action run say which orchestrator is driving it, and whether it is alive.

A group run is driven by one long-running Celery task that dispatches its
hosts in batches. When the worker died, the task died with it, the run
stayed ``running``, and the hosts it had not reached never ran: nothing
noticed for ``batches × per-host deadline + 1h`` — on lin-manager, the
2026-10-01 nightly upgrade was still "running" eight hours after LabDog
restarted underneath it (BUG-101).

``heartbeat_at`` is written by the orchestrator every few seconds while
it drives the run. A stale one is how the action sweeper tells a dead
orchestrator from a slow run, and it hands the run to a new one.
``orchestrator_id`` is the fencing token for that hand-over: the id of
the task that owns the run, so an old orchestrator that turns out to be
alive after all stops at its next heartbeat instead of driving the run
alongside its replacement.

Both stay NULL whenever nothing is driving the run, which is every row
written before this migration.

Revision ID: 0043_orchestrator_heartbeat
Revises: 0042_schedule_changed_at
"""

import sqlalchemy as sa

from alembic import op

revision = "0043_orchestrator_heartbeat"
down_revision = "0042_schedule_changed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_runs",
        sa.Column("orchestrator_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "action_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("action_runs", "heartbeat_at")
    op.drop_column("action_runs", "orchestrator_id")
