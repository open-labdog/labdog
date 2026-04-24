"""Data types for the action registry.

Kept in a leaf module so both ``registry`` and ``packs`` can import these
without producing a circular import. The public surface lives on
``app.actions.registry`` for historical reasons and re-exports these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class ActionParameter:
    key: str
    label: str
    type: Literal["string", "int", "bool", "choice"]
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] | None = None
    help_text: str | None = None


@dataclass(frozen=True)
class ActionDefinition:
    key: str
    name: str
    description: str
    icon: str
    playbook_path: Path
    version: str
    estimated_duration: str
    destructive: bool = False
    supports_group: bool = True
    supports_host: bool = True
    parameters: tuple[ActionParameter, ...] = field(default_factory=tuple)
    pack_name: str = "bundled"
    roles_paths: tuple[Path, ...] = field(default_factory=tuple)
    #: Names of packs whose definitions were overridden by this one,
    #: in the order they were processed (lowest priority first). Empty
    #: when no other pack declared the same ``key``. Shown in the UI as
    #: a provenance hint so admins know why a collision resolved the way
    #: it did.
    overridden_from: tuple[str, ...] = field(default_factory=tuple)
