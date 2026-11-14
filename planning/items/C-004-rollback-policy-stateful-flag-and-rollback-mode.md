# C-004: Rollback policy: stateful flag and rollback_mode on hosts

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Basic rollback safety: stateful hosts, rollback_mode, snapshot pre-check, log what was discarded" (phase 1) |
| Tier / score | P0 / 9.0 |
| Stage | 4 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

An operator can mark a host as stateful and choose how a failed destructive run is rolled back on it. A host whose mode is not `auto` is never reverted on its own: the run stops, keeps the snapshot, says why, and a person can confirm the revert later.

## Scope

In scope:

- `hosts.stateful` and `hosts.rollback_mode` (`auto`, `confirm`, `never`), with the API fields and an audit row when they change.
- Both action executors honour the mode in Phase E.
- An endpoint that carries out a confirmed rollback of one host run.
- The hourly snapshot reaper leaves a snapshot that awaits confirmation alone.
- The AI rollback paths refuse a `never` host and never revert a non-`auto` host automatically.

Out of scope:

- The same two settings on host groups (see Assumptions).
- The UI (C-007), the pre-check (C-005), the "what was discarded" record (C-006).
- A maximum snapshot age (BUG-178), guest-agent consistency (BUG-179), the etcd member rule (BUG-177). C-007 records them in `BUGS.md`.
- Quiesce hooks (C-014) and application rollback.

## Context

- `backend/app/models/host.py` (`Host`, line 53) has neither field. `backend/app/schemas/hosts.py` `HostUpdate` (line 88) and `PUT /api/hosts/{host_id}` (`backend/app/api/hosts.py:346`, `require("config.edit")`) apply `model_dump(exclude_none=True)` and write no audit row.
- Per-host executor: `backend/app/tasks/action_host.py`. `_RunSpec` (line 195) carries `auto_rollback`; `_load_run_spec` (line 480) fills it from `ActionRun.auto_rollback`; `_phase_ef_rollback_or_cleanup` (line 1089) calls `_rollback` (line 1108) whenever the run failed, a snapshot exists and `auto_rollback` is on.
- Group executor: `backend/app/tasks/action_group.py`. `_rollback_all` (line 1468) reverts every failed member that has a snapshot, one at a time, through `_rollback_member` (line 1527).
- `ActionHostRun` (`backend/app/models/action_run.py:170`) has `snapshot_name` and no field for the rollback outcome. `HostRunEntry.rolled_back` (`backend/app/schemas/actions.py:170`) is computed in `backend/app/api/actions.py` (about line 526).
- `backend/app/workflows/snapshot_cleanup.py` deletes `labdog-<run_id>-<ts>` snapshots of finished runs. The hourly sweep waits `workflow.snapshot_max_age_hours` first.
- `backend/app/tasks/host_lock.py::check_host_busy` (line 228) already counts an `AIRollback` with `status='running'` as a blocker. That is the precedent for a rollback that holds the host.
- AI: `backend/app/ai/remediation.py::check_rollback` (line 686) is the one place that refuses a session rollback. The automatic `made_worse` path creates rollbacks with `trigger="automatic"` (about lines 1090 and 1106).

## Dependencies

- None. C-005, C-006, C-007 and C-014 build on this item.
- Latest Community migration at the time of writing is `0060_schedule_owner.py`. Use the next free number.

## Implementation steps

