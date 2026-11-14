# S-002: Extension hook: gate a run before it starts, and hear when it ends

| | |
|---|---|
| Edition | shared |
| Source | roadmap, split from the board item "Maintenance and change-freeze windows" (the Community-side hook) |
| Tier / score | P0 / 7.0 |
| Stage | 1 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

An installed extension can register one gate that is asked before a sync, an action run,
a schedule dispatch or an AI fix starts, and can answer allow, refuse or defer. It can
also register a listener that is told when each of them ends. With no extension, nothing
changes. Enterprise maintenance windows and change tickets will be the first users.

## Scope

In scope:
- `backend/app/run_gate.py`: the request, verdict and end-event types, the two
  `register_*` functions, and `check_run()` / `notify_run_ended()`.
- Four gate points, one per kind, each at the moment work is about to start.
- A held state for a deferred sync or action run that does not block the host queue, and
  a periodic retry.
- End notifications after commit.
- A `PLUGIN_API` minor bump.

Out of scope:
- Any policy. Community registers no gate.
- A UI for holds beyond the existing "pending, with a reason" display.
- Gating drift sweeps, state collection and other read-only work.
- Gating at API request time (see Assumptions).

## Context

- Per-host serialisation lives in `backend/app/tasks/host_lock.py`:
  `check_host_busy` (line 228), `dispatch_next_pending_for_host` (line 670),
  `_oldest_pending_op` (line 764) and its three queries `_pending_sync_job` (794),
  `_pending_action_run` (819), `_pending_host_run` (861). A pending row is woken only when
  another op on the same host finishes.
- Syncs: `SyncJob(` is constructed in more than ten places (`backend/app/api/sync.py`,
  the per-module `*_sync.py` files, `_group_sync.py`, `tasks/gitops.py`,
  `sync/post_run.py`, `tasks/builtin_dispatchers.py`, `ca_certs/actions.py`). They all
  start through `_claim_or_defer` in `backend/app/tasks/host_sync_orchestrator.py`
  (line 260), which already stamps `SyncJob.pending_reason`
  (`backend/app/models/sync_job.py`, line 43). `_finalise_run` (line 490) writes the end.
  CA certificate deploys have their own `_claim_or_defer` in
  `backend/app/tasks/ca_cert_action.py`.
- Action runs: the four creators (see C-008) all send
  `app.tasks.action_orchestrator.run_action`. `_start_run`
  (`backend/app/tasks/action_orchestrator.py`, line 379) calls `_claim_queued_run`
  (line 445), which flips `queued` to `running` under a row lock. `ActionRun` has
  `pending_reason` (`backend/app/models/action_run.py`). Ends are written in several
  places: `finalise_run_if_complete` (131), `_mark_run_failed` (976),
  `_finalise_soft_limited` (999), `_mark_run_cancelled` (1056), the early exits in
  `_start_run`, `backend/app/tasks/action_group.py` (`_aggregate_and_finalise` 1650,
  `_mark_run_failed` 1822, `_mark_run_cancelled` 1857), `cancel_run` in
  `backend/app/api/actions.py` (762) and `backend/app/tasks/action_sweeper.py`.
- The sweepers: `action_sweeper.py` fails a run that stays `queued` for an hour and
  leaves `pending` alone; `sync_sweeper.py` fails stuck jobs. A held row must survive both.
- Schedules: `_tick_schedule` in `backend/app/tasks/scheduled_action_schedule.py`
  (line 107) checks `_held_by_in_flight_run`, then `_blocked_by_owner` (line 186), then
  calls `_dispatch_scheduled_run` (line 261). `_blocked_by_owner` is the model for a
  refusal: it moves `last_dispatched_at` on and audits the reason once.
- AI: both runners call `decide()` from `backend/app/ai/gate.py` (line 79):
  `backend/app/ai/loop.py` line 416 and `backend/app/ai/agent_sdk/runner.py` line 409.
  `GateDecision.needs_approval` parks the session for a human. The classification is one
  of `read_only`, `mutating`, `denied`, `unknown` (`backend/app/ai/safety.py`, line 32).
  Sessions close through `finish_session` in `backend/app/ai/service.py` (line 294).
- `backend/app/db_after_commit.py` gives `run_after_commit(db, callback)`.
- Extensions load in the API process and in every worker (`backend/app/tasks/__init__.py`,
  line 140). `PLUGIN_API` is "1.2" (`backend/app/auth/permissions.py`, line 71).
- The latest migration is `backend/alembic/versions/0060_schedule_owner.py`.

## Dependencies

- None on other items. S-001 is its neighbour: whichever merges second takes the next
  `PLUGIN_API` minor.
