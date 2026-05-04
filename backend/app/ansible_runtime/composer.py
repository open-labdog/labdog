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

from app.ansible_runtime.generator import generate_playbook as generate_firewall_playbook
from app.cron.generator import generate_cron_playbook
from app.hosts_mgmt.generator import generate_hosts_file_playbook
from app.packages.generator import generate_package_playbook
from app.resolver.generator import generate_resolver_playbook
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.services.generator import generate_service_playbook
from app.user_mgmt.generator import generate_user_playbook

# SSH-related play.vars keys that some generators bake into the play.
# The orchestrator owns SSH wiring via the inventory, so adapters strip
# them post-call to keep fragments transport-agnostic. As of today
# (option-c entry), no generator actually populates play.vars with
# these keys — SSH wiring lives entirely in the inventory — so this
# strip is a contract guard against future regressions.
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

# Throwaway value passed to generators that require a key path. The
# orchestrator supplies the real key path via the inventory; adapters
# discard whatever the generator did with this stub. Today the
# generators only bake the path into inventory output that the adapter
# discards, so /dev/null is safe; if a future generator opens the file,
# it will see empty content — switch this to a tempfile if that ever
# becomes an issue.
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

# Map from per-module play ``name`` (as emitted by the generators) to the
# canonical module name. ``orchestrator._runner_events_to_task_events``
# uses this to resolve module identity from ansible-runner events:
# ansible-runner does not surface the per-task ``tags`` list on its
# ``runner_on_*`` result events (BUG-44), so we identify the module by
# the play name carried on every event instead.
#
# Every value here MUST appear in ``CANONICAL_ORDER``. Every key here
# MUST be the exact ``"name"`` field of a play emitted by one of the
# ``fragment_*`` adapters (or the underlying generator they wrap). Keep
# this map next to ``CANONICAL_ORDER`` so generator-side play names and
# the orchestrator-side reverse map can't drift independently — if you
# rename a play, this map must be updated in the same patch.
#
# The firewall module produces two distinct play names depending on
# backend (nftables vs iptables); both map to ``firewall``.
PLAY_NAME_TO_MODULE: dict[str, str] = {
    "LabDog Package Management": "packages",
    "LabDog DNS resolver sync": "resolver",
    "LabDog service management": "services",
    "LabDog /etc/hosts management": "hosts-file",
    "LabDog Cron Job Management": "cron",
    "LabDog Linux User Management": "linux-users",
    "Apply nftables firewall rules (safe mode)": "firewall",
    "Apply iptables firewall rules (safe mode)": "firewall",
}

HOSTS_SENTINEL = "target"


@dataclass(frozen=True)
class PlaybookFragment:
    """One module's contribution to the unified playbook.

    ``module`` must be a canonical module name (see ``CANONICAL_ORDER``).
    ``plays`` is a list of Ansible play dicts. Each play's ``hosts``
    field should be set to ``HOSTS_SENTINEL`` (``"target"``);
    ``compose_playbook`` rewrites it to the caller-supplied
    ``hosts_alias``.

    ``frozen=True`` only freezes attribute reassignment; the ``plays``
    list and its dicts are themselves mutable. Don't share fragments
    across threads or rely on input immutability after passing them in.
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
# host_ip and a throwaway SSH key path, normalizes the result into a
# list of plays, then hands off to ``_finalize`` which strips SSH vars
# and pins ``hosts`` to the sentinel. The orchestrator owns inventory
# and the real SSH key — the generator's stub call exists only to
# produce play structure.
# ---------------------------------------------------------------------------


def _finalize(plays: list[dict[str, Any]], module: str) -> PlaybookFragment:
    """Strip SSH vars from each play, pin ``hosts`` to the sentinel, return the fragment.

    Mutates ``plays`` and its inner dicts in place. Adapters always
    pass freshly-built lists, so caller-visible mutation is not an
    issue.
    """
    for play in plays:
        play_vars = play.get("vars")
        if isinstance(play_vars, dict):
            for key in list(play_vars.keys()):
                if key in _SSH_VAR_KEYS:
                    del play_vars[key]
            if not play_vars:
                play.pop("vars", None)
        play["hosts"] = HOSTS_SENTINEL
    return PlaybookFragment(module=module, plays=plays)


def fragment_cron(cron_jobs: list[dict]) -> PlaybookFragment:
    """Build the ``cron`` fragment by wrapping ``generate_cron_playbook``."""
    play = generate_cron_playbook(
        host_ip=HOSTS_SENTINEL,
        cron_jobs=cron_jobs,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    return _finalize([play], "cron")


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
    return _finalize(list(result["playbook"]), "packages")


def fragment_hosts_file(rendered_content: str, ssh_port: int = 22) -> PlaybookFragment:
    """Build the ``hosts-file`` fragment by wrapping ``generate_hosts_file_playbook``.

    The hosts_mgmt generator returns a ``(playbook_yaml, inventory_json)``
    tuple; the adapter parses the YAML, discards the inventory string
    (the orchestrator owns inventory), and keeps the play list.
    """
    playbook_yaml, _inventory = generate_hosts_file_playbook(
        host_ip=HOSTS_SENTINEL,
        ssh_port=ssh_port,
        rendered_content=rendered_content,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    return _finalize(yaml.safe_load(playbook_yaml), "hosts-file")


def fragment_firewall(
    backend: str,
    rules: list[FirewallRuleSpec],
    policies: ChainPolicies | None = None,
) -> PlaybookFragment:
    """Build the ``firewall`` fragment by wrapping ``generate_firewall_playbook``.

    The firewall generator dispatches on ``backend`` (``"nftables"`` or
    ``"iptables"``) and returns a YAML playbook string. The firewall
    plays already use ``"target"`` (== ``HOSTS_SENTINEL``) but
    ``_finalize`` rewrites unconditionally to keep the contract
    uniform across adapters.
    """
    playbook_yaml = generate_firewall_playbook(
        backend=backend,
        host_ip=HOSTS_SENTINEL,
        rules=rules,
        ssh_key_path=_UNUSED_KEY_PATH,
        policies=policies,
    )
    return _finalize(yaml.safe_load(playbook_yaml), "firewall")


def fragment_services(services: list[dict], ssh_port: int = 22) -> PlaybookFragment:
    """Build the ``services`` fragment by wrapping ``generate_service_playbook``.

    The services generator returns a ``(playbook_yaml, inventory_json)``
    tuple; the adapter parses the YAML, discards the inventory string,
    and keeps the play list. The play's non-SSH ``vars``
    (``allowed_unit_paths`` / ``allowed_override_paths`` used by the
    cleanup tasks) are preserved by the SSH-key allowlist.
    """
    playbook_yaml, _inventory = generate_service_playbook(
        host_ip=HOSTS_SENTINEL,
        ssh_port=ssh_port,
        services=services,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    return _finalize(yaml.safe_load(playbook_yaml), "services")


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
    return _finalize(list(result["playbook"]), "resolver")


def fragment_linux_users(users: list[dict], groups: list[dict]) -> PlaybookFragment:
    """Build the ``linux-users`` fragment by wrapping ``generate_user_playbook``."""
    play = generate_user_playbook(
        host_ip=HOSTS_SENTINEL,
        users=users,
        groups=groups,
        ssh_key_path=_UNUSED_KEY_PATH,
    )
    return _finalize([play], "linux-users")
