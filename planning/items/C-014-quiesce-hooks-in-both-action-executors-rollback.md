# C-014: Quiesce hooks in both action executors (rollback phase 2)

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Quiesce hooks for stateful hosts (rollback phase 2)" |
| Tier / score | P1 / 6.5 |
| Stage | 6 |
| Effort | M |
| Autonomy | B |
| Depends on | C-004 |

## Goal
A pack action can declare a quiesce playbook and an unquiesce playbook. Unbork runs the first before it snapshots the VM and the second after verify or rollback, on success and on failure, so nothing is written during the window and a revert loses nothing. A stateful host running such an action keeps automatic rollback.

## Scope
In scope:
- Two manifest fields and a timeout, loaded and validated like `verify_playbook`.
- The quiesce and unquiesce phases in `action_host.py` and `action_group.py`.
- Telling the main and verify playbooks that the host is quiesced, and the unquiesce playbook how the run ended.
- Relaxing the stateful default from C-004 when a quiesce succeeded.

Out of scope:
- Writing quiesce playbooks for any pack (C-015).
- Application rollback and cluster rejoin (C-027, parked).
- Quiescing for AI changes (`app/ai/snapshots.py`) and for syncs.

## Context
- `backend/app/actions/manifest.py`: `ActionManifest` has `verify_playbook` (line 102) and `verify_timeout_seconds` (line 114). `_reserved_underscore_prefix` (line 93) rejects parameter keys that start with an underscore.
- `backend/app/actions/packs.py` (lines 199 to 241) resolves `verify_playbook` to a path, refuses a missing file, calls `_refuse_sibling_references`, and stores `verify_playbook_path` on the definition (`backend/app/actions/types.py`, line 74).
- Per-host executor, `backend/app/tasks/action_host.py`, `_run_action_host_async` (line 1421): prologue, then `_phase_a_snapshot` (758), `_phase_b_playbook` (828), `_phase_d_verify` (929), `_phase_ef_rollback_or_cleanup` (1089), `_phase_g_persist` (1171), with `_on_soft_timeout`, `_on_failure` and a `finally` that calls `_release_host`. A dry run skips snapshot, verify and rollback. `_verify_with_pack_playbook` (953) shows how a second playbook is run and logged.
- Group executor, `backend/app/tasks/action_group.py`, `_run_envelope` (line 616): `_snapshot_phase`, `_run_group_playbook`, `_verify_all`, `_rollback_all`, `_cleanup_all`, `_persist_member_outcomes`. Members whose snapshot fails are dropped from the run.
- Budgets: `backend/app/tasks/action_timeouts.py` (`per_host_deadline_seconds`, `run_deadline_seconds`, `HARD_LIMIT_MARGIN_SECONDS`) sizes Celery limits from the playbook and verify timeouts.
- `run_ansible` (`backend/app/ansible_runtime/runner.py`, line 68) copies only the playbook file into the run directory; roles reach it through `roles_paths`.
- The board's design: "Manifest `quiesce_playbook` / `unquiesce_playbook`, run before the snapshot and after verify or rollback (maintenance mode, stop writers, checkpoint). No writes in the window, so a revert loses nothing." Trade-off noted there: planned downtime for the whole run, and only as good as the pack's quiesce.

## Dependencies
- C-004 (stage 4): the `stateful` flag and `rollback_mode` (`auto | confirm | never`) on hosts and groups, and the function that resolves the effective mode in Phase E. Read its code before step 6.
- The manifest is a public pack contract. The change is additive: old manifests load unchanged, and an older unbork ignores nothing it must not (check how `ActionManifest` treats unknown keys; if it forbids them, say in the docs which version a pack needs).

## Implementation steps
1. `manifest.py`: add `quiesce_playbook: str | None`, `unquiesce_playbook: str | None`, and `quiesce_timeout_seconds: int` (default 300, same bounds as `verify_timeout_seconds`). Validator: both playbooks or neither. Reject a parameter whose key is `unbork_quiesced` or `unbork_run_outcome`.
2. `packs.py` and `types.py`: resolve both paths exactly as `verify_playbook` is resolved, and add `quiesce_playbook_path`, `unquiesce_playbook_path`, `quiesce_timeout` to the definition. `backend/app/api/actions.py`, `_definition_to_out` and `ActionDefinitionOut`: add `quiesces: bool`.
3. `action_host.py`: add `_run_hook_playbook(ctx, spec, path, *, label, inventory_json, extra_vars, timeout) -> tuple[bool, str | None]`, factored out of `_verify_with_pack_playbook` (own data dir registered in `ctx.extra_data_dirs`, output appended to the step log under `=== Quiesce playbook output ===`, a `[quiesce]` or `[unquiesce]` step line).
4. Per-host order. The quiesce runs only when all of these hold: the action declares it, the run is not a dry run, the action is destructive, `spec.snapshot_enabled`, and the host has a VM mapping (a snapshot will be attempted).
   - Quiesce, before `_phase_a_snapshot`. On failure: run unquiesce, fail the host run with "quiesce failed", take no snapshot, run nothing else.
   - Snapshot. On failure: run unquiesce, then fail as today.
   - Main playbook and verify, with the extra-var `unbork_quiesced: true`.
   - Rollback decision (Phase E) as today.
   - Unquiesce, with `unbork_run_outcome` set to `succeeded`, `failed` or `rolled_back`.
   - Snapshot cleanup (Phase F) only after unquiesce succeeded. If unquiesce fails after a successful run, keep the snapshot, mark the host run failed with "unquiesce failed: the application may still be in maintenance", and do not roll back.
   Split `_phase_ef_rollback_or_cleanup` so the unquiesce sits between rollback and cleanup.
