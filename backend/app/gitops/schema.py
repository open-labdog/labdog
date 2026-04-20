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

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_USER_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$")


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


class ServiceYAML(BaseModel):
    service_name: str
    state: Literal["running", "stopped"]
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=10000)
    comment: str | None = None
    unit_content: str | None = None
    deploy_mode: Literal["full", "override"] = "override"


class PackageYAML(BaseModel):
    package_name: str
    version: str | None = None
    state: Literal["present", "absent", "latest"] = "present"
    package_manager: Literal["auto", "apt", "dnf", "yum"] = "auto"
    priority: int = Field(default=0, ge=0, le=10000)
    comment: str | None = None
    hold: bool = False


class PackageRepositoryYAML(BaseModel):
    name: str
    url: str
    key_url: str | None = None
    repo_type: Literal["apt", "yum"]
    distribution: str | None = None
    components: str | None = None
    state: Literal["present", "absent"] = "present"


class HostsEntryYAML(BaseModel):
    """YAML model for a single /etc/hosts entry.

    Two mutually exclusive variants:

    * **Literal** — ``ip_address`` + ``hostname`` are required; ``host_ref_id`` must be absent.
    * **Reference** — ``host_ref_id`` is required; ``ip_address`` and ``hostname`` must be absent.

    ``aliases`` is an optional list of additional hostnames for the same IP.
    ``priority`` controls emission order in the rendered ``/etc/hosts`` file (higher = earlier);
    YAML list order is informational only — the drift detector and emission engine both use
    priority, not YAML position.
    """

    ip_address: str | None = None
    hostname: str | None = None
    host_ref_id: int | None = None
    aliases: list[str] = []
    comment: str | None = None
    priority: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def _validate_ref_or_literal(self) -> "HostsEntryYAML":
        if self.host_ref_id is not None:
            if self.ip_address or self.hostname:
                raise ValueError(
                    "ip_address and hostname must be empty when host_ref_id is set"
                )
        else:
            if not self.ip_address or not self.hostname:
                raise ValueError(
                    "ip_address and hostname are required when host_ref_id is not set"
                )
        return self


class CronJobYAML(BaseModel):
    name: str
    user: str = "root"
    schedule: str
    command: str
    environment: dict[str, str] = {}
    state: Literal["present", "absent"] = "present"
    priority: int = Field(default=0, ge=0, le=10000)
    comment: str | None = None

    @field_validator("user")
    @classmethod
    def validate_user(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("user must not be empty")
        if not _USER_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid user '{v}': must match [a-zA-Z0-9_][a-zA-Z0-9_.-]* "
                "and be at most 32 characters (no shell metacharacters)"
            )
        return v


class BarricadeGroupYAML(BaseModel):
    group: str  # Human-readable name
    priority: int | None = None  # Informational
    firewall: FirewallModuleYAML | None = None
    services: list[ServiceYAML] | None = None
    packages: list[PackageYAML] | None = None
    package_repositories: list[PackageRepositoryYAML] | None = None
    hosts_entries: list[HostsEntryYAML] | None = None
    cron_jobs: list[CronJobYAML] | None = None
    model_config = ConfigDict(extra="allow")  # Ignore unknown top-level keys
