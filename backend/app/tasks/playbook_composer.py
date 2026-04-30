"""Compose a single Ansible playbook from per-module fragments.

Used by the coalesced per-host sync orchestrator (v0.2.0): each module
generator produces a `PlaybookFragment`, the orchestrator collects
them, and ``compose_playbook`` emits one YAML playbook for
ansible-runner.

Pure library code: no DB, no Celery, no async. The orchestrator that
consumes it lives elsewhere.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import yaml

from app.cron.generator import generate_cron_playbook
from app.packages.generator import generate_package_playbook
from app.resolver.generator import generate_resolver_playbook
from app.user_mgmt.generator import generate_user_playbook

# SSH-related play.vars keys that some generators bake into the play.
# The orchestrator owns SSH wiring via the inventory, so adapters strip
# them post-call to keep fragments transport-agnostic.
_SSH_VAR_KEYS = frozenset(
    {
        "ansible_host",
        "ansible_port",
        "ansible_user",
        "ansible_ssh_private_key_file",
        "ansible_ssh_common_args",
        "ansible_ssh_extra_args",
        "ansible_connection",
    }
)

# Throwaway value passed to generators that require a path. The
# orchestrator supplies the real key path via the inventory; adapters
# discard whatever the generator did with this stub.
_UNUSED_KEY_PATH = "/dev/null"

CANONICAL_ORDER: list[str] = [
    "packages",
    "resolver",
    "services",
    "hosts-file",
    "cron",
    "linux-users",
    "firewall",
]

HOSTS_SENTINEL = "target"


@dataclass(frozen=True)
class PlaybookFragment:
    """One module's contribution to the unified playbook.

    ``module`` must be a canonical module name (see ``CANONICAL_ORDER``).
    ``plays`` is a list of Ansible play dicts. Each play's ``hosts``
    field should be set to ``HOSTS_SENTINEL`` (``"target"``);
    ``compose_playbook`` rewrites it to the caller-supplied
    ``hosts_alias``.
    """

    module: str
    plays: list[dict[str, Any]]


def _inject_tags(plays: list[dict[str, Any]], module_name: str) -> list[dict[str, Any]]:
    """Return a deep copy of ``plays`` with ``module_name`` added to every task's tags.

    Walks ``pre_tasks``, ``tasks``, and ``post_tasks`` if present. Idempotent —
    re-running on already-tagged tasks does not duplicate the tag.
    """
    out = copy.deepcopy(plays)
    for play in out:
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in play.get(section, []) or []:
                existing = task.get("tags") or []
                if isinstance(existing, str):
                    existing = [existing]
                if module_name not in existing:
                    task["tags"] = [module_name, *existing]
    return out


def compose_playbook(
    fragments: list[PlaybookFragment],
    module_filter: list[str] | None = None,
    hosts_alias: str = HOSTS_SENTINEL,
) -> str:
    """Concatenate fragments into a single Ansible playbook YAML string.

    - Plays are emitted in ``CANONICAL_ORDER``, regardless of input order.
    - ``module_filter``: ``None`` includes everything; a non-empty list
      includes only those modules (silently skipping ones not in
      ``fragments``); ``[]`` raises ``ValueError`` to forbid an empty
      playbook.
    - ``hosts_alias``: substituted for ``HOSTS_SENTINEL`` on every
      play's ``hosts`` field.
    - Each task is tagged with its module name.
    """
    if module_filter is not None and len(module_filter) == 0:
        raise ValueError("compose_playbook: module_filter must not be empty")

    unknown = [f.module for f in fragments if f.module not in CANONICAL_ORDER]
    if unknown:
        raise ValueError(f"compose_playbook: unknown module(s) {unknown}")

    by_module: dict[str, PlaybookFragment] = {}
    for f in fragments:
        if f.module in by_module:
            raise ValueError(f"compose_playbook: duplicate fragment for module {f.module!r}")
        by_module[f.module] = f

    selected_modules = [
        m
        for m in CANONICAL_ORDER
        if m in by_module and (module_filter is None or m in module_filter)
    ]

    composed: list[dict[str, Any]] = []
    for module in selected_modules:
        plays = _inject_tags(by_module[module].plays, module)
        for play in plays:
            if play.get("hosts") == HOSTS_SENTINEL:
                play["hosts"] = hosts_alias
        composed.extend(plays)

    return yaml.dump(composed, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Module adapters: wrap per-module generators into PlaybookFragment.
#
# Each adapter calls the underlying generator with HOSTS_SENTINEL as
# host_ip and a throwaway SSH key path, then normalizes the result into
# a list of plays, strips any SSH-bearing play vars, and rewrites every
# play's ``hosts`` to HOSTS_SENTINEL. The orchestrator owns inventory
# and the real SSH key — the generator's stub call exists only to
# produce play structure.
# ---------------------------------------------------------------------------


def _strip_ssh_vars(plays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove SSH-related keys from each play's ``vars`` dict, in place.

    No-op for plays without ``vars`` or with ``vars`` that contain no
    SSH keys. If ``vars`` becomes empty after stripping, the key is
    removed from the play entirely.
    """
    for play in plays:
        play_vars = play.get("vars")
        if not isinstance(play_vars, dict):
            continue
        for key in list(play_vars.keys()):
            if key in _SSH_VAR_KEYS:
                del play_vars[key]
        if not play_vars:
            play.pop("vars", None)
    return plays


