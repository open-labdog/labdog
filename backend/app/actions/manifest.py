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

from pydantic import BaseModel, ConfigDict, Field


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
    playbook: str = Field(
        description="Playbook filename relative to the manifest's directory."
    )
    version: str
    estimated_duration: str
    destructive: bool = False
    supports_group: bool = True
    supports_host: bool = True
    parameters: list[ManifestParameter] = Field(default_factory=list)
