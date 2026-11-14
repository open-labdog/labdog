# Stage 1: A complete audit trail and the first extension hooks

Goal: every change unbork makes on a host or to its own rules leaves an audit row, and an extension can subscribe to the audit stream and gate a run, without Community knowing who is listening.

## Entry criteria

- Stage 0 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-008](../items/C-008-audit-rows-for-action-runs-who-started.md) | Audit rows for action runs: who started what | A | S | none |
| 2 | [C-047](../items/C-047-audit-rows-for-firewall-rule-changes-bug.md) | Audit rows for firewall rule changes (BUG-147) | A | S | none |
| 3 | [C-022](../items/C-022-harden-the-alert-webhook.md) | Harden the alert webhook | A | S | none |
| 4 | [S-001](../items/S-001-extension-hook-subscribe-to-audit-entries.md) | Extension hook: subscribe to audit entries | B | S | C-008 |
| 5 | [S-002](../items/S-002-extension-hook-gate-a-run-before-it.md) | Extension hook: gate a run before it starts, and hear when it ends | B | M | none |
| 6 | [S-015](../items/S-015-extension-hook-register-celery-tasks-and-periodic.md) | Extension hook: register Celery tasks and periodic jobs | B | S | none |
| 7 | [S-016](../items/S-016-extension-hook-declare-additional-permissions.md) | Extension hook: declare additional permissions | B | S | none |
| 8 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 1 | | | all of the above |

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 1` is done, `planning/reviews/stage-1.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] Starting an action run from the API and from a schedule each writes an audit row naming who started it, and a guard test fails when a new place creates a run without one. Cancelling a run is audited too.
- [ ] Creating, editing, deleting and reordering a firewall rule each writes an audit row; BUG-147 is deleted from `BUGS.md`.
- [ ] The alert webhook rejects what the item's spec says it must reject, with tests.
- [ ] A test extension registered through the new audit hook receives every committed audit entry and none that was rolled back; a raising subscriber never breaks the request.
- [ ] A test extension registered through the run gate can refuse and can defer a sync, an action run, a schedule dispatch and an AI fix, and is told when each ends.
- [ ] `PLUGIN_API`'s minor is raised once per hook added here, with the comment above it saying what each added; a Community install with no extension behaves exactly as before.
- [ ] A test extension can register a Celery task and a periodic job, and both run in the worker and the beat process.
- [ ] A test extension can declare a permission of its own, a route can require it, and `tests/test_permissions.py` still covers every route.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
