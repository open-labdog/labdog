"""Alert intake: the alert_events table and its link to a session.

Two producers write these rows — a Grafana contact-point webhook and an
Alertmanager poller — and they must not create duplicates of each other's
work, so the table carries the dedup key rather than the writers.

**The unique key is (fingerprint, starts_at), not fingerprint.**
Alertmanager's fingerprint hashes the alert's label set, so the same rule
firing for the same host yields the same fingerprint every time it ever
fires. Keying on it alone would fold next month's outage into this
month's row; adding ``starts_at`` keeps one continuous firing as one row
while letting a genuinely new firing be a new one.

Also closes a loop left open in 0015: ``ai_sessions.alert_event_id`` was
added then as a plain integer, because the table it points at did not
exist yet. It becomes a real foreign key here.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_alert_events"
down_revision = "0022_ai_credential_set_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("alertname", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="firing"),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("annotations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedup_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("host_id", sa.Integer(), nullable=True),
        sa.Column("investigation_session_id", sa.Integer(), nullable=True),
        sa.Column("investigation_outcome", sa.String(length=32), nullable=True),
        sa.Column("investigation_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["host_id"], ["hosts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["investigation_session_id"], ["ai_sessions.id"], ondelete="SET NULL"
        ),
        # The dedup contract, enforced in the database rather than in the
        # two writers. Both use ON CONFLICT against this constraint, so a
        # webhook and a poll arriving at the same instant produce one row
        # and an increment, not a race.
        sa.UniqueConstraint("fingerprint", "starts_at", name="uq_alert_events_fingerprint_starts"),
    )
    op.create_index("ix_alert_events_fingerprint", "alert_events", ["fingerprint"])
    op.create_index("ix_alert_events_alertname", "alert_events", ["alertname"])
    op.create_index("ix_alert_events_status", "alert_events", ["status"])
    op.create_index("ix_alert_events_starts_at", "alert_events", ["starts_at"])
    op.create_index("ix_alert_events_created_at", "alert_events", ["created_at"])
    op.create_index("ix_alert_events_host_id", "alert_events", ["host_id"])

    # Left as a bare integer in 0015 because alert_events did not exist.
    op.create_foreign_key(
        "fk_ai_sessions_alert_event_id",
        "ai_sessions",
        "alert_events",
        ["alert_event_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ai_sessions_alert_event_id", "ai_sessions", type_="foreignkey")
    op.drop_index("ix_alert_events_host_id", table_name="alert_events")
    op.drop_index("ix_alert_events_created_at", table_name="alert_events")
    op.drop_index("ix_alert_events_starts_at", table_name="alert_events")
    op.drop_index("ix_alert_events_status", table_name="alert_events")
    op.drop_index("ix_alert_events_alertname", table_name="alert_events")
    op.drop_index("ix_alert_events_fingerprint", table_name="alert_events")
    op.drop_table("alert_events")
