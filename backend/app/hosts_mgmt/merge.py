import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import EffectiveHostsEntryResponse
from app.merge_utils import first_wins, load_owned_rows
from app.models.host import Host


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
    """Merge group-level hosts entries + host-level overrides.

    Merge key: the resolved ``ip_address``. Host override replaces a group
    entry entirely; among rows at the same level the highest priority
    wins. The two system entries are seeded first and nothing may displace
    them.

    Unlike its sibling modules the key cannot be read straight off the
    row: an entry may name a ``host_ref_id`` instead of an address, so
    every candidate is resolved — in one batched query — before the
    first-wins pass runs.
    """
    system: dict[str, EffectiveHostsEntryResponse] = {
        entry["ip_address"]: EffectiveHostsEntryResponse(
            ip_address=entry["ip_address"],
            hostname=entry["hostname"],
            aliases=entry["aliases"],
            comment=entry["comment"],
            priority=0,
            is_system=True,
            source="system",
            source_id=0,
            source_name="system",
        )
        for entry in SYSTEM_ENTRIES
    }

    owned = await load_owned_rows(db, host_id, HostsEntry)
    ref_lookup = await _build_host_ref_lookup(db, [o.row for o in owned])
    # Resolve every candidate, winner or not: a dangling host_ref_id is a
    # broken configuration whether or not that entry would have survived
    # the merge, and it raised here before the extraction too.
    resolved = {o.row.id: _resolve_entry(o.row, ref_lookup) for o in owned}

    winners = first_wins(owned, key=lambda e: resolved[e.id][0])

    merged = dict(system)
    for owner in winners:
        ip, hostname = resolved[owner.row.id]
        # A group may not displace a system entry, but a host override
        # may — that asymmetry is how it behaved before this extraction,
        # and someone pinning their own 127.0.0.1 line is doing it
        # deliberately at the level where deliberate is the only option.
        if owner.source == "group" and ip in system:
            continue
        merged[ip] = EffectiveHostsEntryResponse(
            ip_address=ip,
            hostname=hostname,
            aliases=owner.row.aliases or [],
            comment=owner.row.comment,
            priority=owner.row.priority,
            is_system=False,
            source=owner.source,
            source_id=owner.source_id,
            source_name=owner.source_name,
        )

    # System entries first, then highest priority first — `/etc/hosts` is
    # read top to bottom and the first match for a name wins, so this is
    # what settles two entries that share a hostname (BUG-57). The IP is
    # the final tie-break, only so the file is stable when priorities are
    # equal; it is a string compare, and never meant more than that.
    return sorted(merged.values(), key=lambda e: (not e.is_system, -e.priority, e.ip_address))


def render_hosts_file(entries: list[EffectiveHostsEntryResponse]) -> str:
    """
    Render a complete /etc/hosts file from effective entries.

    Emitted in the order given, which ``get_effective_hosts_entries``
    has already settled: system entries first, then highest priority
    first. Order is not cosmetic here — the file is read top to bottom
    and the first line matching a name wins.
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
