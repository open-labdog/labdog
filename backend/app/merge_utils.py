"""Deterministic ordering for the group-inheritance merge engines (BUG-69).

Every module — firewall, cron, packages, services, users, hosts entries,
CA certs, resolver — resolves a host's effective configuration by walking
the groups the host belongs to, highest priority first, and letting the
first occurrence of a merge key win. Ordering by ``priority`` alone leaves
the winner to whatever order Postgres happened to return the rows in, so
two groups sharing a priority produced a winner that could flip between
syncs with no configuration change at all. Nothing surfaced the flip: the
diff engines compare sets, so a re-ordering is not "drift".

``host_groups.priority`` is now unique (migration ``0033``), which removes
the tie in practice, but the ordering is still spelled out in full here —
a total order that does not depend on a constraint holding is cheaper than
one that does, and ``id ASC`` gives the only tiebreak an operator can
reason about without another column: the group created first wins.

The same reasoning applies one level down, to the rules *inside* a group.
Those selects order by ``priority DESC, id ASC`` at their call sites; the
per-group rule tables have no unique constraint on the merge key, and for
firewall rules the emitted order is the first-match order of the ruleset.
"""

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup


def ordered_groups_for_host(host_id: int) -> Select:
    """Select ``(group_id, name, priority)`` for a host's groups, in merge order.

    Highest priority first, ties broken by group id ascending.
    """
    return (
        select(
            HostGroupMembership.c.group_id,
            HostGroup.name,
            HostGroup.priority,
        )
        .join(HostGroup, HostGroup.id == HostGroupMembership.c.group_id)
        .where(HostGroupMembership.c.host_id == host_id)
        .order_by(HostGroup.priority.desc(), HostGroup.id.asc())
    )


@dataclass(frozen=True)
class OwnedRow[T]:
    """One config row plus where a host inherited it from.

    ``source`` is ``"host"`` or ``"group"``; ``source_id`` and
    ``source_name`` are the host id and ``"host override"``, or the group
    id and its name. Every effective-* schema carries these three fields,
    which is why the merge has to keep provenance rather than just rows.
    """

    row: T
    source: str
    source_id: int
    source_name: str


def _merge_order(model: type) -> tuple:
    """ORDER BY for a config table, most-preferred row first.

    Highest priority first, ties by id ascending — the same total order
    ``ordered_groups_for_host`` applies one level up. ``CACertRule`` has no
    priority column (its merge is a union by fingerprint, not a contest),
    so it orders by id alone.
    """
    if hasattr(model, "priority"):
        return (model.priority.desc(), model.id.asc())
    return (model.id.asc(),)


async def load_owned_rows[T](db: AsyncSession, host_id: int, model: type[T]) -> list[OwnedRow[T]]:
    """Every row a host inherits from ``model``, most-preferred first.

    Host overrides lead, then group rows with the highest-priority group's
    first. Feed the result to ``first_wins`` to collapse it: because host
    rows sort ahead of every group row, one first-wins pass produces
    exactly what the modules used to build in two — group rows into a dict,
    then host rows over the top.

    Three queries whatever the group count. The modules each ran a SELECT
    per group inside the loop, which is the N+1 ``rules/desired_state.py``
    was written to avoid; the ``IN`` query below returns rows already in
    merge order, so bucketing them by group preserves it.
    """
    order = _merge_order(model)

    host_rows = (
        (await db.execute(select(model).where(model.host_id == host_id).order_by(*order)))
        .scalars()
        .all()
    )
    owned: list[OwnedRow[T]] = [
        OwnedRow(row, "host", host_id, "host override") for row in host_rows
    ]

    groups = (await db.execute(ordered_groups_for_host(host_id))).all()
    if not groups:
        return owned

    group_ids = [group_id for group_id, _name, _priority in groups]
    group_rows = (
        (await db.execute(select(model).where(model.group_id.in_(group_ids)).order_by(*order)))
        .scalars()
        .all()
    )
    by_group: dict[int, list[T]] = {}
    for row in group_rows:
        by_group.setdefault(row.group_id, []).append(row)

    for group_id, group_name, _priority in groups:
        for row in by_group.get(group_id, ()):
            owned.append(OwnedRow(row, "group", group_id, group_name))

    return owned


def first_wins[T](owned: Iterable[OwnedRow[T]], key: Callable[[T], Hashable]) -> list[OwnedRow[T]]:
    """Keep the first row for each merge key, discarding the rest.

    ``load_owned_rows`` has already put the input in precedence order, so
    "first" is "wins" — one pass settles both the host-over-group question
    and the highest-priority-wins question.
    """
    seen: set[Hashable] = set()
    kept: list[OwnedRow[T]] = []
    for item in owned:
        merge_key = key(item.row)
        if merge_key in seen:
            continue
        seen.add(merge_key)
        kept.append(item)
    return kept
