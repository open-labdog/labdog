"""Describe an action run's target independently of the FKs.

Deleting a host with ad-hoc run history fails outright. ``action_runs``
carries ``host_id``/``group_id``/``scheduled_action_id`` as
``ON DELETE SET NULL`` FKs under ``ck_action_runs_scope``, which forbids
all three being NULL — which is exactly the state ``SET NULL`` produces
for a run that targeted only the host being deleted. So
``DELETE /api/hosts/{id}`` raises

    new row for relation "action_runs" violates check constraint
    "ck_action_runs_ck_action_runs_scope"

and the host cannot be removed short of manual SQL.

``ON DELETE CASCADE`` would fix it by destroying the audit trail at the
moment an operator most needs it — ``action_runs.output`` is frequently
the only record of what was run against a host that is being removed
*because* something went wrong. Instead the run now says what it targeted
in its own columns:

* ``target_kind`` — ``host`` | ``group`` | ``fleet``, mirroring
  ``scheduled_actions.target_kind``.
* ``target_label`` — the hostname or group name as it stood at dispatch
  time. Denormalised on purpose: after the delete there is nothing left
  to join to, and that is the case the column exists for.

The FK columns stay, still ``SET NULL``, and remain the source of truth
for links while the target exists. The old CHECK is replaced by one on
``target_kind`` alone, which no delete can violate.

The backfill reads the current name where the row still joins and falls
back to a legible placeholder where it does not. Rows that are already
all-NULL — impossible under the old CHECK, but a database restored from
before it existed may have them — become ``fleet``.

Revision ID: 0026_run_target_kind
Revises: 0025_action_pack_trusted
"""

import sqlalchemy as sa

from alembic import op

revision = "0026_run_target_kind"
down_revision = "0025_action_pack_trusted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("action_runs", sa.Column("target_kind", sa.String(8), nullable=True))
    op.add_column("action_runs", sa.Column("target_label", sa.String(255), nullable=True))

    op.execute(
        """
        UPDATE action_runs r SET
            target_kind = CASE
                WHEN r.host_id IS NOT NULL THEN 'host'
                WHEN r.group_id IS NOT NULL THEN 'group'
                ELSE 'fleet'
            END,
            target_label = COALESCE(
                (SELECT h.hostname FROM hosts h WHERE h.id = r.host_id),
                (SELECT g.name FROM host_groups g WHERE g.id = r.group_id),
                CASE
                    WHEN r.host_id IS NOT NULL THEN 'host ' || r.host_id
                    WHEN r.group_id IS NOT NULL THEN 'group ' || r.group_id
                    ELSE 'All hosts'
                END
            )
        """
    )

    op.alter_column("action_runs", "target_kind", nullable=False)
    op.alter_column("action_runs", "target_label", nullable=False)

    # Raw SQL, not op.drop_constraint: the metadata naming convention
    # would prefix `ck_action_runs_` onto a name that already carries it
    # (0001 rendered it doubled) and look for a constraint that does not
    # exist.
    op.execute("ALTER TABLE action_runs DROP CONSTRAINT ck_action_runs_ck_action_runs_scope")
    op.execute(
        "ALTER TABLE action_runs ADD CONSTRAINT ck_action_runs_ck_action_runs_target_kind "
        "CHECK (target_kind IN ('host', 'group', 'fleet'))"
    )


def downgrade() -> None:
    """Lossy: rows whose target has since been deleted cannot satisfy the
    old CHECK, because the information that would satisfy it is the
    information this migration added. Those rows are deleted — there is
    no value of ``host_id`` that is both honest and permitted."""
    op.execute("ALTER TABLE action_runs DROP CONSTRAINT ck_action_runs_ck_action_runs_target_kind")
    op.execute(
        """
        DELETE FROM action_runs
        WHERE host_id IS NULL AND group_id IS NULL AND scheduled_action_id IS NULL
        """
    )
    op.execute(
        "ALTER TABLE action_runs ADD CONSTRAINT ck_action_runs_ck_action_runs_scope CHECK ("
        "(host_id IS NOT NULL AND group_id IS NULL) OR "
        "(host_id IS NULL AND group_id IS NOT NULL) OR "
        "(host_id IS NULL AND group_id IS NULL AND scheduled_action_id IS NOT NULL))"
    )
    op.drop_column("action_runs", "target_label")
    op.drop_column("action_runs", "target_kind")
