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
