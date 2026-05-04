"""Pure helpers for the coalesced per-host sync orchestrator.

Two contract-only functions used by the orchestrator (v0.2.0):

- ``determine_modules_to_run`` resolves an API-supplied
  ``module_filter`` to a canonical-ordered list of modules the
  orchestrator should sync.

- ``aggregate_module_outcomes`` groups ansible-runner task events by
  their module tag and reports per-module success/failure.

Pure library code: no DB, no Celery, no I/O. The orchestrator that
wires these into a Celery task lives elsewhere.
"""

from __future__ import annotations

from typing import Any

from app.ansible_runtime.composer import CANONICAL_ORDER

ModuleOutcome = str  # "in_sync" | "error" | "no_tasks"


def determine_modules_to_run(
    module_filter: list[str] | None,
    all_modules: list[str] = CANONICAL_ORDER,
) -> list[str]:
    """Resolve ``module_filter`` against ``all_modules``.

    - ``module_filter=None`` → return ``all_modules`` unchanged.
    - Non-empty list → return the canonical-ordered intersection.
    - Empty list → raise ``ValueError`` (an empty filter would mean
      "sync nothing", which is never the operator's intent and is
      already rejected at compose time).
    - Any module in ``module_filter`` not in ``all_modules`` →
      ``ValueError``. Unknown filters are caller-side bugs and should
      surface immediately, not silently produce a no-op sync.
    """
    if module_filter is None:
        return list(all_modules)

    if len(module_filter) == 0:
        raise ValueError("determine_modules_to_run: module_filter must not be empty")

    unknown = [m for m in module_filter if m not in all_modules]
    if unknown:
        raise ValueError(f"determine_modules_to_run: unknown module(s) {unknown}")

    requested = set(module_filter)
    return [m for m in all_modules if m in requested]


def aggregate_module_outcomes(
    task_events: list[dict[str, Any]],
    modules_run: list[str],
) -> dict[str, ModuleOutcome]:
    """Group ansible-runner task events by their module tag, return per-module status.

    Each event in ``task_events`` is expected to have:
      - ``tags``: list of strings (one of which is the module name; injected
        by ``compose_playbook``'s ``_inject_tags`` pass).
      - ``failed``: bool. Truthy → module marked ``"error"``.
      - ``unreachable``: bool. Truthy → module marked ``"error"``.

    Per-module status:
      - ``"error"`` — at least one event tagged with the module had
        ``failed`` or ``unreachable`` true.
      - ``"in_sync"`` — at least one event tagged with the module, all
        succeeded.
      - ``"no_tasks"`` — module appears in ``modules_run`` but no event
        carries its tag (e.g. the play ran zero tasks because the
        desired state matched current state with idempotent ops).

    Modules not in ``modules_run`` are ignored even if their tag
    appears in ``task_events`` (defence against rogue tags). Modules
    in ``modules_run`` always appear in the output.
    """
    by_module: dict[str, ModuleOutcome] = {m: "no_tasks" for m in modules_run}
    relevant = set(modules_run)

    for event in task_events:
        tags = event.get("tags") or []
        failed = bool(event.get("failed")) or bool(event.get("unreachable"))
        for tag in tags:
            if tag not in relevant:
                continue
            if failed:
                by_module[tag] = "error"
            elif by_module[tag] == "no_tasks":
                by_module[tag] = "in_sync"
            # if already "in_sync" or "error", leave alone — error is
            # sticky (one failed task → module errored even if later
            # tasks succeed)

    return by_module
