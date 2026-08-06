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


#: Shared by both AI actions. ``mission`` is the only required input; the
#: rest narrow what the session may do and what it may spend.
_AI_PARAMETERS: tuple[ActionParameter, ...] = (
    ActionParameter(
        key="mission",
        label="What to investigate",
        type="string",
        default="",
        required=True,
        help_text=(
            "Describe the check in plain language, e.g. 'Report any service "
            "that failed to start since the last reboot, and whether disk "
            "usage grew in the last day.'"
        ),
    ),
    ActionParameter(
        key="autonomy_level",
        label="Autonomy",
        type="choice",
        default="read_only",
        choices=("read_only", "approval", "full_auto"),
        help_text=(
            "read_only investigates and reports without changing anything. "
            "Raise it only for a check you would be comfortable running "
            "unattended at 3am."
        ),
    ),
    ActionParameter(
        key="provider_id",
        label="AI provider",
        type="int",
        default=0,
        help_text=(
            "Which configured provider to use; 0 uses the default. A "
            "recurring check is a good place to pick a local model — it runs "
            "every night whether or not anything is wrong."
        ),
    ),
    ActionParameter(
        key="allowed_tools",
        label="Permitted tools",
        type="string",
        default="",
        help_text=(
            "Comma-separated tool names, or blank for all of them. Naming a "
            "subset bounds both cost and reach: a log sweep limited to "
            "query_loki cannot open an SSH session at all. Options: "
            "run_ssh_command, query_loki, query_mimir, query_mimir_range."
        ),
    ),
)


#: Per-host AI investigation. Targeting a group fans out into one
#: independent session per member — right for "check each of these",
#: wrong for "compare these", which is what the group variant below is for.
AI_TASK = ActionDefinition(
    key="_builtin.ai_task",
    name="AI check (per host)",
    description=(
        "Hand an investigation to the configured AI provider, once per "
        "target host. Each host gets its own session with only that host in "
        "scope. Every command it runs is classified, bounded, and audited."
    ),
    icon="bot",
    playbook_path=None,
    version="1.0.0",
    estimated_duration="1–10 min/host",
    # Destructive so a raised autonomy level inherits the snapshot/rollback
    # envelope rather than needing a second, weaker one.
    destructive=True,
    supports_group=True,
    supports_host=True,
    # A fleet-wide AI sweep is a large, easy-to-regret spend; make the
    # operator name a group.
    supports_fleet=False,
    parameters=_AI_PARAMETERS,
    pack_name=BUILTIN_PACK_NAME,
)


#: Whole-group AI investigation. ``supports_host=False`` is what selects
#: single-invocation dispatch in ``app.tasks.action_orchestrator`` — the
#: shape is read off the ActionDefinition, not the run, which is why this
#: has to be a separate key rather than a parameter on the one above.
AI_TASK_GROUP = ActionDefinition(
    key="_builtin.ai_task_group",
    name="AI check (whole group)",
    description=(
        "Hand an investigation to the configured AI provider once, with "
        "every member of the target group in scope at the same time. Use "
        "this to compare hosts or find the odd one out — questions a "
        "per-host run cannot answer, because those sessions cannot see "
        "each other."
    ),
    icon="bot",
    playbook_path=None,
    version="1.0.0",
    estimated_duration="2–15 min",
    destructive=True,
    supports_group=True,
    supports_host=False,
    supports_fleet=False,
    parameters=_AI_PARAMETERS,
    pack_name=BUILTIN_PACK_NAME,
)


BUILTIN_DEFINITIONS: tuple[ActionDefinition, ...] = (
    SYNC,
    DRIFT_CHECK,
    COLLECT_STATE,
    AI_TASK,
    AI_TASK_GROUP,
)


def register_builtins(registry: dict[str, ActionDefinition]) -> None:
    """Add the built-in pseudo-actions to an existing registry dict.

    Built-ins overwrite same-keyed pack entries (defence-in-depth — the
    underscore-prefix gate in ``ActionManifest`` should keep this from
    ever happening).
    """
    for defn in BUILTIN_DEFINITIONS:
        registry[defn.key] = defn
