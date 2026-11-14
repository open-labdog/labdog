from dataclasses import dataclass, field


@dataclass
class HostsFileDiffEntry:
    ip_address: str
    hostname: str
    aliases: list[str]
    reason: str  # "missing", "hostname_mismatch", "aliases_mismatch", "extra"


def diff_entry_dict(entry: HostsFileDiffEntry) -> dict:
    """A diff entry as the plan and drift responses list it."""
    return {
        "ip_address": entry.ip_address,
        "hostname": entry.hostname,
        "aliases": entry.aliases,
        "reason": entry.reason,
    }


@dataclass
class HostsFileDiff:
    entries_to_add: list[HostsFileDiffEntry] = field(default_factory=list)
    entries_to_remove: list[HostsFileDiffEntry] = field(default_factory=list)
    entries_to_update: list[HostsFileDiffEntry] = field(default_factory=list)
    entries_in_sync: list[str] = field(default_factory=list)  # list of IPs

    @property
    def has_changes(self) -> bool:
        return bool(self.entries_to_add or self.entries_to_remove or self.entries_to_update)


def compute_hosts_diff(
    current: list,  # list[ParsedHostsEntry]
    desired: list,  # list[EffectiveHostsEntryResponse]
) -> HostsFileDiff:
    """
    Compare current /etc/hosts content against desired effective config,
    line by line within each address.

    An empty *desired* is what ``get_effective_hosts_entries`` returns
    when no entry applies, and unbork then leaves the file alone — so
    nothing in it is "extra", and the diff is empty (BUG-115).
    """
    diff = HostsFileDiff()
    if not desired:
        return diff

    # BUG-118: an address may have several lines, so group by address and
    # pair lines within it. One line on each side is compared as before (a
    # changed name is an update); otherwise lines pair up by hostname.
    current_by_ip: dict[str, list] = {}
    for e in current:
        current_by_ip.setdefault(e.ip_address, []).append(e)
    desired_by_ip: dict[str, list] = {}
    for e in desired:
        desired_by_ip.setdefault(e.ip_address, []).append(e)

    for ip, wanted in desired_by_ip.items():
        have = current_by_ip.get(ip, [])
        if len(wanted) == 1 and len(have) == 1:
            _compare_line(diff, ip, have[0], wanted[0])
            continue
        unmatched = list(have)
        for desired_entry in wanted:
            match = next(
                (c for c in unmatched if c.hostname.lower() == desired_entry.hostname.lower()),
                None,
            )
            if match is None:
                diff.entries_to_add.append(
                    HostsFileDiffEntry(
                        ip_address=ip,
                        hostname=desired_entry.hostname,
                        aliases=_aliases(desired_entry),
                        reason="missing",
                    )
                )
                continue
            unmatched.remove(match)
            _compare_line(diff, ip, match, desired_entry)
        for current_entry in unmatched:
            diff.entries_to_remove.append(_extra(current_entry))

    # Check for extra entries in current not in desired
    for ip, have in current_by_ip.items():
        if ip not in desired_by_ip:
            diff.entries_to_remove.extend(_extra(e) for e in have)

    return diff


def _aliases(entry) -> list[str]:
    return entry.aliases if hasattr(entry, "aliases") else []


def _extra(current_entry) -> HostsFileDiffEntry:
    return HostsFileDiffEntry(
        ip_address=current_entry.ip_address,
        hostname=current_entry.hostname,
        aliases=current_entry.aliases,
        reason="extra",
    )


def _compare_line(diff: HostsFileDiff, ip: str, current_entry, desired_entry) -> None:
    hostname_match = current_entry.hostname == desired_entry.hostname
    desired_aliases = _aliases(desired_entry)
    aliases_match = sorted(current_entry.aliases) == sorted(desired_aliases)
    if hostname_match and aliases_match:
        diff.entries_in_sync.append(ip)
        return
    if not hostname_match and not aliases_match:
        reason = "hostname_and_aliases_mismatch"
    elif not hostname_match:
        reason = "hostname_mismatch"
    else:
        reason = "aliases_mismatch"
    diff.entries_to_update.append(
        HostsFileDiffEntry(
            ip_address=ip,
            hostname=desired_entry.hostname,
            aliases=desired_aliases,
            reason=reason,
        )
    )