def fragment_cron(cron_jobs: list) -> PlaybookFragment:
    """Build the ``cron`` fragment by wrapping ``generate_cron_playbook``."""
    play = generate_cron_playbook(
        host_ip=HOSTS_SENTINEL,
        cron_jobs=cron_jobs,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    plays = [play]
    plays = _strip_ssh_vars(plays)
    for p in plays:
        p["hosts"] = HOSTS_SENTINEL
    return PlaybookFragment(module="cron", plays=plays)


def fragment_packages(packages: list[dict], repos: list[dict]) -> PlaybookFragment:
    """Build the ``packages`` fragment by wrapping ``generate_package_playbook``.

    The package generator returns a ``{"playbook": [...], "inventory": ...}``
    dict; the adapter discards the inventory (the orchestrator owns
    inventory) and keeps the play list.
    """
    result = generate_package_playbook(
        host_ip=HOSTS_SENTINEL,
        packages=packages,
        repos=repos,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    plays = list(result["playbook"])
    plays = _strip_ssh_vars(plays)
    for p in plays:
        p["hosts"] = HOSTS_SENTINEL
    return PlaybookFragment(module="packages", plays=plays)


def fragment_resolver(resolver_type: str, rendered_content: str) -> PlaybookFragment:
    """Build the ``resolver`` fragment by wrapping ``generate_resolver_playbook``.

    The resolver generator returns a ``{"playbook": [...], "inventory": ...}``
    dict; the adapter discards the inventory and keeps the play list.
    """
    result = generate_resolver_playbook(
        host_ip=HOSTS_SENTINEL,
        resolver_type=resolver_type,
        rendered_content=rendered_content,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    plays = list(result["playbook"])
    plays = _strip_ssh_vars(plays)
    for p in plays:
        p["hosts"] = HOSTS_SENTINEL
    return PlaybookFragment(module="resolver", plays=plays)


def fragment_linux_users(users: list, groups: list) -> PlaybookFragment:
    """Build the ``linux-users`` fragment by wrapping ``generate_user_playbook``."""
    play = generate_user_playbook(
        host_ip=HOSTS_SENTINEL,
        users=users,
        groups=groups,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    plays = [play]
    plays = _strip_ssh_vars(plays)
    for p in plays:
        p["hosts"] = HOSTS_SENTINEL
    return PlaybookFragment(module="linux-users", plays=plays)