5. Failure paths: track `ctx.quiesced = True` from the moment the quiesce playbook starts. In `_on_soft_timeout`, `_on_failure` and before `_release_host` in the `finally`, run the unquiesce playbook if `ctx.quiesced` and it has not run yet, inside its own try/except so it can never mask the original error or stop the host being released. Add `2 * quiesce_timeout` to `per_host_deadline_seconds` and `run_deadline_seconds` for actions that declare the hooks, and check the unquiesce fits inside `HARD_LIMIT_MARGIN_SECONDS` after a soft timeout; if not, raise the margin for these actions.
6. Rollback policy: pass `quiesced=True` to C-004's effective-mode resolver. A `confirm` that comes only from the `stateful` default becomes `auto` for this run. `never`, and a `confirm` an operator set explicitly, are unchanged. Log the choice in the step log.
7. `action_group.py`, `_run_envelope`: run the quiesce playbook once against the members that have a VM mapping, before `_snapshot_phase`; members that fail it are marked failed and dropped, as snapshot failures are. Run the unquiesce playbook once against every member where quiesce was attempted, after `_rollback_all` and before `_cleanup_all`, and in the `except` branches of `_run_action_group_async` before `release_host_queue`. Per member, `_cleanup_all` skips a member whose unquiesce failed.
8. Frontend: `frontend/lib/types.ts` adds `quiesces`; `frontend/components/action-run-dialog.tsx` shows one line when it is true: "This action takes the application offline for the whole run."
9. Sample pack: add an action with both hooks to `backend/tests/fixtures/packs/sample` whose playbooks touch and remove a marker file.

## Acceptance criteria
- A manifest with only one of the two playbooks fails to load with a clear message; `validate_pack.py` in the pack repositories reports it.
- Per-host, success path, order in the step log: quiesce, snapshot, playbook, verify, unquiesce, snapshot deleted.
- Per-host, failing playbook with auto rollback: quiesce, snapshot, playbook, rollback, unquiesce with `unbork_run_outcome=rolled_back`.
- Quiesce failure: no snapshot is taken, unquiesce runs, the host run is failed, the host is released.
- An exception or soft timeout after quiesce still runs unquiesce and releases the host.
- Unquiesce failure after a successful run: host run failed, snapshot kept, no rollback.
- Dry run, non-destructive action, `snapshot_enabled=false`, or a host without a VM mapping: neither hook runs.
- The same five behaviours hold for a group-dispatch action.
- A stateful host (C-004) with the default mode rolls back automatically when the quiesce succeeded, and waits for confirmation when the action declares no quiesce.

## Test plan
- New `backend/tests/test_action_quiesce.py` covering the orders above for both executors with a fake `run_ansible`; extend `tests/test_action_host_phases.py`, `tests/test_action_group_paths.py`, `tests/test_action_timeouts.py` and the manifest loading tests in `tests/test_action_packs.py`.
- `cd backend && source .venv/bin/activate && pytest tests/test_action_quiesce.py tests/test_action_host_phases.py tests/test_action_group_paths.py tests/test_action_timeouts.py tests/test_action_packs.py -q`, then the full suite once before the stage review.
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.
- Real envelope on lin-manager-2 with a Proxmox-mapped VM named "test": run the sample action, once succeeding and once with a playbook made to fail, and confirm the marker file and the snapshot behave as the criteria say. Never on a host not named "test".

## Docs and changelog
- `docs/ui/actions.md`: manifest reference for the three fields, the order of phases, the two extra-vars, and the rule that the verify playbook must work while the host is quiesced.
- `docs/examples/action-packs/`: one starter pack with both hooks.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Quiesce hooks for destructive actions.** A pack can declare `quiesce_playbook` and `unquiesce_playbook`; unbork runs them around the snapshot window, also when the run fails. Stateful hosts keep automatic rollback for such actions."

## Risks and rollback
- A host left quiesced is an outage. Every exit path must reach unquiesce; the tests in step 5 are the protection, and the run's error message must say when it did not succeed.
- After a rollback the VM is restored to its quiesced state, so the unquiesce playbook must work on both the upgraded and the reverted system. That is a pack requirement; document it.
- Verify runs while the host is quiesced. A pack whose health check needs the application open to users cannot pass it. See Assumptions.
- Rollback: revert the commit. Manifests that already declare the hooks then fail validation or are ignored, depending on the unknown-key policy; note this in the changelog if reverting after release.

## Assumptions
- Assumption (not yet confirmed): unquiesce runs after verify, as the board says, so verify sees a quiesced host. Question for Dennis: is that the intent, given that the two C-015 actions check health over HTTP?
- Assumption (not yet confirmed): the hooks run only when a snapshot will be attempted. Without a snapshot there is nothing to make lossless.
- Assumption (not yet confirmed): an operator-set `confirm` is not relaxed; only the default that `stateful` implies is. Depends on whether C-004 stores the difference.
- Assumption (not yet confirmed): the extra-var names `unbork_quiesced` and `unbork_run_outcome`. If the rename (D-02) keeps a `labdog_` fallback for pack variables, pass both names.

## Definition of done
- [ ] Manifest fields, loader, both executors, timeouts and the dialog line merged behind review.
- [ ] `tests/test_action_quiesce.py` passes; full backend suite green.
- [ ] Docs, example pack and `CHANGELOG.md` updated.
- [ ] Stage 6 behaviour 5 verified on a VM named "test".
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
