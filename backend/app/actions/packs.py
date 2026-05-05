"""Action packs: filesystem-based containers of playbooks and their manifests.

A pack is a directory shaped like::

    <pack>/
        pack.yml                    # optional — pack metadata
        actions/
            foo.yml                 # the playbook
            foo.manifest.yml        # LabDog action manifest
        roles/                      # optional — Ansible roles referenced by
            role-something/         # this pack's playbooks

Packs are loaded in priority order. When two packs expose the same action key,
the higher-priority pack wins. The bundled pack shipped with LabDog has the
lowest priority so user packs can override built-in actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.actions.manifest import ActionManifest
from app.actions.types import ActionDefinition, ActionParameter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pack:
    name: str
    path: Path
    priority: int = 0

    @property
    def actions_dir(self) -> Path:
        return self.path / "actions"

    @property
    def roles_dir(self) -> Path:
        return self.path / "roles"


def _manifest_to_definition(
    manifest: ActionManifest,
    manifest_path: Path,
    pack: Pack,
) -> ActionDefinition:
    playbook_path = (manifest_path.parent / manifest.playbook).resolve()
    if not playbook_path.is_file():
        raise FileNotFoundError(
            f"Manifest {manifest_path} references playbook "
            f"{manifest.playbook!r} which does not exist at {playbook_path}."
        )
    roles_paths: tuple[Path, ...] = (pack.roles_dir,) if pack.roles_dir.is_dir() else ()
    verify_playbook_path: Path | None = None
    if manifest.verify_playbook is not None:
        candidate = (manifest_path.parent / manifest.verify_playbook).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(
                f"Manifest {manifest_path} references verify_playbook "
                f"{manifest.verify_playbook!r} which does not exist at "
                f"{candidate}."
            )
        verify_playbook_path = candidate
    return ActionDefinition(
        key=manifest.key,
        name=manifest.name,
        description=manifest.description,
        icon=manifest.icon,
        playbook_path=playbook_path,
        version=manifest.version,
        estimated_duration=manifest.estimated_duration,
        destructive=manifest.destructive,
        supports_group=manifest.supports_group,
        supports_host=manifest.supports_host,
        supports_fleet=manifest.supports_fleet,
        parameters=tuple(
            ActionParameter(
                key=p.key,
                label=p.label,
                type=p.type,
                default=p.default,
                required=p.required,
                choices=tuple(p.choices) if p.choices else None,
                help_text=p.help_text,
            )
            for p in manifest.parameters
        ),
        pack_name=pack.name,
        roles_paths=roles_paths,
        verify_playbook_path=verify_playbook_path,
        verify_timeout_seconds=manifest.verify_timeout_seconds,
    )


def load_pack(pack: Pack) -> list[ActionDefinition]:
    """Load all action definitions from a single pack.

    Scans ``<pack>/actions/*.manifest.yml`` and returns an ActionDefinition per
    valid manifest. Bad manifests are logged and skipped so one malformed file
    can't take down the whole pack.
    """
    if not pack.actions_dir.is_dir():
        logger.warning(
            "pack %r has no actions directory at %s; skipping",
            pack.name,
            pack.actions_dir,
        )
        return []

    defns: list[ActionDefinition] = []
    for manifest_path in sorted(pack.actions_dir.glob("*.manifest.yml")):
        try:
            raw = yaml.safe_load(manifest_path.read_text()) or {}
            manifest = ActionManifest.model_validate(raw)
        except (yaml.YAMLError, ValidationError) as exc:
            logger.error(
                "pack %r: failed to load manifest %s: %s",
                pack.name,
                manifest_path,
                exc,
            )
            continue
        # Defence-in-depth: ActionManifest already rejects underscore keys
        # via field_validator, but a malformed-YAML-fallback path could in
        # theory slip one through. Belt-and-braces.
        if manifest.key.startswith("_"):
            logger.warning(
                "pack %r: skipping manifest %s — key %r is reserved for "
                "built-in pseudo-actions",
                pack.name,
                manifest_path,
                manifest.key,
            )
            continue
        try:
            defns.append(_manifest_to_definition(manifest, manifest_path, pack))
        except FileNotFoundError as exc:
            logger.error(
                "pack %r: failed to load manifest %s: %s",
                pack.name,
                manifest_path,
                exc,
            )
    return defns


def load_packs(packs: list[Pack]) -> dict[str, ActionDefinition]:
    """Merge actions from multiple packs into a single registry dict.

    Packs are processed in ascending priority order — actions from higher
    priority packs overwrite equal-keyed actions from lower priority packs.
    Ties broken by iteration order (later wins), so callers should pass packs
    in the order they want ties resolved.

    Each surviving ``ActionDefinition`` is returned with ``overridden_from``
    populated: the names of packs whose entries for the same key were
    shadowed, in processing order. Callers (API, logs) use this for
    provenance in the UI.
    """
    from dataclasses import replace  # noqa: PLC0415

    ordered = sorted(packs, key=lambda p: p.priority)
    registry: dict[str, ActionDefinition] = {}
    # key → list of pack names in processing order; last is the winner
    history: dict[str, list[str]] = {}
    for pack in ordered:
        for defn in load_pack(pack):
            if defn.key in history:
                prev_winner = history[defn.key][-1]
                logger.info(
                    "action %r from pack %r overrides pack %r",
                    defn.key,
                    pack.name,
                    prev_winner,
                )
            history.setdefault(defn.key, []).append(pack.name)
            registry[defn.key] = defn
    return {
        key: replace(defn, overridden_from=tuple(history[key][:-1]))
        for key, defn in registry.items()
    }
