"""Converge ``grafana_instances`` to the kind + single-url schema.

Migration 0011 was reshaped during development from a combined
(prometheus_query_url / prometheus_push_url / loki_push_url) instance to
a per-kind single-``url`` instance. Databases that applied the earlier
0011 still carry the old columns, so this migration brings any existing
``grafana_instances`` table to the target schema **idempotently** — it is
a no-op on a database that already has the new columns (fresh installs
that applied the reshaped 0011) and a full transform on one that has the
old columns.

Target columns: kind, url, org_id, auth_type, username, encrypted_token,
verify_ssl, ca_cert_pem, is_default.

Revision ID: 0012_grafana_instances_kind_url
Revises: 0011_grafana_instances
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op

revision = "0012_grafana_instances_kind_url"
down_revision = "0011_grafana_instances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the new columns if they are missing (nullable for now so
    #    existing rows don't violate NOT NULL before backfill).
    op.execute("ALTER TABLE grafana_instances ADD COLUMN IF NOT EXISTS kind varchar(16)")
    op.execute("ALTER TABLE grafana_instances ADD COLUMN IF NOT EXISTS url varchar(500)")
    op.execute(
        "ALTER TABLE grafana_instances "
        "ADD COLUMN IF NOT EXISTS auth_type varchar(16) NOT NULL DEFAULT 'none'"
    )
    op.execute("ALTER TABLE grafana_instances ADD COLUMN IF NOT EXISTS username varchar(255)")

    # 2. Backfill kind/url from the legacy columns where they still exist.
    #    Old rows held both prometheus + loki on one row; map them to a
    #    Mimir instance using the push URL (the loki URL is dropped — the
    #    operator re-adds Loki separately under the new model).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'grafana_instances'
                  AND column_name = 'prometheus_push_url'
            ) THEN
                UPDATE grafana_instances
                   SET url = COALESCE(NULLIF(url, ''), prometheus_push_url,
                                      prometheus_query_url, ''),
                       kind = COALESCE(kind, 'mimir');
            END IF;
        END $$;
        """
    )

    # 3. Default any rows still missing values (e.g. empty table edge cases).
    op.execute("UPDATE grafana_instances SET kind = 'mimir' WHERE kind IS NULL")
    op.execute("UPDATE grafana_instances SET url = '' WHERE url IS NULL")

    # 4. Enforce NOT NULL now that every row has values.
    op.execute("ALTER TABLE grafana_instances ALTER COLUMN kind SET NOT NULL")
    op.execute("ALTER TABLE grafana_instances ALTER COLUMN url SET NOT NULL")

    # 5. Drop the legacy columns if present.
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS prometheus_query_url")
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS prometheus_push_url")
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS loki_push_url")


def downgrade() -> None:
    # Best-effort reverse to the original 0011 shape.
    op.execute(
        "ALTER TABLE grafana_instances ADD COLUMN IF NOT EXISTS prometheus_query_url varchar(500)"
    )
    op.execute(
        "ALTER TABLE grafana_instances ADD COLUMN IF NOT EXISTS prometheus_push_url varchar(500)"
    )
    op.execute("ALTER TABLE grafana_instances ADD COLUMN IF NOT EXISTS loki_push_url varchar(500)")
    op.execute(
        "UPDATE grafana_instances "
        "SET prometheus_push_url = url, prometheus_query_url = url "
        "WHERE prometheus_push_url IS NULL"
    )
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS kind")
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS url")
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS auth_type")
    op.execute("ALTER TABLE grafana_instances DROP COLUMN IF EXISTS username")
