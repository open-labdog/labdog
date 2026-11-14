# C-008: Audit rows for action runs: who started what

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Audit log UI: who started what and who changed which schedule" |
| Tier / score | P0 / 15.0 |
| Stage | 1 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal

Every action run leaves one audit row at the moment it is created, naming the run, its
target, and the user, schedule or alert that started it. Cancelling a run leaves a row too.

## Scope

In scope:
- One `action_run.started` audit row for every place that creates an `ActionRun`.
- One `action_run.cancelled` row from `POST /api/actions/runs/{id}/cancel`.
- A single helper that all creators call, and a guard test that fails when a new creator
  forgets it.
- A check that the Audit page shows the new rows readably.

Out of scope:
- A row when a run finishes. The run's own record holds the outcome, and S-002 adds a
  run-end hook for extensions.
- Schedule edits. `backend/app/api/scheduled_actions.py` already audits create, update,
  delete, owner change and dispatch.
- Any new audit page, filter or column.
- `POST /api/actions/refresh`, which changes no host.

## Context

- `backend/app/audit/logger.py` holds `log_action(db, action, entity_type, entity_id,
  user_id, before_state, after_state, ip_address)`. It adds an `AuditLog` row
  (`backend/app/models/audit_log.py`) to the caller's session and never commits.
- `ActionRun(` is constructed in exactly four places:
  1. `backend/app/api/actions.py`, `_insert_run` (line 305), called by `create_run`
     (`POST /api/actions/runs`, line 134). It writes no audit row. `log_action` is not
     imported in this file at all.
  2. `backend/app/api/scheduled_actions.py`, `run_now` (the `ActionRun(` is at line 652).
     It writes `scheduled_action.dispatched` on the schedule with `manual: True`.
  3. `backend/app/tasks/scheduled_action_schedule.py`, `_dispatch_scheduled_run`
     (line 261). It writes `scheduled_action.dispatched` with `user_id=None`.
  4. `backend/app/alerts/labels.py` (the `ActionRun(` is at line 245). It writes
     `alert_action.dispatched` on the alert-action entry.
- So a run started from the API has no audit row, and the three other paths audit a
  different entity (`scheduled_action`, `alert_action`), which makes "all runs, and who
  started each" impossible to read from the log with one filter.
- `cancel_run` in `backend/app/api/actions.py` (line 762) takes the user as `_` and writes
  no audit row.
- The assistant cannot start an action run today: nothing under `backend/app/ai/` creates
  an `ActionRun`. That arrives with C-009 (`propose_action`).
- Run parameters can carry operator input. `_stored_parameters` (line 217 of
  `actions.py`) shows they are validated against the manifest, but no field is marked
  secret, so values must not be copied into the audit log.
- `docs/ui/operations.md`, section "Audit" (line 96), lists the event names.

## Dependencies

None. S-001 depends on this item so that the audit stream it exposes is complete.

## Implementation steps

1. Create `backend/app/actions/run_audit.py` with:
   - `RunOrigin = Literal["api", "schedule", "run_now", "alert", "assistant"]`.
   - `async def audit_run_started(db, run, *, origin, user_id, ai_session_id=None,
     ip_address=None) -> None`. It calls `log_action` with `action="action_run.started"`,
     `entity_type="action_run"`, `entity_id=run.id`, `user_id=user_id`, and `after_state`
     holding: `action_key`, `action_version`, `target_kind`, `target_label`, `host_id`,
     `group_id`, `origin`, `scheduled_action_id`, `alert_event_id`, `ai_session_id`,
     `dry_run` (read from the parameters with the existing `DRY_RUN_PARAM` constant), and
     `parameter_names` (sorted keys only, never values).
   - `async def audit_run_cancelled(db, run, *, user_id) -> None`, writing
     `action="action_run.cancelled"` with `before_state={"status": <status before>}`.
   The run must be flushed first so `run.id` is set. The helper never commits.
