"""YAML schema models for Barricade GitOps group configuration.

Top-level key conventions
--------------------------
Each top-level key in a group YAML file corresponds to a module directory name:

    firewall, services, packages, users, linux_groups, cron_jobs,
    resolver, hosts_entries

Missing section semantics
--------------------------
* **Wipe** (list-shaped modules): if a section is absent or ``null``, any
  existing rows for that module are deleted and the module state becomes empty.
  Applies to: ``firewall``, ``services``, ``packages``, ``users``,
  ``linux_groups``, ``cron_jobs``, ``hosts_entries``.
* **Leave alone** (singleton-shaped modules): if a section is absent or
  ``null``, the current DB state is left untouched.
  Applies to: ``resolver``.

Forward compatibility
----------------------
``BarricadeGroupYAML`` is configured with ``extra="allow"``, so unknown
top-level keys are silently ignored.  This lets future module phases add new
sections to YAML files without breaking older importer versions.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FirewallRuleYAML(BaseModel):
    action: Literal["allow", "deny", "reject"]
    protocol: Literal["tcp", "udp", "icmp", "any"]
    direction: Literal["input", "output"]
    source: str | None = None  # CIDR (IPv4 or IPv6)
    dest: str | None = None  # CIDR
    port: int | str | None = None  # int for single, "start-end" string for range
    comment: str | None = None
    system: bool | None = None  # Read but IGNORED on import


class FirewallModuleYAML(BaseModel):
    rules: list[FirewallRuleYAML] = []
    input_policy: Literal["accept", "drop"] | None = None
    output_policy: Literal["accept", "drop"] | None = None


class BarricadeGroupYAML(BaseModel):
    group: str  # Human-readable name
    priority: int | None = None  # Informational
    firewall: FirewallModuleYAML | None = None
    model_config = ConfigDict(extra="allow")  # Ignore unknown top-level keys
