"""Action packs: filesystem-based containers of playbooks and their manifests.

A pack is a directory shaped like::

    <pack>/
        pack.yml                    # optional — pack metadata
        actions/
            <key>/                  # one directory per action
                manifest.yml        # LabDog action manifest
                playbook.yml        # the Ansible playbook
                roles/              # optional — action-private roles
                    <role-name>/
        roles/                      # optional — pack-shared Ansible roles
            <role-name>/

Packs have **no inherent precedence**. When multiple packs declare the
same action key the operator pins which pack wins via an
``action_resolution`` row. Until pinned, the key is *unresolved* and
the action cannot be run. Uncontested keys win automatically.
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
    pack_id: int | None = None
    """Database id of the matching ``ActionPack`` row, or ``None`` for
    the in-image bundled pack. Used as the natural key for the
    ``action_resolution`` and ``action_registry_snapshot`` tables."""

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
        post_run_sync=tuple(manifest.post_run_sync),
    )


def load_pack(pack: Pack) -> list[ActionDefinition]:
    """Load all action definitions from a single pack.

    Scans ``<pack>/actions/*/manifest.yml`` and returns an ActionDefinition per
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
    for manifest_path in sorted(pack.actions_dir.glob("*/manifest.yml")):
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
                "pack %r: skipping manifest %s — key %r is reserved for built-in pseudo-actions",
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


@dataclass(frozen=True)
class PackContributor:
    """One pack's participation in a particular action key, for the
    contested-keys view."""

    pack_id: int | None
    pack_name: str


@dataclass(frozen=True)
class ResolutionMergeResult:
    """Outcome of :func:`load_packs_with_resolutions`.

    ``registry`` — final ``key → ActionDefinition`` map for installation
    into ``ACTION_REGISTRY``. Contested keys without a resolution
    appear here as **unresolved** placeholders: ``playbook_path=None``,
    ``winning_pack_id=None``, ``overridden_from=`` every contributor.
    The API surfaces this state; the orchestrator refuses to dispatch.

    ``new_snapshot`` — ``key → pack_id`` (None=bundled) for keys with
    a resolved winner. The caller persists this into
    ``action_registry_snapshot`` to drive the next rebuild's freeze
    logic. Unresolved keys are deliberately omitted so a later
    rebuild treats them fresh.

    ``fresh_freezes`` — keys where rebuild detected a fresh conflict
    (an uncontested key just became contested) and auto-pinned the
    previous winner; caller writes one ``action_resolution`` row per
    entry to make the freeze durable.

    ``stale_resolution_keys`` — resolution rows whose chosen pack no
    longer contributes the key (pack deleted / renamed away); caller
    deletes these rows.

    ``contributors`` — ``key → [PackContributor, ...]`` covering every
    pack that supplied a manifest for the key. Cached so the
    ``/api/action-resolutions`` view doesn't need to re-scan manifests.
    """

    registry: dict[str, ActionDefinition]
    new_snapshot: dict[str, int | None]
    fresh_freezes: dict[str, int | None]
    stale_resolution_keys: set[str]
    contributors: dict[str, list[PackContributor]]


def _unresolved_placeholder(
    defn: ActionDefinition,
    losers: tuple[str, ...],
) -> ActionDefinition:
    """Return a placeholder ActionDefinition for an unresolved key.

    Carries the manifest's display metadata (name / description / icon
    / parameters) from one of the candidates so the UI can render
    something useful while the operator is picking, but with
    ``playbook_path=None`` and ``winning_pack_id=None`` so any code
    that tries to actually run the action sees the unresolved state
    and refuses.
    """
    from dataclasses import replace  # noqa: PLC0415

    return replace(
        defn,
        playbook_path=None,
        winning_pack_id=None,
        overridden_from=losers,
    )


