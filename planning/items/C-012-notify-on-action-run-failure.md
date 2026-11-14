# C-012: Notify on action-run failure

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Notification events: action-run failures, sync failures, drift, certificate expiry" (section "Action-failure notifications") |
| Tier / score | P1 / 15.0 |
| Stage | 4 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal

A user who subscribes to "Action run failed" gets one email when an action run ends failed or partly failed, whether a schedule, a person or an alert started it. A scheduled run can no longer fail unnoticed.

## Scope

In scope:

- One new event type, `action_run_failed`, and its message.
- A call at every place where a run reaches a terminal `failed` or `partial` status.

Out of scope:

- Sync failures, drift and certificate expiry (C-013).
- Other channels than email (C-039).
- A notification for a successful run, for a cancelled run, or per host.
- Changing who is subscribed. Nobody is until they choose it.

## Context

- `backend/app/notifications/events.py`: `EVENT_TYPES` is the one table of events (six today); `EVENT_KEYS` is derived from it. The module docstring states the pattern: add an entry, call `notify()` where it happens.
- `backend/app/notifications/service.py::notify(db, event_type, *, subject, body, link_path, dedupe_key)` (line 102) queues one outbox row per subscriber inside a savepoint, does nothing while SMTP is off, never raises at run time, and ignores a repeat of the same `dedupe_key` per user (`uq_notifications_user_dedupe`). `notify_alert_action` (line 505) is the closest existing message and shows the house style (`one_line`, `clean`, `_hostname`, `link_path=f"/actions/runs/{run.id}"`).
- `event_type` columns in `backend/app/notifications/models.py` are plain `String(64)`, so a new event needs no migration.
- A run reaches a terminal failure in these places:
  - `backend/app/tasks/action_orchestrator.py`: `finalise_run_if_complete` (line 131, status written at 201), `_start_run` (397, unknown action or target), `_mark_run_failed` (976), `_finalise_soft_limited` (999).
  - `backend/app/tasks/action_group.py`: `_fail_run_at_load` (530), `_aggregate_and_finalise` (1650), `_mark_run_failed` (1822).
  - `backend/app/tasks/action_sweeper.py` (about lines 216 to 250, stale runs).
  - `backend/app/tasks/ai_task.py` (about line 624).
- `ActionRun` (`backend/app/models/action_run.py`) has `action_key`, `target_kind`, `target_label`, `scheduled_action_id`, `alert_event_id`, `status`, `error_message`, `started_at`, `finished_at`. `ActionHostRun` has `hostname`, `status`, `error_message`. `backend/app/api/actions.py` uses `failure_reason(...)` to pull the first failed task's message out of a host's log.
- Runs started by an alert already send the `alert_action` event from `backend/app/tasks/alert_handling.py` (line 214).
- The notifications page builds its checkbox list from the API, which serves `EVENT_TYPES`. `docs/ui/notifications.md` lists the events by hand.

## Dependencies

- None. Email must be configured (SMTP settings and `notifications.public_url`) for anything to be sent; that exists.

## Implementation steps

1. `backend/app/notifications/events.py`: add `EventType("action_run_failed", "Action run failed", "An action run ended failed or partly failed: a scheduled run, one somebody started, or one an alert started.")`.
2. `backend/app/notifications/service.py`: add `async notify_action_run_failed(db, run) -> int`.
   - Return 0 unless `run.status in ("failed", "partial")`.
   - Load the run's `ActionHostRun` rows. Count failed and total. List up to ten failed hosts as `hostname: reason`, where the reason is `failure_reason` of the host's output when it gives one, else the host run's `error_message`, each through `one_line`.
   - Name the action from `ACTION_REGISTRY` like `notify_alert_action` does, falling back to the key.
   - Subject: `<action name> failed on <target_label>` or `<action name> partly failed on <target_label> (<n> of <m> hosts)`, with ` (scheduled)` appended when `scheduled_action_id` is set.
   - Body lines: action key, target, how it was started (schedule name if the schedule still exists, "by an alert", or "by hand"), status, started and finished through `_when`, the run's `error_message` through `clean(..., 1000)` when set, then the failed hosts. If C-004 and C-006 have landed, add one line per host that was rolled back or awaits a rollback confirmation, from `rollback_state`.
   - `link_path=f"/actions/runs/{run.id}"`, `dedupe_key=f"action_run_failed:{run.id}"`.