2. Call `audit_run_started` in the same transaction as the insert, before the commit:
   - `_insert_run`: `origin="api"`, `user_id=user.id`.
   - `run_now`: `origin="run_now"`, `user_id=user.id`.
   - `_dispatch_scheduled_run`: `origin="schedule"`, `user_id=sa.owner_user_id` (the same
     value the run's `triggered_by_user_id` gets). `log_action` already turns a deleted
     user into `None`.
   - `alerts/labels.py`: `origin="alert"`, `user_id=None`.
   Keep the three existing `scheduled_action.dispatched` and `alert_action.dispatched`
   rows as they are. They describe the schedule and the alert entry, and tests and
   `docs/ui/operations.md` name them.
3. In `cancel_run`, rename `_` to `user`, and call `audit_run_cancelled` before the
   `db.commit()` at the end of the branch that changes the run. The early return for a run
   that is already finished writes nothing.
4. Add the guard test (see Test plan) that walks `backend/app/**/*.py` with `ast` and
   fails if a function that constructs `ActionRun(...)` does not also call
   `audit_run_started`. This is what makes C-009 audit assistant-started runs without
   anyone remembering to.
5. Open `/audit` on a dev stack and confirm an `action_run.started` row shows the user,
   and that its detail shows the action key and target. Read
   `frontend/app/(dashboard)/audit/` first. Change the page only if the row is unreadable
   (for example an entity type it does not know how to label).
6. Update docs and changelog (below).

## Acceptance criteria

- `POST /api/actions/runs` creates exactly one `audit_log` row with
  `action="action_run.started"`, `entity_type="action_run"`, `entity_id` equal to the new
  run id, `user_id` equal to the caller, and `after_state.origin == "api"`.
- A scheduler tick that dispatches a due schedule creates one such row with
  `origin="schedule"`, `scheduled_action_id` set and `user_id` equal to the schedule's
  owner; `POST /api/scheduled-actions/{id}/run-now` creates one with `origin="run_now"`
  and the caller's id.
- An alert whose label starts an action creates one with `origin="alert"`,
  `alert_event_id` set and `user_id` null.
- `audit_run_started(..., origin="assistant", ai_session_id=N)` writes a row carrying
  both values.
- No audit row contains a parameter value.
- A request refused before the insert (unknown action, 409 duplicate, 403) writes no row.
- `POST /api/actions/runs/{id}/cancel` on a queued run writes one `action_run.cancelled`
  row with the caller's id; on a finished run it writes none.
- The guard test fails if the `audit_run_started` call is removed from any of the four
  creators.

## Test plan

- New file `backend/tests/test_action_run_audit.py`, using the `client`, `db` and
  `sample_pack` fixtures from `backend/tests/conftest.py`. One test per creator, one for
  cancel, one for the refused request, one for the assistant origin, and the `ast` guard.
- `cd backend && source .venv/bin/activate && pytest tests/test_action_run_audit.py -q`
- Then the neighbours whose row counts may change:
  `pytest tests/test_scheduled_actions_api.py tests/test_scheduled_action_schedule.py tests/test_actions_runs_validation.py tests/ai/test_alert_labels.py tests/test_audit.py -q`
- `ruff check . && ruff format --check .`
- The full suite runs once before the stage review.
- Step 5 runs on local Docker on lin-manager-2, against a host named "test". No real host
  is needed for the automated tests.

## Docs and changelog

- `docs/ui/operations.md`, section "Audit": add `action_run.started` and
  `action_run.cancelled` to the list of domain events, and one sentence saying each run
  has one start row whose `origin` says where it came from.
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "The audit log records who started and
  who cancelled every action run, whether from the API, a schedule, Run now or an alert."

## Risks and rollback

- Tests that count audit rows for schedules or alerts may see one more row. Fix the
  assertion to filter by action name; do not delete the existing rows.
- The scheduler writes the row in the transaction that stores the run, so a failure in
  `log_action` would roll the dispatch back. `log_action` only adds a row and looks up one
  user, so the risk is the same as for the existing `scheduled_action.dispatched` call.
- Rollback: revert the commit. No migration, no data to undo. Rows already written stay,
  which is correct for an append-only log.

## Assumptions

- Assumption (not yet confirmed): the cancel row is wanted. The board item speaks only of
  who started a run; the stage goal says every change on a host leaves a row, and a cancel
  changes what happens on hosts. Question for Dennis: keep or drop `action_run.cancelled`?
- Assumption (not yet confirmed): parameter names are safe to log and values are not.
- The assistant path cannot be proven end to end in this stage, because C-009 is in a
  later stage. The stage behaviour "from the assistant" is covered here by the helper test
  and the guard test, and C-009 must add its own end-to-end test.
- The event name `action_run.started` follows the dotted style of
  `scheduled_action.dispatched`. Audit action names are not part of the versioning promise
  in `docs/upgrade.md`.

## Definition of done

- [ ] Helper, four call sites and the cancel row are in place.
- [ ] `tests/test_action_run_audit.py` passes, and the neighbouring files still pass.
- [ ] `docs/ui/operations.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml`: C-008 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
