"""Record why a session stopped, not just that it did.

A run that hit a cap finished with ``status='succeeded'`` and a green
badge, indistinguishable in the UI from one that reached its own
conclusion. The reason existed only in two places an operator cannot
reach: the Celery task's return value, and a sentence appended to the
bottom of the report markdown.

That was tolerable while the caps never fired. Once the token cap started
working, the case it exists for — a run cut short partway through an
investigation — became the case the UI was least able to describe.

Deliberately not ``error_message``. A capped run is not an error: it did
what it was asked to do until it ran out of the budget it was given, and
filing it as a failure would be as wrong in the other direction.

Nullable with no backfill. Existing rows genuinely do not know why they
stopped, and inventing "completed" for them would assert something this
migration cannot check.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_ai_session_stopped_reason"
down_revision = "0023_alert_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_sessions",
        sa.Column("stopped_reason", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_sessions", "stopped_reason")
