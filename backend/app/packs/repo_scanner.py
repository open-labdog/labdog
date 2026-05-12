"""Walk a cloned git repo and discover action packs and gitops files.

Pure file-walking logic. No DB. No network. No exceptions on bad input —
malformed YAML, broken manifests, and unparseable gitops files are
recorded as ``errors`` on the relevant finding rather than aborting
the scan. Callers that hit an OS-level error (clone failed, path not
readable) handle it before reaching this module.

Used by ``app.api._repo_scan`` to power the ``POST /api/git-repos/{id}/scan``
endpoint, which feeds the wizard's review step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Pack convention markers.
PACK_YML_NAME = "pack.yml"
ACTIONS_DIR_NAME = "actions"
MANIFEST_FILENAME = "manifest.yml"


@dataclass(frozen=True)
class ScanError:
    """One parse / validation problem against one file in the repo."""

    file: str
    message: str


@dataclass(frozen=True)
class DetectedPack:
    """One action pack discovered at ``path`` (relative to clone root).

    ``contributed_keys`` is the tuple of action ``key`` values found in
    the pack's ``actions/`` subdirectory, in stable filesystem order.
    Empty tuple is fine: a pack with no ``actions/<key>/manifest.yml``
    is still a valid finding, the wizard just renders it with an info
    message.

    ``pack_yml_present`` distinguishes a real pack root (an explicit
    ``pack.yml``) from the synthetic root-level fallback the scanner
    emits when ``actions/`` exists at the repo root with no
    ``pack.yml`` anywhere.
    """

    path: str
    name: str
    contributed_keys: tuple[str, ...]
    pack_yml_present: bool
    errors: tuple[ScanError, ...] = ()


@dataclass(frozen=True)
class DetectedGitopsFile:
    """One YAML file that looks like a gitops group definition.

    ``group_name`` is the value of the ``group:`` top-level key,
    extracted best-effort even when the file fails strict schema
    validation. Errors from ``LabDogGroupYAML.model_validate`` land in
    ``errors``; the wizard surfaces them inline.
    """

    path: str
    group_name: str | None
    errors: tuple[ScanError, ...] = ()


@dataclass(frozen=True)
class ScanResult:
    packs: list[DetectedPack] = field(default_factory=list)
    gitops_files: list[DetectedGitopsFile] = field(default_factory=list)
    scan_errors: list[ScanError] = field(default_factory=list)


def scan_repository(
    clone_path: Path | str,
    *,
    repo_name: str | None = None,
) -> ScanResult:
    """Walk ``clone_path`` and return everything LabDog might activate.

    See module docstring for what 'pack' and 'gitops file' mean. The
    scan is non-recursive at the top, and each action is a directory
    under ``<pack>/actions/`` containing ``manifest.yml`` — nested
    ``actions/`` subdirectories aren't supported.
    """
    root = Path(clone_path).resolve()
    if not root.is_dir():
        return ScanResult(
            scan_errors=[ScanError(file=str(root), message="clone path is not a directory")]
        )

    pack_dirs = _find_pack_directories(root)
    packs: list[DetectedPack] = []

    if pack_dirs:
        for pack_dir in pack_dirs:
            packs.append(_build_detected_pack(root, pack_dir, synthetic=False))
    else:
        # Fallback: no pack.yml anywhere, but if the repo root has an
        # ``actions/`` directory containing action manifests, treat the
        # root as a single synthetic pack.
        root_actions = root / ACTIONS_DIR_NAME
        if root_actions.is_dir() and any(root_actions.glob(f"*/{MANIFEST_FILENAME}")):
            packs.append(
                _build_detected_pack(
                    root,
                    root,
                    synthetic=True,
                    fallback_name=repo_name or "pack",
                )
            )

    pack_dir_set = {p.resolve() for p in pack_dirs}
    gitops_files = _find_gitops_files(root, pack_dir_set)

    return ScanResult(packs=packs, gitops_files=gitops_files, scan_errors=[])


# ---------------------------------------------------------------------------
# Pack discovery
# ---------------------------------------------------------------------------


def _find_pack_directories(root: Path) -> list[Path]:
    """Every directory inside ``root`` that contains a ``pack.yml`` file."""
    return sorted({p.parent for p in root.rglob(PACK_YML_NAME) if p.is_file()})


def _build_detected_pack(
    root: Path,
    pack_dir: Path,
    *,
    synthetic: bool,
    fallback_name: str | None = None,
) -> DetectedPack:
    rel_path = "" if pack_dir == root else pack_dir.relative_to(root).as_posix()
    errors: list[ScanError] = []

    # Pack name: prefer pack.yml's ``name:``, else dirname, else fallback.
    name: str
    if synthetic:
        name = fallback_name or "pack"
    else:
        pack_yml = pack_dir / PACK_YML_NAME
        rel_pack_yml = pack_yml.relative_to(root).as_posix()
        try:
            with pack_yml.open() as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise ValueError(f"pack.yml top-level must be a mapping, got {type(data).__name__}")
            name = str(data.get("name") or pack_dir.name)
        except (yaml.YAMLError, OSError, ValueError) as exc:
            errors.append(ScanError(file=rel_pack_yml, message=str(exc)))
            name = pack_dir.name

    # Per-pack actions: <pack>/actions/<key>/manifest.yml.
    contributed_keys: list[str] = []
    actions_dir = pack_dir / ACTIONS_DIR_NAME
    if actions_dir.is_dir():
        for manifest_path in sorted(actions_dir.glob(f"*/{MANIFEST_FILENAME}")):
            rel_manifest = manifest_path.relative_to(root).as_posix()
            key, err = _validate_manifest(manifest_path, rel_manifest)
            if err is not None:
                errors.append(err)
            if key is not None:
                contributed_keys.append(key)

    return DetectedPack(
        path=rel_path,
        name=name,
        contributed_keys=tuple(contributed_keys),
        pack_yml_present=not synthetic,
        errors=tuple(errors),
    )


def _validate_manifest(manifest_path: Path, rel_path: str) -> tuple[str | None, ScanError | None]:
    """Validate one ``manifest.yml`` file. Returns ``(key, error)``.

    Either or both can be ``None``: a malformed manifest yields
    ``(None, ScanError)``; a valid one yields ``(key, None)``; a
    YAML-valid manifest with a parseable ``key:`` field but failing
    full ``ActionManifest`` validation yields ``(key, ScanError)`` so
    the conflict resolver still sees what key was claimed.
    """
    # Deferred import: ActionManifest pulls in pydantic + a chunk of
    # the action subsystem which we don't want at module load.
    from app.actions.manifest import ActionManifest

    try:
        with manifest_path.open() as fh:
            raw = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError) as exc:
        return None, ScanError(file=rel_path, message=f"YAML parse error: {exc}")

    if not isinstance(raw, dict):
        return None, ScanError(
            file=rel_path,
            message=f"manifest top-level must be a mapping, got {type(raw).__name__}",
        )

    raw_key = raw.get("key") if isinstance(raw.get("key"), str) else None

    try:
        ActionManifest.model_validate(raw)
    except Exception as exc:
        return raw_key, ScanError(file=rel_path, message=f"manifest validation failed: {exc}")

    return raw_key, None


# ---------------------------------------------------------------------------
# Gitops discovery
# ---------------------------------------------------------------------------


def _find_gitops_files(root: Path, pack_dirs: set[Path]) -> list[DetectedGitopsFile]:
    """Every ``*.yml`` / ``*.yaml`` whose top-level dict has a ``group:`` key.

    Files inside any pack directory are excluded — pack manifests are
    not gitops files. ``pack.yml`` and ``manifest.yml`` are excluded by
    name to cover the synthetic-pack case (a pack root without a
    ``pack.yml`` is not in ``pack_dirs``, so its manifests still need
    filename-level filtering).
    """
    candidates: list[Path] = []
    for pattern in ("**/*.yml", "**/*.yaml"):
        candidates.extend(root.glob(pattern))

    gitops: list[DetectedGitopsFile] = []
    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        if _is_under_any(path, pack_dirs):
            continue
        if path.name in (PACK_YML_NAME, MANIFEST_FILENAME):
            continue

        gitops_file = _build_gitops_finding(root, path)
        if gitops_file is not None:
            gitops.append(gitops_file)

    return gitops


def _is_under_any(path: Path, ancestors: set[Path]) -> bool:
    resolved = path.resolve()
    for anc in ancestors:
        try:
            resolved.relative_to(anc)
        except ValueError:
            continue
        return True
    return False


def _build_gitops_finding(root: Path, path: Path) -> DetectedGitopsFile | None:
    """Return a finding only when the file looks like a gitops group YAML.

    ``None`` for files that parse but lack a ``group:`` key — those are
    just regular YAML files that happen to live in the repo, not
    LabDog gitops definitions.
    """
    rel = path.relative_to(root).as_posix()
    errors: list[ScanError] = []

    try:
        with path.open() as fh:
            raw: Any = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError) as exc:
        # Invalid YAML at .yml/.yaml extension might still be a gitops
        # file the operator wrote with a typo. Surface as a finding so
        # the wizard can show the parse error.
        return DetectedGitopsFile(
            path=rel,
            group_name=None,
            errors=(ScanError(file=rel, message=f"YAML parse error: {exc}"),),
        )

    if not isinstance(raw, dict) or "group" not in raw:
        return None

    raw_group = raw.get("group")
    group_name = raw_group if isinstance(raw_group, str) else None

    # Best-effort full schema validation. Failure is non-fatal — the
    # finding is still emitted with the extracted group_name so the
    # operator sees what's there and what's wrong with it.
    from app.gitops.schema import LabDogGroupYAML

    try:
        LabDogGroupYAML.model_validate(raw)
    except Exception as exc:
        errors.append(ScanError(file=rel, message=f"gitops schema validation failed: {exc}"))

    return DetectedGitopsFile(path=rel, group_name=group_name, errors=tuple(errors))
