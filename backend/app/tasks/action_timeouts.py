"""Shared deadline arithmetic for action runs.

Single source of truth for "how long may this action legitimately
take?", used by three consumers that must never drift apart:

* the executors (``action_host`` / ``action_group``) — wall-clock
  ``timeout=`` passed to ansible-runner;
* the orchestrator — per-child Celery soft/hard time limits and its
  own batch-join timeout;
* the stale-run sweeper (``action_sweeper``) and the scheduler's
  wedged-in-flight guard — "past this age the worker is presumed
  dead".

Kept import-light (no module-level app imports) so it can be pulled
into any task module without circular-import risk.
"""

from __future__ import annotations

import math

#: Used when the ``ansible.playbook_timeout`` setting can't be read
#: (DB down, tests without settings) and when an action key is missing
#: from the registry (pack removed after dispatch). Matches the
#: executors' historical fallback.
FALLBACK_TIMEOUT_SECONDS = 1800

#: Verify-timeout assumed for registry misses. Matches the
#: ``ActionDefinition.verify_timeout_seconds`` default.
FALLBACK_VERIFY_TIMEOUT_SECONDS = 300

#: Headroom on top of playbook + verify for the rest of the per-host
#: envelope: Proxmox snapshot, rollback, cleanup, SSE/DB writes.
ENVELOPE_GRACE_SECONDS = 900

#: Gap between a task's Celery soft limit and its hard (SIGKILL)
#: limit — the window the soft-limit handler has to finalise DB rows.
HARD_LIMIT_MARGIN_SECONDS = 300

#: Slack added to the whole-run deadline for batch boundaries,
#: queueing delays, and post-run sync dispatch.
RUN_DEADLINE_SLACK_SECONDS = 3600


def effective_playbook_timeout(playbook_timeout_floor: int | None) -> int:
    """Wall-clock timeout for one ansible-runner invocation.

    ``max(global setting, per-action manifest floor)`` — the global
    ``ansible.playbook_timeout`` setting can widen an action's budget
    but never shrink it below the manifest's
    ``playbook_timeout_seconds`` floor.
    """
    from app.settings_service import get_setting_sync_typed  # noqa: PLC0415

    try:
        timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
    except Exception:
        timeout = FALLBACK_TIMEOUT_SECONDS
    if playbook_timeout_floor:
        timeout = max(timeout, playbook_timeout_floor)
    return timeout


def _registry_lookup(action_key: str):
    try:
        from app.actions.registry import ACTION_REGISTRY  # noqa: PLC0415

        return ACTION_REGISTRY.get(action_key)
    except Exception:
        return None


#: Built-in actions whose work is bounded by ``ai.wall_clock_seconds``
#: rather than by an ansible-runner timeout.
_AI_ACTION_KEYS = frozenset({"_builtin.ai_task", "_builtin.ai_task_group"})


def _ai_wall_clock_floor() -> int:
    """The AI wall-clock cap, as a floor for the per-host deadline.

    The AI actions run no playbook, so ``ansible.playbook_timeout`` says
    nothing about how long they legitimately take — their own cap does.
    Reading the setting here rather than hardcoding a floor on the
    ActionDefinition keeps the two from drifting: raising
    ``ai.wall_clock_seconds`` widens the deadline automatically instead of
    silently exceeding it.
    """
    from app.settings_service import get_setting_sync_typed  # noqa: PLC0415

    try:
        return int(get_setting_sync_typed("ai.wall_clock_seconds"))
    except Exception:
        return 900


def per_host_deadline_seconds(action_key: str) -> int:
    """Upper bound on one host's whole envelope (snapshot → playbook →
    verify → rollback → cleanup).

    Deliberately generous: ansible-runner's own ``timeout`` always
    fires well before this, so any row still ``running`` past this age
    belongs to a dead worker, not a slow host.

    The AI actions are the exception — nothing else bounds them, so their
    floor comes from ``ai.wall_clock_seconds``. Keeping it derived rather
    than generous matters here: this deadline is also what the action
    sweeper uses to reap an orphaned run, and until it does, that host's
    queue stays blocked.
    """
    action = _registry_lookup(action_key)
    floor = action.playbook_timeout_seconds if action is not None else None
    if action_key in _AI_ACTION_KEYS:
        floor = max(floor or 0, _ai_wall_clock_floor())
    verify = (
        action.verify_timeout_seconds if action is not None else FALLBACK_VERIFY_TIMEOUT_SECONDS
    )
    return effective_playbook_timeout(floor) + verify + ENVELOPE_GRACE_SECONDS


def run_deadline_seconds(action_key: str, host_count: int, parallelism: int) -> int:
    """Upper bound on a whole ActionRun: sequential batches of
    ``parallelism`` hosts, each bounded by the per-host deadline,
    plus slack. ``parallelism <= 0`` means "all at once" (one batch).
    """
    hosts = max(1, host_count)
    batch_size = hosts if parallelism <= 0 else max(1, parallelism)
    batches = math.ceil(hosts / batch_size)
    return batches * per_host_deadline_seconds(action_key) + RUN_DEADLINE_SLACK_SECONDS
