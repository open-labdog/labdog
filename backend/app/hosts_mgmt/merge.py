import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import EffectiveHostsEntryResponse
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup


class HostRefUnresolved(ValueError):
    """Raised when a hosts-entry host_ref_id cannot be resolved."""


async def _build_host_ref_lookup(
    db: AsyncSession, entries: list[HostsEntry]
) -> dict[int, tuple[str, str]]:
    """Return {host_id: (ip_address, hostname)} for every referenced host."""
    ref_ids = {e.host_ref_id for e in entries if e.host_ref_id is not None}
    if not ref_ids:
        return {}
    rows = await db.execute(
        select(Host.id, Host.ip_address, Host.hostname).where(Host.id.in_(ref_ids))
    )
    return {row.id: (row.ip_address, row.hostname) for row in rows}


#: Anything that would end the /etc/hosts line a comment sits on.
_SAFE_COMMENT = re.compile(r"[\r\n\x00]")


def _resolve_entry(entry: HostsEntry, ref_lookup: dict[int, tuple[str, str]]) -> tuple[str, str]:
    """Return (effective_ip, effective_hostname) for a HostsEntry."""
    if entry.host_ref_id is not None:
        pair = ref_lookup.get(entry.host_ref_id)
        if not pair or not pair[0] or not pair[1]:
            raise HostRefUnresolved(
                f"hosts entry {entry.id} references host {entry.host_ref_id} "
                "which has no ip or hostname"
            )
        return pair
    return (entry.ip_address or "", entry.hostname or "")


# System entries always injected
SYSTEM_ENTRIES = [
    {"ip_address": "127.0.0.1", "hostname": "localhost", "aliases": [], "comment": None},
    {
        "ip_address": "::1",
        "hostname": "localhost",
        "aliases": ["ip6-localhost", "ip6-loopback"],
        "comment": None,
    },
]


async def get_effective_hosts_entries(
    host_id: int, db: AsyncSession
) -> list[EffectiveHostsEntryResponse]:
    """
    Merge group-level hosts entries + host-level overrides.
    Key = ip_address. Host override replaces group entry entirely.
    Higher priority group wins.
    Always includes system entries (localhost).
    """
    # 1. Start with system entries
    merged: dict[str, EffectiveHostsEntryResponse] = {}
    for sys_entry in SYSTEM_ENTRIES:
        merged[sys_entry["ip_address"]] = EffectiveHostsEntryResponse(
            ip_address=sys_entry["ip_address"],
            hostname=sys_entry["hostname"],
            aliases=sys_entry["aliases"],
            comment=sys_entry["comment"],
            is_system=True,
            source="system",
            source_id=0,
            source_name="system",
        )

    # 2. Query group memberships ordered by priority DESC
    memberships = await db.execute(
        select(
            HostGroupMembership.c.group_id,
            HostGroup.name,
            HostGroup.priority,
        )
        .join(HostGroup, HostGroup.id == HostGroupMembership.c.group_id)
        .where(HostGroupMembership.c.host_id == host_id)
        .order_by(HostGroup.priority.desc())
    )
    groups = memberships.all()

    # 3. For each group (highest priority first), collect entries
    all_group_entries: list[tuple[int, str, HostsEntry]] = []
    for group_id, group_name, _priority in groups:
        result = await db.execute(select(HostsEntry).where(HostsEntry.group_id == group_id))
        for entry in result.scalars().all():
            all_group_entries.append((group_id, group_name, entry))

    # 4. Host overrides
    host_result = await db.execute(select(HostsEntry).where(HostsEntry.host_id == host_id))
    host_entries = list(host_result.scalars().all())

    # Batch-resolve host_ref_id → (ip, hostname)
    ref_lookup = await _build_host_ref_lookup(
        db, [e for _, _, e in all_group_entries] + host_entries
    )

    for group_id, group_name, entry in all_group_entries:
        ip, hostname = _resolve_entry(entry, ref_lookup)
        if ip not in merged:
            merged[ip] = EffectiveHostsEntryResponse(
                ip_address=ip,
                hostname=hostname,
                aliases=entry.aliases or [],
                comment=entry.comment,
                is_system=False,
                source="group",
                source_id=group_id,
                source_name=group_name,
            )

    for entry in host_entries:
        ip, hostname = _resolve_entry(entry, ref_lookup)
        merged[ip] = EffectiveHostsEntryResponse(
            ip_address=ip,
            hostname=hostname,
            aliases=entry.aliases or [],
            comment=entry.comment,
            is_system=False,
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    return sorted(merged.values(), key=lambda e: (not e.is_system, e.ip_address))


def render_hosts_file(entries: list[EffectiveHostsEntryResponse]) -> str:
    """
    Render a complete /etc/hosts file from effective entries.
    System entries first, then sorted by IP.
    """
    lines = ["# Managed by LabDog — do not edit manually"]

    for entry in entries:
        parts = [entry.ip_address, entry.hostname]
        if entry.aliases:
            parts.extend(entry.aliases)
        line = " ".join(parts)
        if entry.comment:
            # Second line of defence. The schema rejects newlines in a
            # comment (SEC-24), but entries also arrive from the GitOps
            # YAML importer, and rows written before that validator
            # existed are still in the database. This is the point where a
            # newline becomes a real /etc/hosts line, so strip rather than
            # raise: a mangled comment is a cosmetic problem, and refusing
            # to render the file would take the whole host's sync down for
            # one bad annotation.
            line += f"  # {_SAFE_COMMENT.sub(' ', entry.comment)}"
        lines.append(line)

    # Ensure trailing newline
    lines.append("")
    return "\n".join(lines)
