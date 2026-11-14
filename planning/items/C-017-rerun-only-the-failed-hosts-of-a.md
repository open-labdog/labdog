# C-017: Rerun only the failed hosts of a run, linked to the original

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Action runs: rerun a failed run, and save a run as a schedule" (first half) |
| Tier / score | P1 / 5.5 |
| Stage | 6 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
After a group run where some hosts failed, an operator starts a new run on exactly those hosts with one click. The new run records which run it retries, and the server refuses it when the action's current manifest no longer accepts the stored parameters.

## Scope
In scope:
- `action_runs.rerun_of_id` and a stored host subset for a run.
- `POST /api/actions/runs/{id}/rerun`, with server-side re-validation.
- "Rerun failed hosts" on the run page, and the link between the two runs in the run page, the Runs list and the audit log.

Out of scope:
- Retrying automatically. A rerun is always started by a person.
- Subset reruns of group-dispatch actions (`supports_host: false`), which are refused: their playbook coordinates the whole group.
- Changing the existing whole-target "Re-run…" dialog, other than sending it through the new endpoint so it is linked too.

## Context
- A client-side rerun already exists. `frontend/components/rerun-action-button.tsx` (`RerunActionButton`) opens `ActionRunDialog` with `rerunOf`, and `formValuesFromRun` in `frontend/components/action-run-dialog.tsx` (line 62) drops parameters the catalog no longer declares and shows a warning banner. It posts to the ordinary `POST /api/actions/runs`, so it always targets the whole host or group and the new run has no link to the old one. Screenshots of that flow: `/mnt/project-files/rerun-action/1-run-page.png` and `2-rerun-dialog.png`. Playwright spec: `frontend/e2e/run-rerun.spec.ts`.
- `backend/app/api/actions.py`: `create_run` (line 135) is built from `_runnable_action`, `_stored_parameters` (validates with `build_param_model(action).model_validate`), `_require_target`, `_refuse_duplicate_run`, `_insert_run` and `_dispatch_run`. `_insert_run` does not set `snapshot_enabled`, `verify_enabled` or `auto_rollback`, so ad-hoc runs take the column defaults.
- `backend/app/models/action_run.py`: `ActionRun` targets one `host_id` or one `group_id` (or neither, for a fleet run from a schedule). There is no way to store a subset of a group, and no `rerun_of_id`.
- `backend/app/tasks/action_orchestrator.py`: `_resolve_target_host_ids` (line 488) expands a group to all its members. `_send_group_task` hands `supports_host: false` actions to `app.tasks.action_group`, which claims every member in `_claim_or_defer_group`.
- Per-host results live in `ActionHostRun.status` (`queued | pending | running | succeeded | failed | skipped | cancelled`); `host_id` is NULL once the host was deleted.
- `create_run` writes no audit row today. Stage 1 (C-008) is expected to add run-start audit rows; check what it added before step 6.

## Dependencies
- None hard. Reads C-008's audit helper if stage 1 added one.
- Latest Community migration on today's tree is `0060_schedule_owner.py`. Use the next free number at the time of the work.

