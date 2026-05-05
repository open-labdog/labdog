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
    #: ``None`` for built-in pseudo-actions (``_builtin.*``) which don't
    #: have an Ansible playbook on disk — their per-host work is handled
    #: by dedicated Celery tasks in ``app.tasks.*`` (see C5 dispatch
    #: routing). Pack-supplied actions always have a path.
    playbook_path: Path | None
    version: str
    estimated_duration: str
    destructive: bool = False
    supports_group: bool = True
    supports_host: bool = True
    #: Whether this action makes sense across the entire fleet. Conservative
    #: default ``False`` — operators rarely want every host to run an
    #: action at once. The built-in ``_builtin.drift_check`` and
    #: ``_builtin.collect_state`` set this to ``True``.
    supports_fleet: bool = False
    parameters: tuple[ActionParameter, ...] = field(default_factory=tuple)
    pack_name: str = "bundled"
    roles_paths: tuple[Path, ...] = field(default_factory=tuple)
    #: Names of packs whose definitions were overridden by this one,
    #: in the order they were processed (lowest priority first). Empty
    #: when no other pack declared the same ``key``. Shown in the UI as
    #: a provenance hint so admins know why a collision resolved the way
    #: it did.
    overridden_from: tuple[str, ...] = field(default_factory=tuple)
    #: Absolute path to a pack-supplied verify playbook that decides
    #: post-run success. ``None`` falls back to the built-in
    #: SSH/services/packages health check. Only consulted for
    #: destructive actions on hosts with a Proxmox VM mapping.
    verify_playbook_path: Path | None = None
    verify_timeout_seconds: int = 300

    @property
    def is_builtin(self) -> bool:
        """``True`` for keys in the reserved ``_builtin.*`` namespace."""
        return self.key.startswith("_builtin.")
