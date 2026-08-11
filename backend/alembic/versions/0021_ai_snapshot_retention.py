"""Snapshot retention, and a per-session opt-out.

0020 gave the agent the ability to snapshot a host before changing it and
nothing to ever remove those snapshots — verified on a live instance,
where two accumulated on one VM inside ten minutes of testing. Every
future change would add another until the datastore filled.

Immediate cleanup on success, which is what the action-run path does,
would be wrong here. That path deletes its snapshot because a verify step
has just said the change was good. An AI session has no verify step, and
the snapshot exists precisely so a human can undo the agent's work *after
the fact* — throwing it away the moment the session succeeds discards the
thing it was taken for. So the answer is a retention window rather than a
cleanup step, and ``snapshot_pruned_at`` is what makes that sweep
idempotent.

``skip_snapshots`` is the other half: a session that does not want the
overhead can say so at creation. It composes with
``ai.snapshot_before_mutating`` by agreement — a snapshot is taken only
when the instance setting is on and the session has not opted out — so
turning snapshots off globally cannot be undone per session.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_ai_snapshot_retention"
down_revision = "0020_ai_approval_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_sessions",
        sa.Column("skip_snapshots", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ai_tool_calls",
        sa.Column("snapshot_pruned_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The sweep looks for calls that have a snapshot and have not been
    # pruned. Partial, because rows with a snapshot are a small minority of
    # tool calls — most are reads.
    op.create_index(
        "ix_ai_tool_calls_unpruned_snapshots",
        "ai_tool_calls",
        ["started_at"],
        postgresql_where=sa.text("snapshot_name IS NOT NULL AND snapshot_pruned_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_ai_tool_calls_unpruned_snapshots", table_name="ai_tool_calls")
    op.drop_column("ai_tool_calls", "snapshot_pruned_at")
    op.drop_column("ai_sessions", "skip_snapshots")
