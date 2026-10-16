"""Add ``alert_events.investigation_autonomy`` and its note.

Alert investigations were read-only by construction, so there was nothing
to record. They now run at the level the alert remediation settings allow
— read-only, approval, or full auto for named alerts — and a listed alert
can be downgraded by a safeguard. The row keeps the level the session
started at and, when it is not simply the instance setting, why.

Existing rows start with NULL: every one of them was read-only, and the
Alerts page shows nothing extra for that.

Revision ID: 0044_alert_autonomy
Revises: 0043_orchestrator_heartbeat
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0044_alert_autonomy"
down_revision = "0043_orchestrator_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_events",
        sa.Column("investigation_autonomy", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "alert_events",
        sa.Column("investigation_autonomy_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alert_events", "investigation_autonomy_note")
    op.drop_column("alert_events", "investigation_autonomy")
