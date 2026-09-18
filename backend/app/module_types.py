"""The canonical name of each configuration module, in one place.

``HostModuleStatus.module_type`` and ``SyncJob.module_type`` are plain
strings, written in one place and read in a dozen. Nothing connected the
writer to the readers, so a single mistyped literal in
``hosts/dependents.py`` wrote ``"hosts_entries"`` where every consumer
reads ``"hosts_file"`` (BUG-68): when a referenced host's IP changed, its
dependants got a new invisible row marked ``out_of_sync`` while the row
they actually read still said ``in_sync`` and their ``/etc/hosts`` kept
pointing at the old address. The firewall half of the same loop was
correct, which is what made it easy to miss.

These constants exist so the next such typo is an ImportError rather than
a silent divergence. They are the *short DB names*; the orchestrator's
own ``module_filter`` uses a different vocabulary, and
``host_sync_orchestrator._MODULE_TYPE_MAPPING`` is the translation
between the two.
"""

from __future__ import annotations

from enum import StrEnum


class ModuleType(StrEnum):
    """Values that may appear in a ``module_type`` column."""

    firewall = "firewall"
    hosts_file = "hosts_file"
    service = "service"
    package = "package"
    cron = "cron"
    linux_user = "linux_user"
    resolver = "resolver"


#: Names that were written to the database by mistake, mapped to what they
#: should have been. Migration ``0028`` repairs the rows; this stays as the
#: record of what the wrong name was.
LEGACY_MODULE_TYPES: dict[str, str] = {
    "hosts_entries": ModuleType.hosts_file,
}
