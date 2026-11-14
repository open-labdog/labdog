# C-018: Save an action run as a schedule

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Action runs: rerun a failed run, and save a run as a schedule" (second half) |
| Tier / score | P2 / 7.0 |
| Stage | 6 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
From any finished run, an operator opens the schedule dialog already filled in with that run's action, target, parameters and safety flags, adds a cron expression and saves.

## Scope
In scope:
- A "Schedule…" button on the run page.
- Prefilling `ScheduleActionDialog` from an `ActionRun`.
- Handling the fields that do not map one to one: parallelism, dry run, preview parameters.

Out of scope:
- Any backend change, unless the test in step 5 shows one is needed.
- Scheduling from the Runs list row (the run page is one click away).
- Editing an existing schedule from a run. If one exists for the same target and action, the dialog says so.

## Context
- `frontend/components/scheduled-actions/schedule-action-dialog.tsx`: `ScheduleActionDialogProps` (line 29) takes `preselected` (action key and target only) or `scheduledAction` (edit mode). `buildInitialState` (line 90) builds the reducer state: `actionKey`, `targetKind`, `targetId`, `parameters`, `scheduleCron`, `enabled`, `snapshotEnabled`, `verifyEnabled`, `autoRollback`, `batchSize`.
- `POST /api/scheduled-actions` (`backend/app/api/scheduled_actions.py`, `create_scheduled_action`, line 436) needs `schedules.manage` on the target and returns 409 when a schedule already exists for the same target and action key (`uq_scheduled_actions_target_action`). `ScheduledActionIn` is in `backend/app/schemas/scheduled_actions.py`; `batch_size` is `ge=1`.
- The scheduler passes `batch_size` straight into `ActionRun.parallelism` (`backend/app/tasks/scheduled_action_schedule.py`, line 282), so the two mean the same thing, except that a run may have `parallelism == -1` ("All at once" in `PARALLELISM_OPTIONS`, `frontend/components/action-run-dialog.tsx` line 50) and a schedule may not.
- A run's stored `parameters` carry internal keys that are not manifest parameters: `__dry_run` and `__metrics_destinations`. `formValuesFromRun` (same file, line 62, exported) already separates manifest values from internal keys and from parameters the action no longer declares.
- The run page header puts its buttons in `actions=` of `PageHead` in `frontend/components/action-run-detail.tsx` (line 279), where `RerunActionButton` lives.
- Screenshot of the run page: `/mnt/project-files/rerun-action/1-run-page.png`.

## Dependencies
- None. If C-017 has landed, place the new button next to its two rerun buttons.

## Implementation steps
1. `schedule-action-dialog.tsx`: add an optional prop `fromRun?: { run: ActionRun; action: ActionDefinition }`. In `buildInitialState`, when it is set and `scheduledAction` is not:
   - `actionKey = run.action_key`; `targetKind = run.target_kind`; `targetId = run.host_id ?? run.group_id ?? null`.
   - `parameters = formValuesFromRun(action, run).values` (import it from `action-run-dialog.tsx`).
   - `snapshotEnabled`, `verifyEnabled`, `autoRollback` from the run.
   - `batchSize = run.parallelism >= 1 ? run.parallelism : Math.max(1, run.host_runs.length)`.
   - `scheduleCron = ""`, `enabled = false`.
2. In the dialog body, when `fromRun` is set, show one note line: "From run #id. Add a schedule; it stays disabled until you enable it." Then show a warning `Banner` for each of these that applies:
   - The run was a dry run (`run.parameters.__dry_run`): "That run was a preview. The schedule will make real changes."
   - A bool parameter named `report_only` is true in the prefilled values: "report_only is on, so every scheduled run would only report. Turn it off in Parameters if that is not what you want."
   - `formValuesFromRun` returned `dropped` keys: name them, as the rerun dialog does.
   - The run had `parallelism == -1`: "The run did all hosts at once; a schedule needs a batch size, set to N."
3. New component `frontend/components/schedule-run-button.tsx` (`ScheduleRunButton({ run })`), modelled on `RerunActionButton`: loads the catalog with the `["actions-catalog"]` query, is hidden unless `useCan("schedules.manage")`, disabled with a title when the action is no longer supplied or the run's host or group was deleted (a fleet run is allowed: `targetId` is null). `data-testid="schedule-run"`. It mounts `ScheduleActionDialog` with `fromRun` only while open.
4. `action-run-detail.tsx`: render `ScheduleRunButton` beside `RerunActionButton` for terminal runs.
5. On a 409 from the create call, keep the dialog open and show the API's message ("A schedule already exists for this target + action_key…") with a link to `/schedules`. Check that the dialog's existing error handling does this; add it if not.
6. Confirm by test that `POST /api/scheduled-actions` accepts the prefilled body for a host, a group and a fleet target. No backend change is expected.

## Acceptance criteria
- On a finished run's page, a user with `schedules.manage` sees "Schedule…". A viewer does not.
- The dialog opens at the picker step with the run's action and target, and the Parameters step shows the run's values. No `__` key is sent in the request body.
- Saving with a valid cron creates a schedule whose `target_kind`, `target_id`, `action_key`, `parameters`, `snapshot_enabled`, `verify_enabled`, `auto_rollback` match the run, with `enabled` false unless the operator ticked it.
- A dry-run run shows the preview warning. A run with `report_only: true` shows the report-only warning.
- A run with parallelism "All at once" on three hosts prefills batch size 3.
- When a schedule already exists for the target and action, the operator sees the 409 message and nothing is created.

## Test plan
- New Playwright spec `frontend/e2e/run-schedule.spec.ts`, using `e2e/run-mock.ts` and the mocking style of `e2e/run-rerun.spec.ts`: mock the catalog, the run and `POST /api/scheduled-actions`; assert the request body, the two warnings, and the 409 path.
- `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test run-schedule.spec.ts scheduled-actions.spec.ts`
- Backend, only to confirm step 6: `cd backend && source .venv/bin/activate && pytest tests/test_scheduled_actions_api.py -q`
- Running stack on lin-manager-2: schedule from a real run on a host named "test", press "Run now" on the schedule, and compare the two runs' parameters.

## Docs and changelog
- `docs/ui/scheduled-actions.md`: "Create a schedule from a run". `docs/ui/actions.md`: one sentence pointing to it.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Save a run as a schedule.** A finished run's page has a Schedule… button that opens the schedule dialog filled in from the run."

## Risks and rollback
- The risk the board names: a preview quietly becoming a recurring preview, or a real change quietly becoming recurring. Both are covered by the warnings and by `enabled` starting false.
- `report_only` is a pack convention, not part of the manifest schema, so a pack that names its preview parameter differently gets no warning.
- Rollback: revert the frontend commit. No data or API changes.

## Assumptions
- Assumption (not yet confirmed): the button is offered on every terminal run, including failed ones. The board says "on any run".
- Assumption (not yet confirmed): `parallelism == -1` maps to a batch size equal to the run's host count. Question for Dennis: or should `ScheduledActionIn.batch_size` accept -1?
- Assumption (not yet confirmed): the metrics destinations picked for the run (`__metrics_destinations`) are not carried into the schedule, because `ScheduledActionIn` has no field for them. `formValuesFromRun` puts them back into the push-variable parameters where the action declares a metrics backend; check the scheduler honours those.

## Definition of done
- [ ] Button, dialog prefill and warnings merged; new Playwright spec passes; `tsc` and lint clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] Stage 6 behaviour 2 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