- S-013 and the Enterprise users of this hook come later and must not need a contract
  change, so the reviewer should read this spec with them in mind.

## Implementation steps

1. Create `backend/app/run_gate.py`:
   - `RunRequest` (frozen dataclass): `kind` (`"sync" | "action_run" |
     "schedule_dispatch" | "ai_fix"`), `host_ids: tuple[int, ...]`, `group_id`,
     `action_key`, `modules`, `user_id`, `sync_job_id`, `action_run_id`,
     `scheduled_action_id`, `ai_session_id`, `dry_run`.
   - `Verdict` (frozen): `outcome` (`"allow" | "refuse" | "defer"`), `reason: str`,
     `retry_at: datetime | None`. Helpers `ALLOW`, `refuse(reason)`,
     `defer(reason, retry_at=None)`.
   - `RunGate = Callable[[AsyncSession, RunRequest], Awaitable[Verdict]]`. A gate may read
     through the session and must not commit or write.
   - `register_run_gate(gate)`, `register_run_end_listener(fn)`, `reset_run_gate()`.
     Gates are asked in registration order; the first answer that is not allow wins.
   - `async check_run(db, request) -> Verdict`: returns `ALLOW` at once when no gate is
     registered. A gate that raises is logged and counts as `refuse("run gate failed")`.
   - `RunEnded` (frozen): `kind`, the same ids, `status`, `finished_at`.
     `notify_run_ended(db, event)` registers delivery with `run_after_commit`; each
     listener runs in its own `try`/`except` and is synchronous, like S-001's subscribers.
2. Migration `0061_run_gate_hold.py`: add nullable `gate_retry_at`
   (`DateTime(timezone=True)`) to `sync_jobs` and `action_runs`. Additive, with a
   downgrade that drops both.
