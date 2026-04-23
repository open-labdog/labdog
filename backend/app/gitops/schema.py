"""YAML schema models for LabDog GitOps group configuration.

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
``LabDogGroupYAML`` is configured with ``extra="allow"``, so unknown
top-level keys are silently ignored.  This lets future module phases add new
sections to YAML files without breaking older importer versions.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.resolver.schemas import ALLOWED_OPTIONS, _validate_dns_name, _validate_ip

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
                raise ValueError("ip_address and hostname must be empty when host_ref_id is set")
        else:
            if not self.ip_address or not self.hostname:
                raise ValueError("ip_address and hostname are required when host_ref_id is not set")
        return self


class ResolverYAML(BaseModel):
    """YAML model for DNS resolver configuration.

    Mirrors :class:`app.resolver.schemas.ResolverConfigCreate` exactly —
    same fields, same validators.  Validator helpers are imported from that
    module to avoid duplicating logic.

    Missing-section semantics for this module are **leave-alone**: if the
    ``resolver:`` key is absent or ``null`` in the YAML file the current DB
    state is preserved unchanged.  Explicit deletion requires the UI /
    DELETE endpoint.
    """

    nameservers: list[str]
    search_domains: list[str] = []
    options: dict[str, int | str] = {}
    resolver_type: Literal["resolv_conf", "systemd_resolved", "networkmanager"] = "resolv_conf"
    dns_over_tls: bool = False

    @field_validator("nameservers")
    @classmethod
    def validate_nameservers(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one nameserver is required")
        if len(v) > 3:
            raise ValueError("Maximum 3 nameservers allowed (resolv.conf limit)")
        return [_validate_ip(ns) for ns in v]

    @field_validator("search_domains")
    @classmethod
    def validate_search_domains(cls, v: list[str]) -> list[str]:
        if len(v) > 6:
            raise ValueError("Maximum 6 search domains allowed (resolv.conf limit)")
        return [_validate_dns_name(d) for d in v]

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: dict[str, int | str]) -> dict[str, int | str]:
        for key, val in v.items():
            if key not in ALLOWED_OPTIONS:
                raise ValueError(
                    f"Unknown option '{key}'. Allowed: {', '.join(sorted(ALLOWED_OPTIONS))}"
                )
            if key in ("ndots", "timeout", "attempts"):
                if not isinstance(val, int) or val < 0 or val > 15:
                    raise ValueError(f"Option '{key}' must be int 0-15, got {val}")
        return v

    @model_validator(mode="after")
    def check_dns_over_tls(self) -> "ResolverYAML":
        if self.dns_over_tls and self.resolver_type != "systemd_resolved":
            self.dns_over_tls = False  # silently ignore for non-systemd-resolved
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


class LinuxGroupYAML(BaseModel):
    """YAML model for a single Linux group entry.

    Structural shape only — full validation is delegated to
    ``LinuxGroupCreate.model_validate()`` in the handler so we don't duplicate
    logic (protected-name check, gid range, username regex).
    """

    groupname: str
    gid: int | None = None
    state: Literal["present", "absent"] = "present"
    priority: int = Field(default=0, ge=0, le=10000)


class LinuxUserYAML(BaseModel):
    """YAML model for a single Linux user entry.

    Structural shape only — full validation is delegated to
    ``LinuxUserCreate.model_validate()`` in the handler so we don't duplicate
    logic (protected-name check, uid range, SSH key validation, sudo_rule
    metacharacter check).
    """

    username: str
    uid: int | None = None
    shell: str = "/bin/bash"
    home_dir: str | None = None
    state: Literal["present", "absent"] = "present"
    comment: str | None = None
    sudo_rule: str | None = None
    authorized_keys: list[str] = []
    supplementary_groups: list[str] = []
    priority: int = Field(default=0, ge=0, le=10000)


class LabDogGroupYAML(BaseModel):
    group: str  # Human-readable name
    priority: int | None = None  # Informational
    firewall: FirewallModuleYAML | None = None
    services: list[ServiceYAML] | None = None
    packages: list[PackageYAML] | None = None
    package_repositories: list[PackageRepositoryYAML] | None = None
    hosts_entries: list[HostsEntryYAML] | None = None
    cron_jobs: list[CronJobYAML] | None = None
    resolver: ResolverYAML | None = None
    users: list[LinuxUserYAML] | None = None
    linux_groups: list[LinuxGroupYAML] | None = None
    model_config = ConfigDict(extra="allow")  # Ignore unknown top-level keys
