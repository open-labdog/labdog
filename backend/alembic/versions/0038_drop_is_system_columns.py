"""Drop the ``is_system`` columns nothing ever wrote.

``firewall_rules.is_system`` and ``hosts_entries.is_system`` have existed
since ``0001`` and no code path has ever set either to ``true``. The
create schemas do not accept the field (deliberately — a client must not
be able to mint a row the API then refuses to let it edit), the two
GitOps importers strip ``system: true`` out of incoming YAML before a row
is built, and the only "system" rules and entries LabDog has are
synthesised at merge time and never persisted.

So five API guards, four importer filters and two UI branches protected
rows that could only exist if someone edited the database by hand. The
e2e test covering the disabled buttons was deleted for exactly that
reason: it needed ``docker exec … psql`` to produce its fixture.

What survives is the concept, which lives where it is real: the
``is_system`` field on ``FirewallRuleSpec`` and the effective-rule /
effective-entry responses, set by ``app.rules.merge._make_ssh_lockout_rule``
and ``app.hosts_mgmt.merge.SYSTEM_ENTRIES``. The anti-lockout rule is
derived per host — ``host_source_ip`` takes precedence over the
configured server IP — so it was never a row to begin with.

Downgrade re-adds both columns defaulting to ``false``, which restores
the schema exactly for every row this migration can have seen. It cannot
restore a hand-set ``true``; upgrade logs a warning naming the count if
any exist, so the loss is visible rather than silent.

Revision ID: 0038_drop_is_system_columns
Revises: 0037_unify_module_sync_status
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "0038_drop_is_system_columns"
down_revision = "0037_unify_module_sync_status"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _warn_if_set(table: str) -> None:
    """Say so before dropping a hand-set flag.

    Unreachable through any supported interface, so this should always be
    zero. If it is not, an operator set it directly and is about to lose
    it — that belongs in the upgrade log, not in a silent DDL statement.
    """
    sql = sa.text(f"SELECT count(*) FROM {table} WHERE is_system")  # noqa: S608
    count = op.get_bind().execute(sql).scalar_one()
    if count:
        logger.warning(
            "%s: %d row(s) have is_system = true and will lose that flag. "
            "No LabDog code path sets it, so these were set directly in the "
            "database; they become ordinary editable rows.",
            table,
            count,
        )


def upgrade() -> None:
    for table in ("firewall_rules", "hosts_entries"):
        _warn_if_set(table)
        op.drop_column(table, "is_system")


def downgrade() -> None:
    for table in ("firewall_rules", "hosts_entries"):
        op.add_column(
            table,
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
