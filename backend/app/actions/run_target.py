"""Describe what an action run targeted, in terms that outlive the target.

``action_runs`` links to its target through ``ON DELETE SET NULL`` FKs,
so deleting a host erases the only description an ad-hoc run had of what
it ran against — and ``action_runs.output`` is often the only surviving
record of what happened on a host that is being removed *because*
something went wrong.

:func:`describe_target` resolves the pair written into
``ActionRun.target_kind`` / ``target_label`` at dispatch time. The label
is a snapshot: a host renamed after the run keeps the name it had when
the run was dispatched, which is the more useful answer for an audit
reader and the only possible one once the row is gone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import Host
from app.models.host_group import HostGroup

#: Shown for a fleet-wide run, which has no row to name.
FLEET_LABEL = "All hosts"


async def describe_target(
    db: AsyncSession,
    *,
    host_id: int | None = None,
    group_id: int | None = None,
) -> tuple[str, str]:
    """Return ``(target_kind, target_label)`` for a run being created.

    Neither id set means a fleet run. A target that cannot be resolved
    still gets a label — the caller has already validated the id, and a
    run refused at insert time because its name lookup lost a race would
    be a worse failure than an approximate label.
    """
    if host_id is not None:
        hostname = await db.scalar(select(Host.hostname).where(Host.id == host_id))
        return "host", hostname or f"host {host_id}"
    if group_id is not None:
        name = await db.scalar(select(HostGroup.name).where(HostGroup.id == group_id))
        return "group", name or f"group {group_id}"
    return "fleet", FLEET_LABEL
