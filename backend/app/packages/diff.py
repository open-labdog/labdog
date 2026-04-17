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
    in_sync: list[PackageEntry] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.to_install or self.to_remove or self.to_upgrade)


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
                diff.in_sync.append(entry)
            else:
                diff.to_install.append(entry)

        else:
            if actual_state == "absent":
                diff.to_install.append(entry)
            elif desired_version is None:
                diff.in_sync.append(entry)
            elif _version_matches(actual_version, desired_version):
                diff.in_sync.append(entry)
            else:
                diff.to_upgrade.append(entry)

    return diff


def _version_matches(actual_version: str | None, desired_version: str) -> bool:
    """Supports exact match and shell glob patterns (e.g. '1.24.*'). No >= operators."""
    if actual_version is None:
        return False
    if fnmatch.fnmatch(actual_version, desired_version):
        return True
    return actual_version.lower() == desired_version.lower()
