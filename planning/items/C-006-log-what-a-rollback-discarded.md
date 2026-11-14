# C-006: Log what a rollback discarded

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Basic rollback safety" (section "Log what a rollback discarded") |
| Tier / score | P1 / 10.0 |
| Stage | 4 |
| Effort | S |
| Autonomy | A |
| Depends on | C-004 |

## Goal

After an action run rolls a host back, the run's output and the audit log state which snapshot was restored, when it was taken, and therefore how much time of writes on that host was thrown away.

## Scope

In scope:

- A "discarded" summary in the run output and one audit row per rolled-back host, for the per-host executor, the group executor and the confirmed rollback from C-004.
- The same fields exposed on the run API so C-007 can show them.

Out of scope:

- Listing individual files, rows or packages that changed. unbork does not know them, and the record says so.
- The AI session rollback. It already writes an `ai_rollback` audit row in `backend/app/ai/remediation.py::perform_rollback`; BUG-178 (C-007 records it) covers showing the snapshot's age there.
- Notifications about rollbacks.

## Context

- `backend/app/tasks/action_host.py::_rollback` (line 1108) logs `[rollback] restoring <name>` and `[rollback] success=<bool>`, then deletes the snapshot. It writes no audit row and no time information. The file has no `log_action` call.
- `backend/app/tasks/action_group.py::_rollback_member` (line 1527) does the same into `ctx.step_log`.
- Snapshot names embed the creation time: `labdog-<run_id>-<unix_ts>` (`backend/app/workflows/steps/snapshot.py`, line with `name = f"{name_prefix}-{run_id}-{int(time.time())}"`). After S-003, `SnapshotHandle.created_at` carries it, and after C-044 `SnapshotHandle.include_ram` says whether memory came back too.
- `backend/app/audit/logger.py::log_action(db, action, entity_type, entity_id, user_id, before_state, after_state, ip_address)` is the audit writer. It drops a `user_id` whose account is gone.
- C-004 adds `ActionHostRun.rollback_state` and the task `app.tasks.action_rollback.run_confirmed_rollback`.
- `HostRunEntry.rolled_back` (`backend/app/schemas/actions.py:170`) is the only rollback fact the API returns today.

## Dependencies

- C-004 (the `rollback_state` column and the confirmed-rollback task). S-003 and C-044 are used when present; without them the time is parsed from the snapshot name and memory is reported as not restored.

## Implementation steps

1. New `backend/app/actions/rollback_record.py`:
   - `@dataclass RollbackRecord(host_id, hostname, snapshot_name, snapshot_taken_at, rolled_back_at, discarded_seconds, memory_restored, trigger, succeeded, error)` where `trigger` is `automatic` or `confirmed`.
   - `snapshot_time(name, handle=None) -> datetime | None`: the handle's `created_at`, else the trailing integer of the name, else None.
   - `summary_lines(record) -> list[str]`, for example: `[rollback] <hostname> is back at <snapshot_taken_at> UTC. Everything written to it in the <14m32s> since then was discarded, including this run's changes.` and, when memory was restored, `[rollback] running processes were restored from the snapshot's memory.`; otherwise `[rollback] the machine was started fresh from the snapshot's disks.` When the time is unknown, say so instead of guessing.
   - `async record_rollback(db, record, *, action_run_id, host_run_id, user_id)`: calls `log_action(db, "action_rollback", "action_run", action_run_id, user_id, before_state=None, after_state={...})` with every field of the record plus `host_run_id` and `action_key`.
2. Migration `backend/alembic/versions/00NN_host_run_rollback_record.py` (next free number): add nullable `action_host_runs.rolled_back_at TIMESTAMPTZ`, `snapshot_taken_at TIMESTAMPTZ`, `rollback_discarded_seconds INTEGER`. Add them to `ActionHostRun`.
3. `action_host.py::_rollback`: capture the time before the revert starts, build the record after `rollback_to_snapshot` returns, append `summary_lines` through `_log_step`, set the three columns and call `record_rollback` in the same session and commit. A failed rollback is recorded too (`succeeded=False`, with the error) and discards nothing; say that in the line. `user_id` is `spec.triggered_by_user_id`.
4. `action_group.py::_rollback_member`: the same, with lines appended to `ctx.step_log` and the audit row written in the member's short session.
5. `app/tasks/action_rollback.py` (from C-004): the same, with `trigger="confirmed"` and the confirming user's id.
6. `backend/app/schemas/actions.py`: add `rolled_back_at`, `snapshot_taken_at` and `rollback_discarded_seconds` to the host-run output models, and fill them in `backend/app/api/actions.py` where `rolled_back` is filled (about line 526) and in the run detail.
7. Tests in `backend/tests/test_rollback_record.py`: `snapshot_time` for a normal name, a name with no timestamp and a handle; the summary text for both memory cases and for the unknown-time case; the per-host and group executors write one `action_rollback` audit row per rolled-back host with the expected `after_state`; a failed rollback writes `succeeded=false`; a successful run writes none; the API returns the three fields.

## Acceptance criteria

- After a failed destructive run that was rolled back, the host run's output contains a line naming the snapshot time and the discarded duration.
- The audit log has exactly one `action_rollback` row for that host and run, whose `after_state` holds `host_id`, `hostname`, `snapshot_name`, `snapshot_taken_at`, `rolled_back_at`, `discarded_seconds`, `memory_restored`, `trigger` and `succeeded`.
- In a group run where two members roll back, there are two rows.
- A confirmed rollback (C-004) is recorded with `trigger=confirmed` and the confirming user.
- `GET /api/actions/runs/{id}` returns the three new fields for a rolled-back host and nulls otherwise.
- No audit row and no new line appear for a run that did not roll back.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_rollback_record.py tests/test_action_host.py tests/test_action_host_phases.py tests/test_action_group.py tests/test_action_group_paths.py -q`; full suite once before the stage review; `ruff check . && ruff format --check .`.
- Running stack: on local Docker on `lin-manager-2`, reuse the failing `example` run against a VM named "test" from C-004 or C-044 and read the run page and the audit page.

## Docs and changelog

- `docs/ui/actions.md`, "Destructive actions and snapshot safety": one paragraph on the discarded-window line and the audit row. `docs/ui/admin.md` if it lists audit actions.
- `CHANGELOG.md` `[Unreleased]`, Added: "A rollback now records what it discarded: the run output and the audit log state the snapshot's time and how much write time on the host was thrown away."

## Risks and rollback

- The wording could suggest unbork knows what was written. The line states a time window only.
- Clock difference between unbork and the hypervisor could make the window slightly wrong. The snapshot name's timestamp comes from unbork's own clock, so both ends use the same clock.
- An audit write must never break a rollback. Wrap `record_rollback` so a failure is logged and the rollback result stands.
- Back out by reverting the commits and running the migration's `downgrade()`. Audit rows already written stay (the audit log is append-only).

## Assumptions

- Assumption (not yet confirmed): "what was discarded" means the time window and whether memory was restored, because unbork has no record of the writes themselves. Question for Dennis if a list of unbork's own operations on the host in that window (syncs, other runs) is wanted as well. For an action run the host is locked for the whole window, so that list would be this run alone.
- Assumption (not yet confirmed): the audit entity is the action run (`entity_type="action_run"`), so the row sits beside the run's other audit rows.

## Definition of done

- [ ] Record module, migration, three call sites and API fields merged.
- [ ] Tests green, ruff clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
