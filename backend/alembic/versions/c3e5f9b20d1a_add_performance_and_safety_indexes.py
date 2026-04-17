"""add performance and safety indexes

Revision ID: c3e5f9b20d1a
Revises: a1f3d7c2b0e5
Create Date: 2026-04-03 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5f9b20d1a"
down_revision: str | None = "a1f3d7c2b0e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # FK indexes on module tables — these columns are the most-queried
    # predicates (every plan/sync/drift filters by group_id or host_id).
    for table in [
        "firewall_rules",
        "service_rules",
        "package_rules",
        "linux_users",
        "linux_groups",
        "cron_jobs",
        "hosts_entries",
    ]:
        op.create_index(f"ix_{table}_group_id", table, ["group_id"])
        # ix_firewall_rules_host_id already exists from a1f3d7c2b0e5
        if table != "firewall_rules":
            op.create_index(f"ix_{table}_host_id", table, ["host_id"])

    # host_module_status is queried per-module on every collect-state
    op.create_index("ix_host_module_status_host_id", "host_module_status", ["host_id"])

    # sync_jobs composite index — used on every sync trigger and status poll
    op.create_index(
        "ix_sync_jobs_host_module_status",
        "sync_jobs",
        ["host_id", "module_type", "status"],
    )

    # Partial unique index to prevent duplicate active sync jobs (TOCTOU fix)
    op.execute(
        "CREATE UNIQUE INDEX uq_sync_job_active "
        "ON sync_jobs(host_id, module_type) "
        "WHERE status IN ('pending', 'running')"
    )

    # Audit log indexes — entity lookups and time-ordered pagination
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    # Drop unique constraint on host_groups.priority — equal priority is valid
    op.drop_constraint("uq_host_groups_priority", "host_groups", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_host_groups_priority", "host_groups", ["priority"])
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.execute("DROP INDEX IF EXISTS uq_sync_job_active")
    op.drop_index("ix_sync_jobs_host_module_status", table_name="sync_jobs")
    op.drop_index("ix_host_module_status_host_id", table_name="host_module_status")

    for table in [
        "hosts_entries",
        "cron_jobs",
        "linux_groups",
        "linux_users",
        "package_rules",
        "service_rules",
        "firewall_rules",
    ]:
        if table != "firewall_rules":
            op.drop_index(f"ix_{table}_host_id", table_name=table)
        op.drop_index(f"ix_{table}_group_id", table_name=table)
