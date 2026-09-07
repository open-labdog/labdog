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

from sqlalchemy import Select, select

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
