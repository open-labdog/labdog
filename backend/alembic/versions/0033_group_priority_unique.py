"""Make ``host_groups.priority`` unique.

BUG-69. Every module resolves a host's effective configuration by walking
the groups it belongs to in ``priority DESC`` order and letting the first
occurrence of a merge key win. Two groups sharing a priority therefore
gave a host in both a nondeterministic winner — whatever order Postgres
happened to return the rows in — that could flip between syncs with no
configuration change. Nothing surfaced it: the diff engines compare sets,
so a re-ordering is not "drift".

``POST``/``PATCH /api/groups`` have always rejected a duplicate priority
with 409, but with a read-then-write check that two concurrent requests
can both pass. This is the constraint that actually holds. The merge
engines still spell out a total order (``priority DESC, id ASC``) rather
than leaning on it — see ``app/merge_utils.py``.

Existing duplicates are renumbered rather than rejected: an upgrade that
refuses to run is worse than one that moves a number. The renumbering
preserves the effective order exactly — groups are walked in
``priority DESC, id ASC`` (the order the fixed merge engines produce) and
each one keeps its priority unless that would tie or overtake the group
before it, in which case it drops to the next value down. A group whose
priority is already unique and correctly placed is never touched.

``ck_host_groups_priority_range`` bounds the column to 1..1000. If the
walk runs off the bottom of that range, every group is renumbered from
1000 downwards instead — same order, fresh spacing. That is only
possible with a badly clustered set of priorities, and it is still
strictly better than failing the upgrade. More than 1000 groups cannot
be made unique at all, and does raise.

Downgrade drops the constraint. It cannot recover the duplicate values
that were renumbered, and does not try.

Revision ID: 0033_group_priority_unique
Revises: 0032_host_run_hostname
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "0033_group_priority_unique"
down_revision = "0032_host_run_hostname"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_MIN_PRIORITY = 1
_MAX_PRIORITY = 1000


def _assign(rows: list) -> dict[int, int] | None:
    """Strictly decreasing priorities in walk order, keeping originals where possible.

    Returns ``{group_id: new_priority}`` for the rows that have to move, or
    ``None`` if the walk would fall below ``_MIN_PRIORITY``.
    """
    moves: dict[int, int] = {}
    ceiling = _MAX_PRIORITY
    for row in rows:
        assigned = min(row.priority, ceiling)
        if assigned < _MIN_PRIORITY:
            return None
        if assigned != row.priority:
            moves[row.id] = assigned
        ceiling = assigned - 1
    return moves


def _renumber_all(rows: list) -> dict[int, int]:
    """Last resort: 1000, 999, … in walk order. Same order, fresh spacing."""
    if len(rows) > _MAX_PRIORITY - _MIN_PRIORITY + 1:
        raise RuntimeError(
            f"cannot make host_groups.priority unique: {len(rows)} groups do not fit "
            f"in {_MIN_PRIORITY}..{_MAX_PRIORITY}"
        )
    return {
        row.id: _MAX_PRIORITY - offset
        for offset, row in enumerate(rows)
        if row.priority != _MAX_PRIORITY - offset
    }


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, name, priority FROM host_groups ORDER BY priority DESC, id ASC")
    ).fetchall()

    moves = _assign(rows)
    if moves is None:
        logger.warning(
            "BUG-69: host_groups.priority is too tightly clustered to deduplicate "
            "in place; renumbering every group from %s downwards",
            _MAX_PRIORITY,
        )
        moves = _renumber_all(rows)

    by_id = {row.id: row for row in rows}
    for group_id, new_priority in moves.items():
        row = by_id[group_id]
        logger.warning(
            "BUG-69: group %r (id=%s) priority %s -> %s",
            row.name,
            group_id,
            row.priority,
            new_priority,
        )

    # The unique constraint does not exist yet, so the intermediate states
    # of this loop are allowed to hold duplicates; every assigned value is
    # inside ck_host_groups_priority_range, which is the only constraint
    # that applies while it runs.
    for group_id, new_priority in moves.items():
        conn.execute(
            sa.text("UPDATE host_groups SET priority = :p WHERE id = :i"),
            {"p": new_priority, "i": group_id},
        )

    op.create_unique_constraint("uq_host_groups_priority", "host_groups", ["priority"])


def downgrade() -> None:
    op.drop_constraint("uq_host_groups_priority", "host_groups", type_="unique")
