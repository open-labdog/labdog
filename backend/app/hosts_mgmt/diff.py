from dataclasses import dataclass, field


@dataclass
class HostsFileDiffEntry:
    ip_address: str
    hostname: str
    aliases: list[str]
    reason: str  # "missing", "hostname_mismatch", "aliases_mismatch", "extra"


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
    Compare current /etc/hosts content against desired effective config.
    Key = ip_address.
    """
    diff = HostsFileDiff()

    current_map = {e.ip_address: e for e in current}
    desired_map = {e.ip_address: e for e in desired}

    # Check desired entries against current
    for ip, desired_entry in desired_map.items():
        current_entry = current_map.get(ip)
        if current_entry is None:
            diff.entries_to_add.append(HostsFileDiffEntry(
                ip_address=ip,
                hostname=desired_entry.hostname,
                aliases=desired_entry.aliases if hasattr(desired_entry, 'aliases') else [],
                reason="missing",
            ))
        else:
            hostname_match = current_entry.hostname == desired_entry.hostname
            desired_aliases = desired_entry.aliases if hasattr(desired_entry, 'aliases') else []
            aliases_match = sorted(current_entry.aliases) == sorted(desired_aliases)

            if not hostname_match and not aliases_match:
                diff.entries_to_update.append(HostsFileDiffEntry(
                    ip_address=ip,
                    hostname=desired_entry.hostname,
                    aliases=desired_aliases,
                    reason="hostname_and_aliases_mismatch",
                ))
            elif not hostname_match:
                diff.entries_to_update.append(HostsFileDiffEntry(
                    ip_address=ip,
                    hostname=desired_entry.hostname,
                    aliases=desired_aliases,
                    reason="hostname_mismatch",
                ))
            elif not aliases_match:
                diff.entries_to_update.append(HostsFileDiffEntry(
                    ip_address=ip,
                    hostname=desired_entry.hostname,
                    aliases=desired_aliases,
                    reason="aliases_mismatch",
                ))
            else:
                diff.entries_in_sync.append(ip)

    # Check for extra entries in current not in desired
    for ip, current_entry in current_map.items():
        if ip not in desired_map:
            diff.entries_to_remove.append(HostsFileDiffEntry(
                ip_address=ip,
                hostname=current_entry.hostname,
                aliases=current_entry.aliases,
                reason="extra",
            ))

    return diff
