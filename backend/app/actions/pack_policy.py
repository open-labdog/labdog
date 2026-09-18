"""What a pack may contain before LabDog will load it.

An action pack is a git repository someone points LabDog at. Its playbooks
run against managed hosts, which is the point — but ansible-core also gives
a repository several ways to run code on the *controller*, which is the
LabDog host itself, as the labdog user:

* ``action_plugins/``, ``library/``, ``filter_plugins/``, ``lookup_plugins/``,
  ``callback_plugins/``, ``strategy_plugins/``, ``vars_plugins/``,
  ``test_plugins/``, ``module_utils/`` — Python that ansible-core imports and
  executes in its own process. ``run_ansible`` puts pack roles on
  ``ANSIBLE_ROLES_PATH``, so a plugin directory inside a pack is on the
  search path.
* ``connection: local``, ``hosts: localhost`` and ``delegate_to: localhost``
  — plays and tasks that target the controller rather than the inventory.

Nothing inspected any of that. The loader validated the manifest's parameter
*declarations* and never looked at the playbook body, so registering a pack
was equivalent to handing over code execution on the LabDog host. Under the
flat privilege model that is reachable by any authenticated account.

**The ``trusted`` flag is not a privilege boundary.** Any user can set it,
because any user can do everything (see the privilege-model note in
CLAUDE.md). What it buys is that loading controller-side code becomes a
deliberate, audited act instead of a side effect of adding a repository —
and that the refusal names exactly what it found, so the operator decides
with the facts in front of them. The real containment for a hostile pack is
not this check; it is not adding the pack.

Deliberately a small, legible list rather than a general sandbox. A sandbox
for ansible-core is not something to invent here, and a check that tried to
be exhaustive would be a check nobody could reason about.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

#: Directories ansible-core imports Python from. Presence anywhere inside a
#: pack means the pack can run code on the controller.
CONTROLLER_CODE_DIRS: frozenset[str] = frozenset(
    {
        "action_plugins",
        "library",
        "filter_plugins",
        "lookup_plugins",
        "callback_plugins",
        "connection_plugins",
        "strategy_plugins",
        "vars_plugins",
        "test_plugins",
        "inventory_plugins",
        "module_utils",
        "plugins",
    }
)

#: Play/task keys whose value means "run this on the controller".
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class PolicyFinding:
    """One reason a pack was refused, in words an operator can act on."""

    kind: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.kind}: {self.detail}"


class UntrustedPackContent(Exception):
    """Raised when an untrusted pack contains controller-side code."""

    def __init__(self, pack_name: str, findings: list[PolicyFinding]):
        self.pack_name = pack_name
        self.findings = findings
        joined = "; ".join(str(f) for f in findings)
        super().__init__(
            f"pack {pack_name!r} contains content that runs on the LabDog host "
            f"({joined}). Review it, then mark the pack trusted if this is "
            f"intended."
        )


def _scan_controller_code_dirs(pack_root: Path) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    for path in sorted(pack_root.rglob("*")):
        if path.is_dir() and path.name in CONTROLLER_CODE_DIRS:
            findings.append(
                PolicyFinding(
                    "controller-side Python",
                    f"{path.relative_to(pack_root)}/ is imported and executed by "
                    f"ansible-core on the LabDog host",
                )
            )
    return findings


def _iter_plays(doc) -> list[dict]:
    """Every play in a parsed playbook document, tolerating odd shapes."""
    if not isinstance(doc, list):
        return []
    return [play for play in doc if isinstance(play, dict)]


def _iter_tasks(play: dict):
    """Tasks in every block a play can carry, including nested blocks."""
    for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
        entries = play.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            yield entry
            yield from _iter_tasks(entry)


def _hosts_is_local(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _LOCAL_HOSTS
    if isinstance(value, list):
        return any(_hosts_is_local(v) for v in value)
    return False


def _scan_playbook(path: Path, pack_root: Path) -> list[PolicyFinding]:
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        # Unreadable is not the same as hostile. The loader reports a broken
        # playbook on its own; this check declines to guess.
        logger.debug("pack_policy: could not parse %s: %s", path, exc)
        return []

    rel = path.relative_to(pack_root)
    findings: list[PolicyFinding] = []
    for play in _iter_plays(doc):
        if _hosts_is_local(play.get("hosts")):
            findings.append(
                PolicyFinding("runs on the controller", f"{rel}: a play targets {play['hosts']!r}")
            )
        if str(play.get("connection", "")).strip().lower() == "local":
            findings.append(
                PolicyFinding("runs on the controller", f"{rel}: a play sets connection: local")
            )
        for task in _iter_tasks(play):
            if _hosts_is_local(task.get("delegate_to")):
                findings.append(
                    PolicyFinding(
                        "runs on the controller",
                        f"{rel}: a task delegates to {task['delegate_to']!r}",
                    )
                )
            if str(task.get("connection", "")).strip().lower() == "local":
                findings.append(
                    PolicyFinding("runs on the controller", f"{rel}: a task sets connection: local")
                )
    return findings


def inspect_pack(pack_root: Path) -> list[PolicyFinding]:
    """Every reason this pack would need to be trusted. Empty means clean.

    Read-only and side-effect free, so callers can show the result before
    asking anyone to decide.
    """
    if not pack_root.is_dir():
        return []
    findings = _scan_controller_code_dirs(pack_root)
    for pattern in ("**/*.yml", "**/*.yaml"):
        for path in sorted(pack_root.glob(pattern)):
            if path.is_file():
                findings.extend(_scan_playbook(path, pack_root))
    return findings


def assert_pack_safe(pack_name: str, pack_root: Path, trusted: bool) -> None:
    """Raise :class:`UntrustedPackContent` unless the pack may be loaded.

    A trusted pack is scanned anyway and its findings logged, so the audit
    trail says what was accepted rather than only that something was.
    """
    findings = inspect_pack(pack_root)
    if not findings:
        return
    if trusted:
        logger.info(
            "pack %r is trusted; loading %d item(s) that run on the LabDog host: %s",
            pack_name,
            len(findings),
            "; ".join(str(f) for f in findings),
        )
        return
    raise UntrustedPackContent(pack_name, findings)
