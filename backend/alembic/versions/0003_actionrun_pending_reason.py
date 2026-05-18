"""Add ``pending_reason`` to action_runs and action_host_runs.

When ``host_lock.check_host_busy`` decides "yes, the host is busy",
the action defer paths flip the row to ``status='pending'`` and now
also write a short human-readable string naming the in-flight op that
is holding the host. The frontend renders the string as a tooltip on
the amber "Host busy" badge and as a banner on the run-detail page so
the operator can see *what* is ahead of them in the per-host queue.

Both columns are nullable. Existing rows (and any deferred row that
predates this change) get NULL — the UI degrades gracefully to the
old badge-only behaviour. New defers populate the column the same
commit they flip status to ``pending``.

Revision ID: 0003_pending_reason
Revises: 0002_drop_membership_role
Create Date: 2026-05-18

Note on the revision id length: alembic's default ``alembic_version``
table caps ``version_num`` at varchar(32) — keep the slug short.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic
revision = "0003_pending_reason"
down_revision = "0002_drop_membership_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE action_runs ADD COLUMN pending_reason VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE action_host_runs ADD COLUMN pending_reason VARCHAR(255)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE action_host_runs DROP COLUMN pending_reason")
    op.execute("ALTER TABLE action_runs DROP COLUMN pending_reason")
