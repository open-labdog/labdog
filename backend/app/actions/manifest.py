"""Manifest schema for action playbooks.

An action manifest is a YAML file that declares how LabDog should present
and invoke a playbook: display name, version, parameters, and safety flags.
Manifests are the data-driven replacement for the hardcoded ``register()``
calls that used to live in ``registry.py``.

Convention: each action lives in its own directory under ``<pack>/actions/``.
The manifest is ``<pack>/actions/<key>/manifest.yml``; it names a playbook
relative to its own directory (conventionally ``playbook.yml``). The pack
loader discovers manifests by globbing ``actions/*/manifest.yml`` and
resolves the playbook file named in ``playbook``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ManifestParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    type: Literal["string", "int", "bool", "choice"]
    default: Any = None
    required: bool = False
    choices: list[str] | None = None
    help_text: str | None = None


class ActionManifest(BaseModel):
    # ``extra='ignore'`` so older manifests carrying retired fields
    # (notably ``execution_mode``) don't fail validation. New manifests
    # should still avoid unknown keys; the loader's strictness is now in
    # the test suite + lint, not in the runtime validator.
    model_config = ConfigDict(extra="ignore")

    key: str
    name: str
    description: str
    icon: str
    playbook: str = Field(description="Playbook filename relative to the manifest's directory.")
    version: str
    estimated_duration: str
    destructive: bool = False
    supports_group: bool = True
    supports_host: bool = True
    supports_fleet: bool = Field(
        default=False,
        description=(
            "Whether the action makes sense across every host in the inventory. "
            "Conservative default — flip to True only for truly fleet-wide work "
            "like drift checks or state collection."
        ),
    )
    parameters: list[ManifestParameter] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _reserved_underscore_prefix(cls, v: str) -> str:
        if v.startswith("_"):
            raise ValueError(
                "Action keys starting with '_' are reserved for built-in "
                "pseudo-actions (e.g. '_builtin.sync'). Pick a key that "
                "begins with a letter."
            )
        return v

    verify_playbook: str | None = Field(
        default=None,
        description=(
            "Optional filename (relative to the manifest's directory) of a "
            "second playbook that decides post-run success. When set, LabDog "
            "runs it after the main playbook against the same host with the "
            "same extra_vars and pack roles; the Ansible exit status becomes "
            "the verification result. Only fires for destructive actions on "
            "hosts with a Proxmox VM mapping — same gate as the built-in "
            "health check it replaces."
        ),
    )
    verify_timeout_seconds: int = Field(
        default=300,
        description=(
            "Budget for the verify playbook. Prevents a slow probe from "
            "stalling snapshot cleanup. Ignored when verify_playbook is "
            "unset."
        ),
    )
    post_run_sync: list[
        Literal[
            "packages",
            "resolver",
            "services",
            "hosts-file",
            "cron",
            "linux-users",
            "firewall",
        ]
    ] = Field(
        default_factory=list,
        description=(
            "Modules whose desired state should be re-enforced (synced) "
            "after the action succeeds. Each named module is dispatched "
            "through the normal sync pipeline against the same target "
            "host (per-host fan-out for group-dispatched actions). "
            "Semantics are 'push labdog's desired state', NOT 'collect "
            "current state' -- only declare modules where re-enforcing "
            "the existing config is what the action wants. Skipped on "
            "dry-run and on action failure. Module names must match "
            "``CANONICAL_ORDER`` in ``app/ansible_runtime/composer.py``."
        ),
    )

    @field_validator("post_run_sync")
    @classmethod
    def _dedup_post_run_sync(cls, v: list[str]) -> list[str]:
        # Preserve declaration order; drop duplicates so dispatch fires
        # at most one SyncJob per module per run.
        seen: set[str] = set()
        out: list[str] = []
        for m in v:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out

    post_run_register: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict,
        description=(
            "Resources to register as host-scope overrides in labdog's "
            "desired-state model after the action succeeds. Top-level "
            "keys are canonical module names (packages, resolver, "
            "services, hosts-file, cron, linux-users, firewall); each "
            "value is a list of dicts validated against the module's "
            "REST API Create schema. ``host_id`` is implicit -- the "
            "rows are inserted with host_id=<action target> and "
            "group_id=NULL. Per-host fan-out for group-dispatched "
            "actions; rows added for every member host where the "
            "action succeeded. Conflicts (uniqueness collisions with "
            "existing rows) are skipped silently and logged so "
            "operator-declared rows win over action-declared ones. "
            "Use this for actions that install resources labdog "
            "should manage going forward (e.g. install Alloy + "
            "register alloy.service + alloy package). Do NOT use "
            "actions to mutate firewall, hosts-file, resolver, or "
            "SSH-keys without first declaring them here or via "
            "labdog's UI/API -- those modules purge unknown entries "
            "on next sync."
        ),
    )

    @field_validator("post_run_register")
    @classmethod
    def _validate_post_run_register(
        cls, v: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        # Lazy import to avoid pulling app.packages/services/... at
        # action-module import time (manifest is loaded very early in
        # the FastAPI lifespan + Celery worker_ready boot).
        from app.actions.register_schemas import (
            CREATE_SCHEMAS,
            VALID_MODULE_NAMES,
        )

        cleaned: dict[str, list[dict[str, Any]]] = {}
        for module, items in v.items():
            if module not in VALID_MODULE_NAMES:
                raise ValueError(
                    f"post_run_register: unknown module {module!r}. Valid: "
                    f"{sorted(VALID_MODULE_NAMES)}"
                )
            if not isinstance(items, list):
                raise ValueError(
                    f"post_run_register.{module}: must be a list of dicts"
                )
            schema = CREATE_SCHEMAS[module]
            cleaned_items: list[dict[str, Any]] = []
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"post_run_register.{module}[{i}]: must be a dict"
                    )
                # Reject authorized_keys on linux-users: granting SSH
                # access from a manifest is too privileged to auto-install
                # without explicit operator confirmation.
                if module == "linux-users" and item.get("authorized_keys"):
                    raise ValueError(
                        f"post_run_register.linux-users[{i}]: 'authorized_keys' is not "
                        "allowed via post_run_register. SSH key grants are too privileged "
                        "to auto-install from a manifest -- declare the user via "
                        "post_run_register, then the operator can add keys via the UI/API."
                    )
                # Validate against the existing module Create schema.
                # Re-export as model_dump() so we store the validated /
                # default-filled shape, not the raw author input.
                validated = schema(**item)
                cleaned_items.append(validated.model_dump())
            if cleaned_items:
                cleaned[module] = cleaned_items
        return cleaned