1. Migration `backend/alembic/versions/00NN_host_rollback_policy.py`: add `hosts.stateful BOOLEAN NOT NULL DEFAULT false`, `hosts.rollback_mode VARCHAR(10) NULL`, and `action_host_runs.rollback_state VARCHAR(24) NULL`. Additive, with a working `downgrade()`.
2. `backend/app/models/host.py`: add `class RollbackMode(enum.StrEnum)` with `auto`, `confirm`, `never`, and the two columns. NULL `rollback_mode` means "default".
3. New `backend/app/actions/rollback_policy.py` with `effective_rollback_mode(host) -> RollbackMode`: the stored value if set, else `confirm` when `stateful`, else `auto`. Every caller uses this function. Nothing else reads the column.
4. `backend/app/schemas/hosts.py`: add `stateful: bool | None` and `rollback_mode: RollbackMode | Literal["default"] | None` to `HostUpdate` and `HostCreate`; add `stateful`, `rollback_mode` (nullable) and `effective_rollback_mode` to the response model. In `update_host`, map `"default"` to NULL (needed because of `exclude_none=True`) and write `log_action(db, "host_rollback_policy_changed", "host", host.id, user.id, before_state, after_state)` when either value changed. The route keeps `require("config.edit")`.
5. `backend/app/models/action_run.py`: add `ActionHostRun.rollback_state`. Values: `rolled_back`, `rollback_failed`, `awaiting_confirmation`, `kept` (mode `never` or `auto_rollback=false`), `rolling_back`, `declined`. Set `rolled_back` and `rollback_failed` in the existing paths too.
6. `action_host.py`: add `rollback_mode: str` to `_RunSpec`, filled in `_load_run_spec` from `effective_rollback_mode(host)`. In `_phase_ef_rollback_or_cleanup`, when the run failed, a snapshot exists and `auto_rollback` is on, but the mode is not `auto`: do not call `_rollback`. Log `[rollback] not run: <hostname> has rollback_mode=<mode>; snapshot <name> kept` plus one line that says what to do (`confirm` : confirm on the run page; `never` : restore it in the hypervisor by hand). Set `rollback_state` and append the same sentence to `ActionHostRun.error_message`.
7. `action_group.py`: load each member's mode once (extend `_HostCtx.__slots__` with `rollback_mode`). In `_rollback_all`, drop non-`auto` members from `needs_rollback` and log the same lines in their `step_log`. `_cleanup_all` already leaves failed members' snapshots.
8. `backend/app/workflows/snapshot_cleanup.py`: skip a snapshot whose `ActionHostRun.rollback_state` is `awaiting_confirmation` or `rolling_back`, in both the hourly sweep and the on-demand cleanup. A `kept` snapshot follows today's rules.
9. Confirmed rollback. New route in `backend/app/api/actions.py`: `POST /api/actions/runs/{run_id}/hosts/{host_run_id}/rollback`, `Depends(require("actions.run", scope=row_scope(ActionHostRun, "host_run_id")))`. It refuses with 409 unless the row is `awaiting_confirmation`, the host still exists, and `check_host_busy` returns None; it sets `rolling_back` under the host's advisory lock (`host_lock.py` helpers), writes an audit row `action_rollback_confirmed`, and dispatches a new task `app.tasks.action_rollback.run_confirmed_rollback` on the `actions` queue (add it to the routing table so `tests/test_task_routing.py` passes). A `DELETE` on the same path sets `declined` (same permission) and leaves the snapshot to the reaper.
10. `backend/app/tasks/action_rollback.py`: load the host run, build the snapshot target the same way `_load_envelope` does, call `rollback_to_snapshot`, delete the snapshot on success, set `rolled_back` or `rollback_failed`, append the `[rollback]` lines to `ActionHostRun.output`, release the host and dispatch the next pending op in `finally`.
11. `host_lock.py::check_host_busy`: add a source for `ActionHostRun.rollback_state == 'rolling_back'`, beside the `AIRollback` source, so nothing else starts on the host while it is being reverted.
12. `ai/remediation.py`: `check_rollback` raises `RollbackRefused` for a `never` host. The automatic path records a `refused` rollback (the existing refusal helper near line 880) for any host whose mode is not `auto`. A person pressing the button on a `confirm` host is already a confirmation, so that path is unchanged.

## Acceptance criteria

- A failed destructive run on a host with `stateful=true` and no explicit mode ends with the host run `failed`, `rollback_state=awaiting_confirmation`, the snapshot still present, and a `[rollback] not run` line in the output. `rollback_to_snapshot` was not called.
- The same with `rollback_mode=never` ends in `kept`, and the confirm endpoint returns 409.
- A host with `rollback_mode=auto` and `stateful=true` is reverted as today.
- In a group run with one `auto` and one `confirm` member that both fail, only the `auto` member is reverted.
- The confirm endpoint reverts the host, refuses a second call, refuses a busy host, and is refused for a role without `actions.run`.
- The reaper does not delete a snapshot that awaits confirmation.
- Changing either field writes one audit row with before and after values.
- `tests/test_permissions.py` and `tests/test_permission_scopes.py` pass with the new routes.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_rollback_policy.py tests/test_action_rollback_confirm.py -q` (new files), then `pytest tests/test_action_host.py tests/test_action_host_phases.py tests/test_action_group.py tests/test_action_group_paths.py tests/test_snapshot_cleanup.py tests/ai/test_remediation.py tests/test_permissions.py tests/test_task_routing.py -q`.
- Full suite once before the stage review: `pytest tests/ -q`, `ruff check . && ruff format --check .`.
- Real host: on local Docker on `lin-manager-2`, against a VM named "test" marked stateful, run the `example` action from `unbork-playbooks` so that verify fails. Check that the VM is not reverted, then confirm and check that it is. No other host.

## Docs and changelog

- `docs/ui/actions.md`, section "Destructive actions and snapshot safety": the three modes and what the run shows. `docs/ui/hosts.md`: the two fields. C-007 adds the screenshots.
- `CHANGELOG.md` `[Unreleased]`, Added: "Hosts can be marked stateful and given a rollback mode (auto, confirm, never). A failed destructive run no longer reverts a stateful host on its own."

## Risks and rollback

- A wrong default would stop automatic recovery fleet-wide. The default for an unmarked host stays `auto`, and a test pins it.
- A confirmed rollback that does not hold the host could run under a sync. Step 11 and its test cover that.
- Back out by reverting the commits and running the migration's `downgrade()`. Kept snapshots stay in the hypervisor and are reaped by age.

## Assumptions

- Assumption (not yet confirmed): the mode names are the board's `auto | confirm | never`. `stages/stage-4.md` says "manual"; this spec reads that as "any mode other than auto". Question for Dennis: keep three modes, or one `manual` mode?
- Assumption (not yet confirmed): this item covers hosts only, as its title says. The board sketch also puts both fields on `HostGroup` with "strictest of the host's groups". `effective_rollback_mode` is the single place where group inheritance would be added. Question for Dennis: should groups be in this item?
- Assumption (not yet confirmed): confirming needs `actions.run` on the host, the same right that started the run.
- The board's open question (may a pack mark its target stateful?) is not answered here: operator only.

## Definition of done

- [ ] Migration, model, schemas, both executors, confirm task and route, reaper and AI guards merged.
- [ ] New and existing tests green; ruff clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