## Implementation steps
1. Migration `backend/alembic/versions/00NN_action_run_rerun.py`: add to `action_runs` a nullable `rerun_of_id` (`ForeignKey("action_runs.id", ondelete="SET NULL")`, indexed) and a nullable JSONB `target_host_ids`. Add both to `ActionRun` in `backend/app/models/action_run.py` with comments: `target_host_ids` narrows a group or fleet target to the listed hosts and is NULL for "the whole target".
2. `backend/app/schemas/actions.py`: add `RunRerunBody` (`failed_only: bool = True`, `parameters: dict | None = None`, `parallelism: int | None = None`, `dry_run: bool = False`, `metrics_destinations` as in `RunCreateBody`). Add `rerun_of_id: int | None`, `target_host_ids: list[int] | None` and `reruns: list[int]` (ids of runs that retry this one, filled by the detail endpoint only) to `ActionRunOut`.
3. `backend/app/api/actions.py`: add `POST /runs/{id}/rerun`, declaring `Depends(require("actions.run", scope=row_scope(ActionRun, "id")))`. The handler:
   - 404 when the run is missing; 409 when it is not terminal (`succeeded | partial | failed | cancelled`); 409 for a fleet run or a run whose host or group was deleted (same wording as the button's blockers).
   - Builds a `RunCreateBody` from the original (`action_key`, `host_id`, `group_id`), with `parameters` and `parallelism` from the request when given, else from the original with the `__` internal keys stripped.
   - Calls `_runnable_action` (unknown key, unresolved key, missing playbook, scope support) and `_stored_parameters`. A parameter the current manifest no longer accepts is a 422 with the pydantic errors. The server never drops a parameter silently; the dialog does the dropping and the operator sees it.
   - When `failed_only` is true and the original targeted a group: collect `ActionHostRun.host_id` where `status == "failed"` and `host_id IS NOT NULL`, keep those still in the group, and 409 with a clear message when the list is empty. 409 when `action.supports_host` is false ("this action runs once across the whole group; rerun the whole group"). For a host-targeted original, `failed_only` changes nothing.
   - Calls `require_on(user, "actions.run", hosts=<subset>)` for a subset, or the original target otherwise, then `_refuse_duplicate_run`.
   - Inserts through `_insert_run`, extended with keyword arguments `rerun_of_id`, `target_host_ids`, and the three safety flags copied from the original. `scheduled_action_id` and `alert_event_id` stay NULL: a rerun is an ad-hoc run. For a subset, set `target_label` to the group's label plus " (N failed hosts of run #id)", cut to 255 characters.
   - Dispatches with `_dispatch_run`.
4. `backend/app/tasks/action_orchestrator.py`: in `_resolve_target_host_ids`, when `run.target_host_ids` is set, return the intersection of the resolved members and that list, in the resolved order. In `_start_run`, an empty intersection keeps today's behaviour (run succeeds with no hosts) but sets `error_message` to say the hosts left the target.
5. `get_run` (line 556): fill `reruns` with the ids of runs whose `rerun_of_id` is this run. `list_runs`: return `rerun_of_id` (it is a plain column).
6. Audit: write one `log_action` row, action `action_run_rerun`, `entity_type="action_run"`, `entity_id=<new id>`, `after_state={"rerun_of_id", "failed_only", "host_ids"}`, in the same transaction as the insert. If C-008 added a run-start audit row, add `rerun_of_id` to its payload and do not write a second row.
7. Frontend, `frontend/lib/types.ts`: add the three fields to `ActionRun`. `frontend/components/action-run-dialog.tsx`: add a prop `rerunMode?: "all" | "failed"`; when `rerunOf` is set, `handleSubmit` posts to `/api/actions/runs/{rerunOf.id}/rerun` with `failed_only: rerunMode === "failed"`, and the note reads "Only the N hosts that failed in run #id" in failed mode. `frontend/components/rerun-action-button.tsx`: beside "Re-run…", render "Rerun failed hosts (N)" (`data-testid="rerun-failed"`) when the run targeted a group, at least one host run failed, and the action has `supports_host`; disabled with a title otherwise.
8. `frontend/components/action-run-detail.tsx`: under the title show "Rerun of #N" linking to the original, and "Rerun as #M" for each id in `reruns`. Runs list (`frontend/app/(dashboard)/runs/`): a small "rerun of #N" tag on the row.

## Acceptance criteria
- A group run with hosts A (succeeded), B and C (failed): `POST /api/actions/runs/{id}/rerun` with the default body creates a run whose `ActionHostRun` rows are exactly B and C, with `rerun_of_id` set and `target_host_ids == [B, C]`.
- A stored parameter the current manifest no longer declares, or a value it no longer accepts, gives 422 and no run is created.
- An unresolved (contested) action key gives 409 `action_unresolved`; a removed key gives 400.
- A run with no failed hosts gives 409 for `failed_only: true`; `failed_only: false` reruns the whole target and is still linked.
- A `supports_host: false` action gives 409 for `failed_only: true`.
- A user without `actions.run` on one of the failed hosts gets 403 (covered by the scoped stand-in resolver in `tests/test_permission_scopes.py`), and a viewer gets 403.
- `GET /api/actions/runs/{original}` lists the new run in `reruns`; the run page shows both links.
- Deleting the original run (retention) leaves the rerun in place with `rerun_of_id` NULL.

## Test plan
- New `backend/tests/test_actions_rerun.py`, using the `sample_pack` fixture: the cases above, plus the orchestrator intersection (`_resolve_target_host_ids` with a host removed from the group after the rerun was created).
- `cd backend && source .venv/bin/activate && pytest tests/test_actions_rerun.py tests/test_actions_runs_validation.py tests/test_permissions.py tests/test_permission_scopes.py tests/test_action_orchestrator_paths.py -q`
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test run-rerun.spec.ts` (extend the spec: a mocked partial run shows "Rerun failed hosts (2)" and the POST goes to `/rerun` with `failed_only: true`).
- Running stack, local Docker on lin-manager-2: run a sample action on a group of two hosts named "test" where one is made to fail (stop its sshd), then rerun the failed host. Full backend suite once before the stage review.

## Docs and changelog
- `docs/ui/actions.md`: a "Rerun failed hosts" subsection, including what is refused and why.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Rerun failed hosts.** A finished group run offers a rerun of only the hosts that failed. The new run is linked to the original, and the server re-checks the parameters against the action as it is now."

## Risks and rollback
- The subset is a new way for a run to differ from its target. `check_host_busy` source 3b treats a `running` group run with no host rows yet as holding every member; for a subset run that briefly over-claims the other members, which is safe but can defer another op by a few seconds. Do not change `host_lock.py` for this.
- A host that left the group between the two runs is skipped; the run page must say so (step 4) or the operator will assume it ran.
- Rollback: revert the commit and downgrade the migration. Both columns are nullable and unused by older code.

## Assumptions
- Assumption (not yet confirmed): "failed hosts" means host runs with status `failed` only. `cancelled` and `skipped` hosts are not included. Question for Dennis: should a cancelled run's unfinished hosts count?
- Assumption (not yet confirmed): the endpoint refuses rather than "falls back to a prefilled dialog" (the board offers both). The dialog already shows what it dropped, so the server only has to refuse.
- Assumption (not yet confirmed): a rerun of a scheduled or alert-started run is an ad-hoc run and does not count in that schedule's history.

## Definition of done
- [ ] Migration, model, endpoint, orchestrator change and frontend merged behind review.
- [ ] `tests/test_actions_rerun.py` and the extended Playwright spec pass; full backend suite green.
- [ ] `docs/ui/actions.md` and `CHANGELOG.md` updated.
- [ ] Stage 6 behaviour 1 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
