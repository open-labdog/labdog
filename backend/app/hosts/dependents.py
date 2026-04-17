"""Track which hosts have merged firewall rules / hosts-entries that reference
a given Host, and invalidate their module status when that host's IP changes.

A rule or entry can reference a host either via direct scope (host_id) or
group membership — e.g. a rule on group G with source_host_id=H affects
every host that belongs to G.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import distinct, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hosts_mgmt.models import HostsEntry
from app.models.firewall_rule import FirewallRule
from app.models.host import HostGroupMembership
from app.models.host_module_status import HostModuleStatus


@dataclass
class HostDependents:
    rule_ids: list[int]
    hosts_entry_ids: list[int]

    @property
    def empty(self) -> bool:
        return not self.rule_ids and not self.hosts_entry_ids


async def get_host_dependents(db: AsyncSession, host_id: int) -> HostDependents:
    """Return IDs of rules and hosts-entries that reference the given host."""
    rule_rows = await db.execute(
        select(FirewallRule.id).where(
            or_(
                FirewallRule.source_host_id == host_id,
                FirewallRule.destination_host_id == host_id,
            )
        )
    )
    entry_rows = await db.execute(select(HostsEntry.id).where(HostsEntry.host_ref_id == host_id))
    return HostDependents(
        rule_ids=[r[0] for r in rule_rows.all()],
        hosts_entry_ids=[r[0] for r in entry_rows.all()],
    )


async def affected_host_ids(db: AsyncSession, host_id: int) -> tuple[set[int], set[int]]:
    """Return (hosts_with_firewall_dep, hosts_with_hosts_entry_dep).

    A dependent host is any host whose effective (merged) config includes a
    rule or entry referencing `host_id` — either via direct host-scoped rule
    or via any of its groups.
    """
    # Firewall: rule scoped directly to a host
    fw_direct = await db.execute(
        select(distinct(FirewallRule.host_id)).where(
            FirewallRule.host_id.is_not(None),
            or_(
                FirewallRule.source_host_id == host_id,
                FirewallRule.destination_host_id == host_id,
            ),
        )
    )
    fw_hosts: set[int] = {r[0] for r in fw_direct.all() if r[0] is not None}

    # Firewall: rule scoped to a group → every host in that group
    fw_group_ids = await db.execute(
        select(distinct(FirewallRule.group_id)).where(
            FirewallRule.group_id.is_not(None),
            or_(
                FirewallRule.source_host_id == host_id,
                FirewallRule.destination_host_id == host_id,
            ),
        )
    )
    fw_groups = [r[0] for r in fw_group_ids.all() if r[0] is not None]
    if fw_groups:
        rows = await db.execute(
            select(distinct(HostGroupMembership.c.host_id)).where(
                HostGroupMembership.c.group_id.in_(fw_groups)
            )
        )
        fw_hosts.update(r[0] for r in rows.all())

    # Hosts entries: direct
    he_direct = await db.execute(
        select(distinct(HostsEntry.host_id)).where(
            HostsEntry.host_id.is_not(None),
            HostsEntry.host_ref_id == host_id,
        )
    )
    he_hosts: set[int] = {r[0] for r in he_direct.all() if r[0] is not None}

    # Hosts entries: via group
    he_group_ids = await db.execute(
        select(distinct(HostsEntry.group_id)).where(
            HostsEntry.group_id.is_not(None),
            HostsEntry.host_ref_id == host_id,
        )
    )
    he_groups = [r[0] for r in he_group_ids.all() if r[0] is not None]
    if he_groups:
        rows = await db.execute(
            select(distinct(HostGroupMembership.c.host_id)).where(
                HostGroupMembership.c.group_id.in_(he_groups)
            )
        )
        he_hosts.update(r[0] for r in rows.all())

    return fw_hosts, he_hosts


async def invalidate_host_ref_dependents(db: AsyncSession, host_id: int) -> dict:
    """Mark module status dirty on every host whose config depends on host_id.

    Returns a summary dict of {module_type: [host_ids]}.
    Caller is responsible for commit.
    """
    fw_hosts, he_hosts = await affected_host_ids(db, host_id)
    # A host whose merged firewall config includes this ref → firewall module dirty
    # Same for /etc/hosts.
    for module_type, host_ids in (("firewall", fw_hosts), ("hosts_entries", he_hosts)):
        for hid in host_ids:
            hms = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.host_id == hid,
                    HostModuleStatus.module_type == module_type,
                )
            )
            row = hms.scalar_one_or_none()
            if row is None:
                row = HostModuleStatus(host_id=hid, module_type=module_type)
                db.add(row)
            row.sync_status = "out_of_sync"
    return {"firewall": sorted(fw_hosts), "hosts_entries": sorted(he_hosts)}
