"""Keep a host's action transcripts when the host is deleted.

BUG-65 stopped ``DELETE /api/hosts/{id}`` from failing and taught
``action_runs`` to describe its own target, so the parent run survives a
delete with ``target_label`` intact. It did not touch the child table,
and the child table is where the evidence lives:
``action_host_runs.output`` is the actual transcript of what ran. With
``host_id`` still ``ON DELETE CASCADE`` every one of those rows went
with the host, so the surviving run said *what* was targeted and *that*
it failed, and no longer said what happened — usually at exactly the
moment an operator is removing a host *because* something went wrong.

This mirrors BUG-65 one level down:

* ``host_id`` becomes nullable and ``ON DELETE SET NULL``.
* ``hostname`` snapshots the host's name at dispatch time. Denormalised
  deliberately — once the host is gone there is nothing left to join
  to, which is the case the column exists for.

``uq_action_host_run (action_run_id, host_id)`` is deliberately left
alone. PostgreSQL compares NULLs as distinct in a unique constraint
unless it is declared ``NULLS NOT DISTINCT``, so every nulled row is
unique by construction and two hosts deleted from the same run cannot
collide. Re-expressing it would only add a way to get that wrong.

The busy scans in ``app/tasks/host_lock.py`` need no change for the
same reason: they filter ``host_id = :id`` / ``host_id IN (...)``, and
NULL matches neither, so a row whose host is gone is invisible to the
claim protocol — which is correct, a deleted host cannot hold a lock.

Revision ID: 0032_host_run_hostname
Revises: 0031_user_token_version
"""

import sqlalchemy as sa

from alembic import op

revision = "0032_host_run_hostname"
down_revision = "0031_user_token_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_host_runs",
        sa.Column("hostname", sa.String(255), nullable=True),
    )
    # Every existing row still joins — the CASCADE this migration removes
    # guaranteed it — but COALESCE anyway so a database restored from a
    # dump with the FK disabled still ends up with a legible label.
    op.execute(
        """
        UPDATE action_host_runs hr
        SET hostname = COALESCE(
            (SELECT h.hostname FROM hosts h WHERE h.id = hr.host_id),
            'host ' || hr.host_id,
            '(unknown host)'
        )
        """
    )
    op.alter_column(
        "action_host_runs",
        "hostname",
        nullable=False,
        server_default="",
    )

    op.alter_column("action_host_runs", "host_id", nullable=True)
    op.drop_constraint(
        "fk_action_host_runs_host_id_hosts",
        "action_host_runs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_action_host_runs_host_id_hosts",
        "action_host_runs",
        "hosts",
        ["host_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Lossy: rows whose host is gone cannot be represented and are deleted.

    That is the whole point of the upgrade, so a downgrade throws away
    exactly the transcripts it was written to keep. Take a dump first.
    """
    op.execute("DELETE FROM action_host_runs WHERE host_id IS NULL")
    op.drop_constraint(
        "fk_action_host_runs_host_id_hosts",
        "action_host_runs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_action_host_runs_host_id_hosts",
        "action_host_runs",
        "hosts",
        ["host_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("action_host_runs", "host_id", nullable=False)
    op.drop_column("action_host_runs", "hostname")
