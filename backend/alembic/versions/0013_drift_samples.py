"""Add ``drift_samples`` table (dashboard charts & activity feed metrics substrate).

Forward-only metrics substrate: every module drift-check task/endpoint will
write exactly one row per check here, atomically with its existing
``HostModuleStatus`` / ``Host.sync_status`` write. Powers the dashboard
"sync success rate" and "drift trend" charts (``GET /api/dashboard/*``) and,
later, an OpenMetrics ``/metrics`` exporter that reuses the same
aggregations. No seeding / backfill on this migration — intentional, the
table starts empty and only accumulates going forward.

Revision ID: 0013_drift_samples
Revises: 0012_grafana_instances_kind_url
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op

revision = "0013_drift_samples"
down_revision = "0012_grafana_instances_kind_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drift_samples (
            id                   SERIAL       PRIMARY KEY,
            host_id              INTEGER      NOT NULL REFERENCES hosts(id) ON DELETE CASCADE,
            module_type          VARCHAR(50)  NOT NULL,
            status               VARCHAR(20)  NOT NULL,
            add_count            INTEGER      NOT NULL DEFAULT 0,
            remove_count         INTEGER      NOT NULL DEFAULT 0,
            policy_change_count  INTEGER      NOT NULL DEFAULT 0,
            duration_ms          INTEGER      NULL,
            checked_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_drift_samples_checked_at ON drift_samples (checked_at)")
    op.execute(
        "CREATE INDEX ix_drift_samples_module_type_checked_at"
        " ON drift_samples (module_type, checked_at)"
    )
    op.execute("CREATE INDEX ix_drift_samples_host_id ON drift_samples (host_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS drift_samples")
