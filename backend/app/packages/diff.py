"""Package drift diff engine — compare desired vs actual package states."""

import fnmatch
from dataclasses import dataclass, field


@dataclass
class PackageEntry:
    package_name: str
    desired_state: str  # "present", "absent", "latest"
    desired_version: str | None
    actual_state: str  # "present" or "absent"
    actual_version: str | None
    desired_hold: bool = False
    actual_hold: bool = False


@dataclass
class PackageDiff:
    to_install: list[PackageEntry] = field(default_factory=list)
    to_remove: list[PackageEntry] = field(default_factory=list)
    to_upgrade: list[PackageEntry] = field(default_factory=list)
    # Package is present at the desired version but its hold/versionlock state
    # differs from desired — a sync would run apt-mark hold/unhold (or dnf
    # versionlock add/delete), so this is genuine drift, not in_sync.
    to_hold_change: list[PackageEntry] = field(default_factory=list)
    in_sync: list[PackageEntry] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.to_install or self.to_remove or self.to_upgrade or self.to_hold_change)


def compute_diff(
    desired: list[dict],
    actual: list[dict],
) -> PackageDiff:
    diff = PackageDiff()

    actual_by_name = {a["name"]: a for a in actual}

    for desired_pkg in desired:
        name = desired_pkg["package_name"]
        desired_state = desired_pkg.get("state", "present")
        desired_version = desired_pkg.get("version")

        actual_pkg = actual_by_name.get(name, {"name": name, "state": "absent", "version": None})
        actual_state = actual_pkg.get("state", "absent")
        actual_version = actual_pkg.get("version")

        entry = PackageEntry(
            package_name=name,
            desired_state=desired_state,
            desired_version=desired_version,
            actual_state=actual_state,
            actual_version=actual_version,
            desired_hold=desired_pkg.get("hold", False),
            actual_hold=actual_pkg.get("hold", False),
        )

        if desired_state == "absent":
            if actual_state == "absent":
                diff.in_sync.append(entry)
            else:
                diff.to_remove.append(entry)

        elif desired_state == "latest":
            if actual_state == "present":
                _classify_present(diff, entry)
            else:
                diff.to_install.append(entry)

        else:
            if actual_state == "absent":
                diff.to_install.append(entry)
            elif desired_version is None or _version_matches(actual_version, desired_version):
                _classify_present(diff, entry)
            else:
                diff.to_upgrade.append(entry)

    return diff


def _classify_present(diff: PackageDiff, entry: PackageEntry) -> None:
    """A package present at the desired version: in_sync unless hold drifts.

    Hold only applies to present packages (the generator skips absent ones),
    so this is the single place where a hold-only difference is detected.
    """
    if entry.desired_hold != entry.actual_hold:
        diff.to_hold_change.append(entry)
    else:
        diff.in_sync.append(entry)


def _version_matches(actual_version: str | None, desired_version: str) -> bool:
    """Supports exact match and shell glob patterns (e.g. '1.24.*'). No >= operators."""
    if actual_version is None:
        return False
    if fnmatch.fnmatch(actual_version, desired_version):
        return True
    return actual_version.lower() == desired_version.lower()
