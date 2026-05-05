"""Built-in pseudo-actions registered alongside pack-supplied actions.

The three built-ins mirror operations that previously lived behind
dedicated buttons or per-tab Celery tasks:

- ``_builtin.sync`` — coalesced per-host sync (option-c). Replaces the
  per-tab "Sync" buttons.
- ``_builtin.drift_check`` — drift-check across hosts/groups/fleet.
- ``_builtin.collect_state`` — refresh cached module state via SSH.

They live in the registry alongside pack-supplied actions so the
unified scheduler at ``app.tasks.scheduled_action_schedule.check_due``
and the ``/api/scheduled-actions`` API can treat them identically.
The leading ``_builtin.`` prefix is reserved (manifest validation
rejects pack keys starting with ``_``); built-ins always win on key
collision because they're applied last in
``app.actions.registry.reload_registry``.

Per-host dispatch is handled by the routing in
``app.tasks.action_orchestrator.run_action``; built-ins have no
playbook on disk so ``ActionDefinition.playbook_path`` is ``None``.
"""

from __future__ import annotations

from app.actions.types import ActionDefinition, ActionParameter

BUILTIN_PACK_NAME = "_builtin"


SYNC = ActionDefinition(
    key="_builtin.sync",
    name="Sync host modules",
    description=(
        "Apply the desired module state to one host or every host in a "
        "group. Calls the same coalesced per-host orchestrator (option-c) "
        "as the per-tab Sync buttons."
    ),
    icon="refresh-cw",
    playbook_path=None,
    version="1.0.0",
    estimated_duration="1–5 min/host",
    destructive=True,
    supports_group=True,
    supports_host=True,
    # Bulk fleet-wide sync is rarely intended — operators should be
    # explicit about which hosts to push state to.
    supports_fleet=False,
    parameters=(
        ActionParameter(
            key="module_filter",
            label="Modules",
            type="string",
            default="",
            help_text=(
                "Comma-separated list of module names "
                "(firewall,services,packages,…). Empty applies every module."
            ),
        ),
    ),
    pack_name=BUILTIN_PACK_NAME,
)


DRIFT_CHECK = ActionDefinition(
    key="_builtin.drift_check",
    name="Check drift",
    description=(
        "Compare current host state against desired state and record any "
        "drift. Read-only — never modifies hosts."
    ),
    icon="search-check",
    playbook_path=None,
    version="1.0.0",
    estimated_duration="< 1 min/host",
    destructive=False,
    supports_group=True,
    supports_host=True,
    supports_fleet=True,
    parameters=(),
    pack_name=BUILTIN_PACK_NAME,
)


COLLECT_STATE = ActionDefinition(
    key="_builtin.collect_state",
    name="Collect host state",
    description=(
        "SSH into hosts and refresh their cached module state. Used by the "
        "current-state tabs in host detail."
    ),
    icon="database-zap",
    playbook_path=None,
    version="1.0.0",
    estimated_duration="< 1 min/host",
    destructive=False,
    supports_group=True,
    supports_host=True,
    supports_fleet=True,
    parameters=(),
    pack_name=BUILTIN_PACK_NAME,
)


BUILTIN_DEFINITIONS: tuple[ActionDefinition, ...] = (SYNC, DRIFT_CHECK, COLLECT_STATE)


def register_builtins(registry: dict[str, ActionDefinition]) -> None:
    """Add the built-in pseudo-actions to an existing registry dict.

    Built-ins overwrite same-keyed pack entries (defence-in-depth — the
    underscore-prefix gate in ``ActionManifest`` should keep this from
    ever happening).
    """
    for defn in BUILTIN_DEFINITIONS:
        registry[defn.key] = defn
