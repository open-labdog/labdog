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

import ipaddress
import logging
import re
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

#: Host names that always mean the controller. Literal loopback addresses
#: (``127.0.0.0/8``, ``::1`` in any spelling) are caught by ``ipaddress``.
_LOCAL_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "localhost4",
        "localhost6",
        "ip6-localhost",
        "ip6-loopback",
    }
)

#: Lookup plugins that only reshape data already in the play. Every other
#: lookup runs on the controller and can read its files (``file``,
#: ``fileglob``, ``template``, ``first_found``, ``ini``, ``csvfile``),
#: its environment (``env``), run commands there (``pipe``, ``lines``) or
#: write to it (``password``). SEC-40.
_SAFE_LOOKUPS = frozenset(
    {
        "items",
        "list",
        "dict",
        "indexed_items",
        "together",
        "nested",
        "cartesian",
        "sequence",
        "subelements",
        "flattened",
        "random_choice",
        "vars",
        "varnames",
    }
)

#: ``lookup('x', …)``, ``query("x", …)``, ``q('x', …)`` inside a template.
_LOOKUP_CALL = re.compile(r"\b(?:lookup|query|q)\s*\(\s*(?P<arg>[^,)]*)")

#: Modules whose ``src`` is read on the controller, so an absolute or
#: parent-relative ``src`` copies a LabDog file to the target.
_CONTROLLER_SRC_MODULES = frozenset(
    {"copy", "template", "unarchive", "script", "assemble", "include_vars", "patch"}
)

#: Modules that write to the controller.
_CONTROLLER_WRITE_MODULES = frozenset({"fetch"})


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


def _short_module(key: str) -> str:
    """``ansible.builtin.copy`` → ``copy``; ``ansible.legacy.fetch`` → ``fetch``."""
    return key.rsplit(".", 1)[-1]


def _is_local_name(value) -> bool:
    """True when *value* names the controller: localhost or any loopback."""
    if isinstance(value, list):
        return any(_is_local_name(v) for v in value)
    if not isinstance(value, str):
        return False
    name = value.strip().lower().strip("[]")
    if name in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _is_local_connection(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"local", "ansible.builtin.local"}


def _unsafe_lookup_names(text: str) -> list[str]:
    """Lookup plugins a template string calls that are not data-only."""
    found = []
    for match in _LOOKUP_CALL.finditer(text):
        arg = match.group("arg").strip()
        name = arg.strip("'\"").rsplit(".", 1)[-1]
        quoted = len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "'\""
        # A name computed at run time cannot be checked, so it counts.
        if not quoted or name not in _SAFE_LOOKUPS:
            found.append(arg or "?")
    return found


def _controller_path(value) -> bool:
    """An absolute or ``..``-escaping path, read relative to the controller."""
    if not isinstance(value, str):
        return False
    path = value.strip()
    return path.startswith(("/", "~")) or ".." in Path(path).parts


def _scan_mapping(node: dict, rel: Path) -> list[PolicyFinding]:
    """Findings for one mapping, whatever it is (play, task, block, vars)."""
    out: list[PolicyFinding] = []

    def found(detail: str) -> None:
        out.append(PolicyFinding("runs on the controller", f"{rel}: {detail}"))

    if "hosts" in node and _is_local_name(node.get("hosts")):
        found(f"a play targets {node['hosts']!r}")
    if _is_local_name(node.get("delegate_to")):
        found(f"a task delegates to {node['delegate_to']!r}")
    if "local_action" in node:
        found("a task uses local_action")
    for key in ("connection", "ansible_connection"):
        if _is_local_connection(node.get(key)):
            found(f"{key}: local is set")
    for key in ("ansible_host", "ansible_ssh_host"):
        if _is_local_name(node.get(key)):
            found(f"{key} points at {node[key]!r}")
    for key in node:
        if not isinstance(key, str):
            continue
        if key.startswith("with_") and key[5:] not in _SAFE_LOOKUPS:
            found(f"a task loops with {key} (a controller-side lookup)")
        module = _short_module(key)
        args = node.get(key)
        if module in _CONTROLLER_WRITE_MODULES:
            found(f"a task uses {key}, which writes to the LabDog host")
        if module in _CONTROLLER_SRC_MODULES:
            src = args.get("src") or args.get("file") if isinstance(args, dict) else args
            remote = isinstance(args, dict) and args.get("remote_src") in (True, "yes", "true")
            if not remote and _controller_path(src):
                found(f"{key} reads {src!r} from the LabDog host")
        if module == "add_host" and isinstance(args, dict):
            out.extend(_scan_mapping(args, rel))
    return out


def _scan_node(node, rel: Path) -> list[PolicyFinding]:
    """Walk every mapping, list and string in a YAML document.

    Recursing into everything (rather than following play → tasks → block)
    covers role task files, whose top level is a task list, handlers, vars
    files, ``add_host`` arguments and ``set_fact`` alike (SEC-40).
    """
    findings: list[PolicyFinding] = []
    if isinstance(node, dict):
        findings.extend(_scan_mapping(node, rel))
        for value in node.values():
            findings.extend(_scan_node(value, rel))
    elif isinstance(node, list):
        for item in node:
            findings.extend(_scan_node(item, rel))
    elif isinstance(node, str):
        for name in _unsafe_lookup_names(node):
            findings.append(
                PolicyFinding(
                    "runs on the controller",
                    f"{rel}: a template calls lookup {name} on the LabDog host",
                )
            )
    return findings


def _scan_playbook(path: Path, pack_root: Path) -> list[PolicyFinding]:
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except (OSError, yaml.YAMLError) as exc:
        # Unreadable is not the same as hostile. The loader reports a broken
        # playbook on its own; this check declines to guess.
        logger.debug("pack_policy: could not parse %s: %s", path, exc)
        return []

    rel = path.relative_to(pack_root)
    findings: list[PolicyFinding] = []
    for doc in docs:
        findings.extend(_scan_node(doc, rel))
    # One finding per distinct reason per file keeps the refusal readable.
    return list(dict.fromkeys(findings))


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