3. Add one small helper beside it, `async notify_if_run_failed(db, action_run_id)`, which loads the run and calls step 2, so task code needs one line and no imports of the model.
4. Call the helper at each terminal site listed under Context, after the status and `finished_at` are committed, in the same session where one is open. The dedupe key makes a second call for the same run harmless, so do not try to prove that only one site fires. Do not call it for `cancelled`.
5. Keep it out of the lock path: in `action_host.py` and `action_group.py` the run is closed before the host is released (BUG-117). The helper runs after the close and must not delay or prevent the release; wrap the call so an unexpected error is logged and swallowed, as `notify` already does for its own work.
6. Tests in `backend/tests/test_notify_action_run_failed.py`, using the existing notification test helpers (see `backend/tests` files that exercise `notify_alert_action`): a failed run queues one row per subscriber with the expected subject, link and dedupe key; a partial run names the count; a succeeded or cancelled run queues nothing; a second call queues nothing more; a scheduled run's subject says so; with SMTP off nothing is queued; a host's failure reason appears and is one line. One test per terminal site is not needed; add one for `finalise_run_if_complete` (the normal path), one for the group path `_aggregate_and_finalise`, and one for the sweeper.
7. Extend the test that pins the event list, if one exists (search the tests for `EVENT_KEYS` or `alert_remediation`), with the new key.

## Acceptance criteria

- `GET` of the notification events (the route the notifications page uses) lists `action_run_failed` with its label and description.
- A subscriber gets exactly one queued email for a run that ends `failed` or `partial`, and none for `succeeded` or `cancelled`.
- The email names the action, the target, how the run was started and the failed hosts with a reason each, and links to `/actions/runs/<id>`.
- A failed run started by a schedule produces the email with no person involved (the stage behaviour).
- A failure inside the notification code leaves the run's status and the host release untouched.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_notify_action_run_failed.py -q`, then the existing notification and action tests: `pytest tests/ -q -k "notif or action_orchestrator or action_group or sweeper"`; full suite once before the stage review; `ruff check . && ruff format --check .`.
- Frontend: no code change expected; `cd frontend && npx tsc --noEmit && npm run lint` to confirm.
- Running stack: on local Docker on `lin-manager-2`, with SMTP pointed at a local mail catcher, subscribe, create a schedule for the `example` action against a host named "test" with a parameter that makes it fail, wait for the tick, and read the mail and its link.

## Docs and changelog

- `docs/ui/notifications.md`: add the event to the list, with when it fires. `docs/ui/scheduled-actions.md`: one sentence pointing at it.
- `CHANGELOG.md` `[Unreleased]`, Added: "New notification event \"Action run failed\": an email when an action run ends failed or partly failed, including scheduled runs."

## Risks and rollback

- A large group run that fails on many hosts must stay one email; the body caps the host list at ten and states the total.
- An alert-started run that fails now produces two emails for a user subscribed to both events. That is accepted (see Assumptions).
- Back out by reverting the commits. Existing subscriptions to the event become rows for an unknown key; check how the subscription API treats an unknown key before reverting and delete those rows in the same change if it rejects them.

## Assumptions

- Assumption (not yet confirmed): every failed or partial run notifies, not only scheduled ones. The board item is "action-run failures", and a person who started a run by hand may have left the page. Question for Dennis if hand-started runs should be left out.
- Assumption (not yet confirmed): alert-started runs are not excluded, although `alert_action` also reports them, because the two events have separate subscribers.
- Assumption (not yet confirmed): `partial` counts as a failure for this event.
- Assumption (not yet confirmed): a dry run that fails notifies like any other run.

## Definition of done

- [ ] Event, message, helper and call sites merged.
- [ ] New tests green, ruff clean.
- [ ] `docs/ui/notifications.md`, `docs/ui/scheduled-actions.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
