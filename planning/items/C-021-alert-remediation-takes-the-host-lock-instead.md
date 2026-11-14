# C-021: Alert remediation takes the host lock instead of refusing

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Alert remediation hardening: take the host lock, harden the webhook" (first half) |
| Tier / score | P1 / 6.5 |
| Stage | 6 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
While the assistant runs a command that changes a host, that host is claimed in the same per-host queue that syncs and action runs use. A sync or run that arrives meanwhile waits and is started when the command ends, and the assistant waits its turn when the host is already busy.

## Scope
In scope:
- A claim row per mutating AI command, read by `check_host_busy`.
- Claim before the snapshot, release and dispatch-next after the command, on every exit path.
- A reaper for claims left by a dead worker.

Out of scope:
- Hardening the alert webhook (C-022, the other half of the board item).
- Holding the host for a whole session. Model thinking time must not block syncs.
- Read-only commands, which stay outside the queue as drift sweeps' reads do.
- `propose_action` (C-009), which goes through an ordinary action run.

## Context
- `backend/app/ai/alert_autonomy.py`, `busy_refusal` (line 316): before a mutating command in an unattended remediation, asks `check_host_busy` and refuses when a sync, action run or rollback holds the host. Its docstring states the gap: "This narrows the overlap rather than closing it: a sync can still start while the command is running." It is also called without first taking the host's advisory lock, which `check_host_busy` requires.
- Call sites: `AgentLoop._rollback_point` in `backend/app/ai/loop.py` (line 525), which runs `busy_refusal` and then `snapshot_if_mutating`; the same pair in `backend/app/ai/agent_sdk/runner.py` (line 570); and `execute_approved` in `backend/app/ai/approvals.py` (line 164), which snapshots and runs an approved command with no busy check at all.
- `backend/app/tasks/host_lock.py`: `acquire_host_lock` (117), `check_host_busy` (228) with four sources, source 0 being a `running` `AIRollback`; `BlockerInfo` (83); `format_pending_reason` (589); `release_host_queue` (1062), which dispatches the oldest pending op per host.
- The pattern to copy is the AI rollback: `prepare_rollback` in `backend/app/ai/remediation.py` (line 818) takes the lock through `host_busy` (607), inserts a `running` `AIRollback` row as the claim, and its caller releases the queue when done. `wait_until_free` (630) polls for up to `BUSY_WAIT_SECONDS` (600) at `BUSY_POLL_SECONDS` (30), committing between looks.
- A session driven by `_builtin.ai_task` already holds its host through its own action run (`session.action_run_id`); `busy_refusal` passes `exclude_action_run_id` for that reason.
- The SDK runner holds `_db_lock` for the whole of a tool call, so a long wait inside a tool blocks its cancel and cap checks.
- Stale-state handling for AI rows lives in `backend/app/ai/session_housekeeping.py` and the sweepers in `backend/app/tasks/` (`action_sweeper.py`, `sync_sweeper.py`).

## Dependencies
- None. Latest Community migration today is `0060_schedule_owner.py`; use the next free number.
- `host_lock.py` is the most failure-prone module in the code base (BUG-117 class). Keep the change there to one added source and one label.

## Implementation steps
1. Migration `backend/alembic/versions/00NN_ai_host_claims.py` and model `AIHostClaim` in `backend/app/ai/models.py`: `id`, `session_id` (FK `ai_sessions`, CASCADE), `host_id` (FK `hosts`, CASCADE), `tool_call_id` (FK `ai_tool_calls`, SET NULL), `status` (`running | released`), `claimed_at`, `released_at`. Partial unique index on `host_id` where `status = 'running'`.
2. `host_lock.py`, `check_host_busy`: add source 0b after the rollback scan: a `running` `AIHostClaim` for the host returns `BlockerInfo(kind="ai_command", id=<session id>, host_id=host_id)`. Add the kind to `format_pending_reason` ("Waiting for AI session N's command on host X") and to the label maps in `remediation.host_busy` and `alert_autonomy.busy_refusal`.
3. New module `backend/app/ai/host_claim.py`:
   - `claim(db, session, host_id, tool_call_id) -> AIHostClaim | str | None`. Returns None without a row when the session's own action run already holds this host (`session.action_run_id` set and the host is that run's target). Otherwise, in one transaction: `acquire_host_lock`, `check_host_busy`; when free, insert the row and commit; when busy, commit and return the blocker's description.
   - `claim_or_wait(...)`: calls `claim`, and when busy polls as `wait_until_free` does, up to a new constant `COMMAND_WAIT_SECONDS = 120`, then returns the refusal text that `busy_refusal` produces today.
   - `release(db, claim)`: set `released`, commit, then `await release_host_queue([host_id], after=f"ai_session_id={session.id}")`. Closing the row before releasing is the BUG-117 rule.
