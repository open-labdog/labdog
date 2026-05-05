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
  Applies to: ``resolver``, ``workflow``.

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


class ScheduledActionYAML(BaseModel):
    """YAML model for one entry in the per-group ``scheduled_actions:`` list.

    Replaces the legacy ``workflow:`` singleton. Multiple entries are
    allowed per group — one per (action_key) — since the unified
    ``ScheduledAction`` model lifts the singleton-per-group constraint
    that ``UpdateWorkflow`` carried.

    The importer applies **list-shaped, leave-alone-on-absence**
    semantics: section absent ⇒ DB rows untouched; section present
    (even an empty list ``[]``) ⇒ delete-and-replace among rows where
    ``target_kind='group' AND target_id=<this group>``.
    """

    action_key: str
    enabled: bool = False
    schedule_cron: str | None = None
    parameters: dict = Field(default_factory=dict)
    batch_size: int = Field(default=1, ge=1)
    snapshot_enabled: bool = True
    verify_enabled: bool = True
    auto_rollback: bool = True

    @field_validator("schedule_cron")
    @classmethod
    def _validate_cron(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from croniter import croniter

        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v!r}")
        return v


class DriftYAML(BaseModel):
    """Global drift-detection schedule.

    Singleton with **leave-alone** semantics: if the ``drift:`` block is
    absent from ``_global.yaml`` (or set to ``null``), the existing
    `drift.check_interval_minutes` setting is left untouched.

    Maps to ``app.settings_service.SETTING_DEFINITIONS['drift.check_interval_minutes']``;
    bounds (1–1440) match the UI / settings-service validators exactly.
    """

    check_interval_minutes: int = Field(ge=1, le=1440)


class DiscoveryYAML(BaseModel):
    """One scheduled network-discovery scan (``ScanConfig`` row).

    Cross-references SSH keys and host groups by **name**, not numeric
    ID — IDs are install-specific, names round-trip across environments.
    Both ``SSHKey.name`` and ``HostGroup.name`` carry a unique constraint
    so the lookup is unambiguous.

    Either ``interval_minutes`` or ``cron_expression`` must be set,
    not both, not neither — same XOR rule the API and DB constraint
    enforce.
    """

    name: str
    cidrs: list[str]
    ssh_key: str  # Resolved to SSHKey.id by the importer
    ssh_port: int = Field(default=22, ge=1, le=65535)
    default_groups: list[str] = []  # Resolved to HostGroup.id list
    interval_minutes: int | None = Field(default=None, ge=1, le=10_080)
    cron_expression: str | None = None
    enabled: bool = True
    auto_add: bool = False

    @field_validator("cidrs", mode="before")
    @classmethod
    def validate_cidrs(cls, v: list) -> list[str]:
        from app.schemas.scans import _validate_cidr  # noqa: PLC0415

        if not v:
            raise ValueError("At least one CIDR is required")
        return [_validate_cidr(str(c)) for c in v]

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from croniter import croniter  # noqa: PLC0415

        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v!r}")
        return v

    @model_validator(mode="after")
    def check_schedule_xor(self) -> "DiscoveryYAML":
        has_interval = self.interval_minutes is not None
        has_cron = self.cron_expression is not None
        if has_interval == has_cron:
            raise ValueError(
                "Exactly one of interval_minutes or cron_expression must be set, "
                "not both or neither"
            )
        return self


class LabDogGlobalYAML(BaseModel):
    """Root model for ``_global.yaml`` at the repo root.

    Phase-1 GitOps imports a per-group YAML keyed by ``HostGroup.name``;
    Phase-2 adds this companion file for state that doesn't fit the
    per-group shape — the global drift interval and the independent
    ``ScanConfig`` rows.

    Missing-section semantics:

    * ``drift`` — singleton, **leave alone** if absent (matches
      ``resolver:`` / ``workflow:`` precedent).
    * ``discovery`` — list, **wipe** if absent (matches firewall /
      services / packages precedent: GitOps is source of truth, the
      operator opts out by removing the file or the section).
    """

    drift: DriftYAML | None = None
    discovery: list[DiscoveryYAML] | None = None
    model_config = ConfigDict(extra="allow")  # Forward-compat


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
    scheduled_actions: list[ScheduledActionYAML] | None = None
    # Ignore unknown top-level keys (incl. legacy ``workflow:``).
    model_config = ConfigDict(extra="allow")