def load_packs_with_resolutions(
    packs: list[Pack],
    *,
    resolutions: dict[str, int | None],
    prior_winners: dict[str, int | None],
) -> ResolutionMergeResult:
    """Merge packs into a registry honouring explicit per-key resolutions
    and freeze-on-fresh-conflict semantics.

    Resolution semantics (post-position refactor):

    1. **Uncontested key** (one contributor) — that pack wins.
    2. **Contested + explicit resolution** — pinned pack wins.
       ``pack_id=None`` resolves to bundled.
    3. **Contested + no resolution** — *unresolved*. The registry
       entry is a placeholder with no playbook; the orchestrator
       refuses to dispatch and the UI prompts the operator.
    4. **Stale resolutions** — rows whose chosen pack no longer
       contributes the key are queued for deletion; the key falls
       through to (3) on the next rebuild.
    5. **Fresh-conflict freeze** — a previously-uncontested key that
       just became contested auto-pins the previous winner so a sync
       cannot silently introduce an unresolved key; the operator can
       confirm or change the pin via the UI.
    """
    from dataclasses import replace  # noqa: PLC0415

    # Gather contributors per key, preserving load_pack's logging on
    # malformed manifests.
    contributors: dict[str, list[tuple[Pack, ActionDefinition]]] = {}
    # Pack iteration order is stable (sorted by name) so contested-key
    # placeholders and overridden_from lists are deterministic.
    for pack in sorted(packs, key=lambda p: p.name):
        for defn in load_pack(pack):
            contributors.setdefault(defn.key, []).append((pack, defn))

    registry: dict[str, ActionDefinition] = {}
    new_snapshot: dict[str, int | None] = {}
    fresh_freezes: dict[str, int | None] = {}
    stale: set[str] = set()

    for key, candidates in contributors.items():
        # Sort candidates by pack name for stable display ordering of
        # losers / placeholders.
        candidates.sort(key=lambda c: c[0].name)

        # Uncontested: single contributor wins automatically.
        if len(candidates) == 1:
            sole_pack, sole_defn = candidates[0]
            registry[key] = replace(
                sole_defn,
                winning_pack_id=sole_pack.pack_id,
                overridden_from=(),
            )
            new_snapshot[key] = sole_pack.pack_id
            continue

        # Contested. Look for an explicit resolution that names a
        # current contributor.
        winner: tuple[Pack, ActionDefinition] | None = None
        if key in resolutions:
            chosen_id = resolutions[key]
            match = next((c for c in candidates if c[0].pack_id == chosen_id), None)
            if match is not None:
                winner = match
            else:
                # Resolution points at a pack that's gone or no longer
                # contributes this key — drop the row.
                stale.add(key)

        # Freeze-on-fresh-conflict: no explicit resolution but the
        # previous snapshot has a winner that's still a contributor.
        # Auto-pin it so behaviour doesn't change silently.
        if winner is None and key in prior_winners:
            prev_id = prior_winners[key]
            prev_match = next((c for c in candidates if c[0].pack_id == prev_id), None)
            if prev_match is not None:
                logger.warning(
                    "action %r: fresh conflict — freezing winner to pack %r. "
                    "Operator must confirm via /action-packs.",
                    key,
                    prev_match[0].name,
                )
                winner = prev_match
                fresh_freezes[key] = prev_match[0].pack_id

        if winner is not None:
            winner_pack, winner_defn = winner
            losers = tuple(c[0].name for c in candidates if c[0] is not winner_pack)
            registry[key] = replace(
                winner_defn,
                winning_pack_id=winner_pack.pack_id,
                overridden_from=losers,
            )
            new_snapshot[key] = winner_pack.pack_id
        else:
            # Contested + no resolution + no usable snapshot →
            # unresolved. Use the first candidate's manifest for
            # display metadata; mark playbook_path None and
            # winning_pack_id None so any runtime code sees the
            # unresolved state.
            _placeholder_pack, placeholder_defn = candidates[0]
            losers = tuple(c[0].name for c in candidates)
            registry[key] = _unresolved_placeholder(placeholder_defn, losers)
            # Deliberately omit unresolved keys from new_snapshot —
            # the next rebuild treats them fresh.
            logger.info(
                "action %r: unresolved — %d contributing packs, no operator pin",
                key,
                len(candidates),
            )

    contributors_view: dict[str, list[PackContributor]] = {
        key: [
            PackContributor(pack_id=p.pack_id, pack_name=p.name)
            for p, _ in candidates
        ]
        for key, candidates in contributors.items()
    }

    return ResolutionMergeResult(
        registry=registry,
        new_snapshot=new_snapshot,
        fresh_freezes=fresh_freezes,
        stale_resolution_keys=stale,
        contributors=contributors_view,
    )