3. Sync gate, in `_claim_or_defer` after `check_host_busy` returns `None` and before
   returning `True`. Skip the gate when `SyncJob.origin_action_host_run_id` is set (the
   inner sync of `_builtin.sync`: its parent run was gated).
   - refuse: set the job `failed` with `error_message = "Refused: <reason>"` and
     `completed_at`, commit, then hand the host on with the existing release helper so the
     next pending op starts.
   - defer: keep `pending`, set `pending_reason = reason` and `gate_retry_at`
     (the verdict's `retry_at`, or now plus 60 seconds), commit, and hand the host on.
   Do the same in `ca_cert_action._claim_or_defer` with `kind="sync"` and
   `modules=("ca_certs",)`.
4. Action run gate, in `_start_run` right after `_claim_queued_run` returns the locked
   run and before `_lookup_action`:
   - refuse: the same writes as the "unknown action key" branch, with
     `error_message = "Refused: <reason>"`.
   - defer: set `status="pending"`, `pending_reason`, `gate_retry_at`, clear
     `started_at`, `orchestrator_id` and `heartbeat_at`, commit, return `None`.
   Read `_dispatch_pending_action_run` (`host_lock.py`, line 989) first and reuse the way
   it sends a pending run back through the orchestrator.
5. In the three `_pending_*` queries in `host_lock.py`, skip rows whose `gate_retry_at`
   is in the future, so a held row never sits at the head of a host's queue. Clear
   `gate_retry_at` when a row is claimed.
6. New beat task `app.tasks.run_gate_retry.retry_held` every 60 seconds (register it the
   way `scheduled_action_schedule.py` registers its tick, and route it to a queue a
   worker consumes; `tests/test_task_routing.py` checks this). It finds pending rows with
   `gate_retry_at <= now` and re-sends each through the same dispatch helpers the host
   release uses. The claim asks the gate again.
7. Schedule gate, in `_tick_schedule` after `_blocked_by_owner` and after the action
   lookup, before `_dispatch_scheduled_run`:
   - refuse: like `_blocked_by_owner`: set `last_dispatched_at = now`, write one
     `scheduled_action.gate_refused` audit row with the reason, commit, return a new
     counter `skipped_gate`.
   - defer: return `deferred_gate` without touching `last_dispatched_at`, so the fire
     stays due and the gate is asked again on the next tick.
   The run a schedule dispatches is asked again as an `action_run`, with
   `scheduled_action_id` set so a gate can recognise it.
8. AI gate: add `async def gate_mutation(db, session, tool_name, arguments, decision)` to
   `backend/app/ai/gate.py`. When `decision.allowed` and
   `decision.classification == "mutating"`, it calls `check_run` with `kind="ai_fix"`.
   refuse returns a `GateDecision(allowed=False, reason=...)`; defer returns one with
   `needs_approval=True` when the session's autonomy is `full_auto`, so the session parks
   for a human, and a plain refusal with the reason otherwise. Call it right after
   `decide()` in both runners. Keep `decide()` itself synchronous and unchanged.
9. End notifications: call `notify_run_ended` where each terminal status is written.
   Sync: `_finalise_run` and the sweeper. Action run: every site listed in Context.
   Schedule dispatch has no end of its own (its run does). AI fix: in `finish_session`,
   when the session has at least one executed `AIToolCall` classified `mutating`.
10. Bump `PLUGIN_API` by one minor with a comment line naming `app.run_gate`, and update
    the extensions section of `CLAUDE.md`.
11. Ask for review before merge (class B). The reviewer checks steps 3 to 5 against
    BUG-80, BUG-102 and BUG-117, which are described in the comments of the files touched.

## Acceptance criteria

- With no gate registered, the full suite passes and `check_run` runs no query.
- A test gate that refuses makes: a sync job end `failed` with the reason and the next
  pending job on that host start; an action run end `failed` with the reason and no
  `ActionHostRun` rows; a due schedule skip that fire with one audit row; a mutating AI
  tool call return the refusal text without running.
- A test gate that defers makes: a sync job and an action run stay `pending` with the
  reason and a `gate_retry_at`, while a later op on the same host still runs; the retry
  task starts them once the gate allows; a schedule stay due and dispatch on the first
  tick after the gate allows; a full-auto AI session park for approval.
- A held row is not failed by either sweeper.
- A listener receives exactly one `RunEnded` for each finished sync, action run
  (succeeded, failed, cancelled, refused) and mutating AI session, after the commit, and
  none for a rolled-back write. A raising listener changes nothing else.
- A gate that raises refuses the run and logs the exception.
- The inner sync of `_builtin.sync` is not asked.
- `alembic upgrade head` and `alembic downgrade -1` both work.

## Test plan

- New files `backend/tests/test_run_gate.py` (types, `check_run`, listeners),
  `test_run_gate_sync.py`, `test_run_gate_actions.py`, `test_run_gate_schedule.py` and
  `backend/tests/ai/test_run_gate_ai.py`. Each resets the gate in a fixture.
- `cd backend && source .venv/bin/activate && pytest tests/test_run_gate*.py tests/ai/test_run_gate_ai.py -q`
- The neighbours: `pytest tests/test_action_host_locking.py tests/test_action_group_locking.py tests/test_action_sweeper.py tests/test_scheduled_action_schedule.py tests/test_task_routing.py tests/test_orchestrator_queue.py -q`
- `ruff check . && ruff format --check .`; the full suite once before the stage review.
- On local Docker on lin-manager-2, with a small test plugin whose gate defers for two
  minutes: start a sync and an action run against a host named "test" and watch both
  wait and then run. No other host is used, and nothing is tried on lin-manager.

## Docs and changelog

- `CLAUDE.md`: the extensions section and one sentence in "Per-host serialization" about
  held rows.
- `docs/ui/operations.md`: one sentence that a pending run can show a reason given by an
  installed extension.
- `CHANGELOG.md`, `[Unreleased]`, "Added": "Extension hook to refuse or hold a sync, an
  action run, a scheduled run or an assistant change before it starts, and to hear when
  it ends. A Community install behaves exactly as before. Plugin API 1.3 (or 1.4)."

## Risks and rollback

- The host queue is the most bug-prone code in the project. A held row that is still a
  queue candidate would stall every later op on that host; step 5 and its test are the
  guard.
- A run-end site that is missed gives a silent gap. The action-run test must drive every
  terminal path listed in Context.
- Failing closed on a broken gate can stop all work until the extension is removed. That
  is the safe direction for a gate, and removing the package restores Community behaviour.
- Rollback: revert the commits and run `alembic downgrade -1`. Rows held at that moment
  stay `pending` and are picked up by the normal host release.

## Assumptions

- Assumption (not yet confirmed): the gate is asked when work is about to start, not when
  it is requested, because that is the one place every creator passes. The cost is that a
  refused API request answers 201 and the run then fails with the reason. Question for
  Dennis: should `POST /api/actions/runs` and the sync endpoints also ask first and
  answer 409?
- Assumption (not yet confirmed): a gate that raises refuses.
- Assumption (not yet confirmed): a deferred AI fix becomes an approval request under
  full auto and a refusal otherwise, and the AI end event is per session.
- Assumption (not yet confirmed): gate and listener see ids, not rows, and read what they
  need themselves.

## Definition of done

- [ ] `app/run_gate.py`, migration 0061, four gate points, retry task, end notifications.
- [ ] The five new test files pass; locking, sweeper and routing tests still pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, docs and `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-002 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
