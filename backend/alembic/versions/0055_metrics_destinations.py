"""Per-host and per-group metrics destinations.

Hosts and groups can name the Mimir and Loki instances their Alloy ships
to. A host's own choice wins, then its highest-priority group's, then the
default instance, which is what every host used before this revision, so
existing installs behave as they did.

No ``ON DELETE`` action: deleting an instance a host or group still uses
is refused by the API, because silently falling back to the default would
point the host page at a backend the host isn't shipping to.

Revision ID: 0055_metrics_destinations
Revises: 0054_action_host_runs_host_id
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0055_metrics_destinations"
down_revision = "0054_action_host_runs_host_id"
branch_labels = None
depends_on = None

_TABLES = ("hosts", "host_groups")
_KINDS = ("mimir", "loki")


def upgrade() -> None:
    for table in _TABLES:
        for kind in _KINDS:
            column = f"{kind}_instance_id"
            op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))
            op.create_foreign_key(
                f"fk_{table}_{column}",
                table,
                "grafana_instances",
                [column],
                ["id"],
            )


def downgrade() -> None:
    for table in _TABLES:
        for kind in _KINDS:
            column = f"{kind}_instance_id"
            op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
            op.drop_column(table, column)
