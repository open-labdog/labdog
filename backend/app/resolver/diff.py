from dataclasses import dataclass


@dataclass
class ResolverDiff:
    nameservers_changed: bool
    search_domains_changed: bool
    options_changed: bool
    current: dict | None
    desired: dict | None

    @property
    def has_changes(self) -> bool:
        return self.nameservers_changed or self.search_domains_changed or self.options_changed


def compute_resolver_diff(current: dict | None, desired: dict | None) -> ResolverDiff:
    if current is None and desired is None:
        return ResolverDiff(False, False, False, None, None)
    if current is None or desired is None:
        return ResolverDiff(True, True, True, current, desired)

    return ResolverDiff(
        nameservers_changed=current.get("nameservers", []) != desired.get("nameservers", []),
        search_domains_changed=current.get("search_domains", [])
        != desired.get("search_domains", []),
        options_changed=current.get("options", {}) != desired.get("options", {}),
        current=current,
        desired=desired,
    )