4. Replace `busy_refusal` at the two runner call sites: for a `mutating` classification with an integer `host_id`, call `claim_or_wait` before `snapshot_if_mutating`. A refusal costs no snapshot, as today. Run the tool, then `release` in a `finally` that also covers a snapshot failure and a handler exception. In the SDK runner, do the wait outside `_db_lock`, or if that is not possible there, do not wait in that runner: claim once and refuse when busy.
5. `approvals.execute_approved`: same claim before `snapshot_before_change`, release in a `finally`. When the host stays busy past the wait, return a failed `ToolResult` saying the approved command was not run because the host was busy, so `resume_prompt` tells the model and the operator can approve again.
6. Keep `busy_refusal` as the function that formats the refusal, and delete its own `check_host_busy` call, so there is one code path.
7. Reaper: in the existing AI housekeeping beat task, release any `running` claim older than the SSH command timeout plus a margin whose session is terminal or whose tool call has finished, and call `release_host_queue` for its host. Log each one.
8. Update the docstrings that say AI sessions are not participants, and the "Per-host serialization" section of `CLAUDE.md`.

## Acceptance criteria
- While a mutating AI command runs on host H, a sync requested for H becomes `pending` with a reason naming the AI session, and starts on its own when the command ends.
- While a sync runs on H, a mutating AI command waits; when the sync ends inside the wait it runs, and when it does not the tool returns the "not run" refusal and no snapshot was taken.
- After the command, no `running` claim exists for H, whether the command succeeded, failed, raised, or the snapshot failed.
- A session started by `_builtin.ai_task` for H runs mutating commands on H without deferring behind its own run and without creating a claim.
- An approved command goes through the same claim.
- A claim orphaned by a killed worker is released by the reaper and the host's queue moves on.
- Read-only commands create no claim.
- `tests/test_action_host_locking.py`, `tests/test_action_group_locking.py` and the sync orchestrator tests pass unchanged.

## Test plan
- New `backend/tests/ai/test_host_claim.py`: the claim, wait, release and reaper cases against the real `check_host_busy` with `SyncJob` and `ActionRun` rows.
- Extend `backend/tests/ai/test_alert_autonomy.py`, `tests/ai/test_loop_paths.py`, `tests/ai/test_agent_sdk_runner.py` and `tests/ai/test_approvals.py` for the call sites.
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_host_claim.py tests/ai/test_alert_autonomy.py tests/ai/test_loop_paths.py tests/ai/test_agent_sdk_runner.py tests/ai/test_approvals.py tests/test_action_host_locking.py tests/test_action_group_locking.py tests/test_drift_sweep.py -q`, then the full suite. The `tests/ai` database tests need Docker for testcontainers.
- Running stack on lin-manager-2, host named "test": start a full-auto chat that runs `sleep 60 && touch /tmp/x`, request a sync during it, and watch the sync wait and then run. Then the reverse.

## Docs and changelog
- `docs/ui/assistant.md` and `docs/ui/alerts.md`: replace "refuses while unbork is changing the host" with the new behaviour and the wait.
- `CHANGELOG.md` `[Unreleased]`, Changed: "**AI commands take part in the per-host queue.** A command that changes a host claims it for as long as it runs; syncs and action runs wait for it, and it waits up to two minutes for them."

## Risks and rollback
- A claim that is never released blocks every sync and run on that host. The `finally`, the partial unique index and the reaper are the three protections; the reaper test is not optional.
- Interactive sessions now wait or are refused where they used to run alongside a sync. That is the point, but it is a visible change for operators.
- Waiting inside a tool call delays cancel in the SDK runner (step 4).
- Rollback: revert the commit and downgrade the migration. With the table gone, `check_host_busy` must not reference it, so revert code and schema together.

## Assumptions
- Assumption (not yet confirmed): the claim is per command, not per session. The board allows either ("a claim for the session (or for each command)").
- Assumption (not yet confirmed): the claim applies to every mutating AI command (unattended, full-auto chat, approved), not only unattended remediation, because the race is the same. Question for Dennis: should interactive sessions keep running alongside a sync?
- Assumption (not yet confirmed): a two-minute wait, then the existing refusal. Unattended sessions have lower wall-clock caps, so the 600 seconds used for rollbacks is too long here.

## Definition of done
- [ ] Table, `check_host_busy` source, claim module, three call sites and reaper merged behind review.
- [ ] New and extended tests pass; full backend suite green.
- [ ] Docs, `CLAUDE.md` section and `CHANGELOG.md` updated.
- [ ] Stage 6 behaviour 7 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
