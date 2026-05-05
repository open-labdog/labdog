"""Manifest schema for action playbooks.

An action manifest is a YAML sidecar that sits next to a playbook file and
declares how LabDog should present and invoke it: display name, version,
parameters, and safety flags. Manifests are the data-driven replacement for
the hardcoded ``register()`` calls that used to live in ``registry.py``.

Convention: a playbook at ``<pack>/actions/foo.yml`` is paired with a manifest
at ``<pack>/actions/foo.manifest.yml``. The pack loader discovers manifests by
glob and resolves the playbook file named in ``playbook``.
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
    model_config = ConfigDict(extra="forbid")

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
