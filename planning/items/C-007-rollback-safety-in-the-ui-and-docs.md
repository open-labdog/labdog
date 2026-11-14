# C-007: Rollback safety in the UI and docs; record BUG-177 to BUG-179

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Basic rollback safety" (UI bullet of phase 1, and "Exposures today") |
| Tier / score | P1 / 5.5 |
| Stage | 4 |
| Effort | M |
| Autonomy | B |
| Depends on | C-004, C-005, C-006 |

## Goal

An operator can see and set a host's stateful flag and rollback mode, sees before and after a run whether a rollback point exists and what a rollback threw away, and can confirm a waiting rollback from the run page. The three known exposures from closed PR #282 are in `BUGS.md`.

## Scope

In scope:

- Host edit dialog, host list and host header: the two fields and a badge.
- Run page: the "rollback awaiting confirmation" state with a confirm and a decline button, the refusal reason from C-005, the discarded summary from C-006.
- The snapshot check result on the host page.
- User docs for the whole stage's rollback behaviour.
- `BUGS.md` entries BUG-177, BUG-178 and BUG-179.

Out of scope:

- Fixing the three bugs. BUG-177 belongs to the application-rollback item (C-027), BUG-178 and BUG-179 stay open.
- Group-level settings (not built in C-004).
- Any backend change beyond a missing field found while wiring the UI.

## Context

- Host edit dialog: `frontend/app/(dashboard)/hosts/[id]/_dialogs/edit-host.tsx` (`EditHostDialog`) sends `PUT /api/hosts/{id}` with hostname, address, SSH fields and `group_ids`.
- Host list: `frontend/app/(dashboard)/hosts/client-page.tsx` (filters and the drift column near lines 76 to 311). Host page: `frontend/app/(dashboard)/hosts/[id]/client-page.tsx`, tabs under `_tabs/` (`overview.tsx`, `overview-side.tsx`, `overview-runs.tsx`).
- Run page: `frontend/app/(dashboard)/actions/runs/[runId]/client-page.tsx`. `frontend/lib/types.ts` and `frontend/lib/host-overview.ts` already carry `rolled_back`.
- An existing confirm-style panel to copy from: `frontend/components/ai/rollback-panel.tsx`.
- Permissions in the UI: pages are wrapped in `<PermissionScope>` and use `useCan()` (see `frontend/FRONTEND.md`). Components come from `@/components/ld` (`Banner`, `Field`, `Modal`).
- Backend, once the dependencies land: `HostResponse.stateful`, `rollback_mode`, `effective_rollback_mode` (C-004); `POST` and `DELETE /api/actions/runs/{run_id}/hosts/{host_run_id}/rollback` (C-004, `actions.run`); `ActionHostRun.rollback_state` (C-004); `GET /api/hosts/{host_id}/snapshot-check` (C-005, `hosts.view`); `rolled_back_at`, `snapshot_taken_at`, `rollback_discarded_seconds` (C-006).
- `BUGS.md`: the counter line (line 36) says the last id is `BUG-176`. Entries are open-only, grouped under a dated "Open" heading and a severity heading, in the format shown under "How to file an entry".
- The three bug texts are in the board item "Basic rollback safety" in `/mnt/project-files/roadmap/plan-v3.json`, section "Exposures today (from BUGS.md in PR #282)". Their file pointers still match the code: `backend/app/tasks/action_group.py:1468` is `_rollback_all`, `backend/app/ai/remediation.py:686` is `check_rollback`, `backend/app/workflows/steps/snapshot.py:51` is `create_snapshot`.
- Docs: `docs/ui/actions.md` section "Destructive actions and snapshot safety" (about line 589), `docs/ui/hosts.md`, `docs/ui/settings.md`.

## Dependencies

- C-004, C-005 and C-006 merged. C-044 if it has landed (one sentence in the docs and the "with memory" wording on the run page).
- Screenshots need a running stack: local Docker on `lin-manager-2`.

## Implementation steps

1. `frontend/lib/types.ts`: add the new host fields, `rollback_state` and the three rollback fields on the host-run type, and a `SnapshotCheck` type.
2. `edit-host.tsx`: add a "Rollback" group with a "Stateful (holds data that must not be reverted silently)" checkbox and a mode select with four choices: Default, Automatic, Ask first, Never. Show under it what Default resolves to for this host. Send `stateful` and `rollback_mode` (`"default"` for Default) in the PUT body. Disable both when `useCan("config.edit")` is false.
3. Badge: a small "stateful" badge with the effective mode as its title, in the host list row (`hosts/client-page.tsx`, next to the name) and in the host page header. Add a "stateful" filter beside the existing "drift check" filter.
4. Host page, overview side panel (`_tabs/overview-side.tsx`): a "Rollback point" row fed by `GET /api/hosts/{id}/snapshot-check` through TanStack Query, fetched on demand (a "Check" button), not on every page load, because the route calls the hypervisor. Show "possible", or "not possible" with the reasons.
5. Run page (`actions/runs/[runId]/client-page.tsx`), per host row:
   - `rollback_state=awaiting_confirmation`: a warning banner "Not rolled back: <hostname> is stateful. Snapshot <name> was taken <age> ago. Restoring it discards everything written since." with "Roll back now" (a confirm modal that repeats the age, then `POST .../rollback`) and "Keep current state" (`DELETE .../rollback`). Both need `useCan("actions.run")`; without it the banner is read-only.
   - `kept`: a note that the snapshot was kept and must be restored in the hypervisor by hand.
   - `rolling_back`: a spinner state; refetch until it changes.
   - `rolled_back`: "Rolled back to <time>. <duration> of writes discarded." from the C-006 fields.
   - A host run that failed with the C-005 refusal shows the refusal as the row's reason (it arrives in `error_message`; no special parsing).
6. Host overview runs list (`_tabs/overview-runs.tsx`, `frontend/lib/host-overview.ts`): show "awaiting rollback confirmation" as its own state, not plain "failed".
7. Playwright: extend `frontend/e2e/hosts.spec.ts` (set and clear the flag, badge appears) and add `frontend/e2e/run-rollback-confirm.spec.ts` using the mocking helper `frontend/e2e/run-mock.ts` for a run in `awaiting_confirmation` (confirm posts, banner changes; a viewer sees no buttons).
8. Docs. Rewrite "Destructive actions and snapshot safety" in `docs/ui/actions.md` as one ordered story: the pre-check and its refusal, the snapshot (and memory, if C-044 landed), verify, the three rollback modes, confirming, what is recorded as discarded, and when the snapshot is deleted. `docs/ui/hosts.md`: the two fields and the badge. Refresh the affected images under `docs/ui/screenshots/`.
9. `BUGS.md`: add a heading "Open, 2026-11-09 safe rollback for stateful hosts (from PR #282)" in the file's heading style, with BUG-177 under High and BUG-178 and BUG-179 under the severity the board text gives or, where it gives none, Medium. Copy the board text; re-check each `file:line`; amend each fix direction with what this stage changed (BUG-178: the stateful flag now exists, and `check_rollback` refuses `never` hosts; BUG-179: memory snapshots exist if C-044 landed, and the guest-agent check is still missing; BUG-177: `rollback_mode` lets an operator stop the revert by hand, and the member rule is still missing). Update the counter line to `BUG-179`.
10. If another item in an earlier stage has already used ids 177 to 179, stop and renumber nothing: report it, because the roadmap and this item's title name these ids.

## Acceptance criteria

- The flag and mode can be set and cleared in the host edit dialog, and the list and header show the badge afterwards.
- A user without `config.edit` sees the fields disabled; a user without `actions.run` sees no confirm or decline button.
- A run with a host in `awaiting_confirmation` shows the banner with the snapshot's age; confirming calls the endpoint and the row moves to "rolled back" with the discarded duration.
- The snapshot check row shows the reasons for a host with no VM mapping.
- `BUGS.md` contains BUG-177, BUG-178 and BUG-179 in the documented format, and the counter line says `BUG-179`.
- `npx tsc --noEmit` and `npm run lint` are clean, with no more warnings than before.

## Test plan

- `cd frontend && npx tsc --noEmit && npm run lint`.
- `npx playwright test hosts.spec.ts run-rollback-confirm.spec.ts run-log.spec.ts` (needs the full stack: `./dev/dev.sh start`, or local Docker on `lin-manager-2`).
- Docs build: the command the CI "docs build check" runs under `website/`.
- Manual, on `lin-manager-2` with a VM named "test": mark it stateful, fail a destructive `example` run, confirm from the run page, and take the screenshots there.

## Docs and changelog

- `docs/ui/actions.md`, `docs/ui/hosts.md`, `docs/ui/screenshots/`, `BUGS.md` as above.
- `CHANGELOG.md` `[Unreleased]`, Added: "Hosts show a stateful badge and a rollback mode in their settings; the run page asks for confirmation before reverting a stateful host and shows what a rollback discarded."

## Risks and rollback

- The confirm button reverts a VM. It sits behind a modal that states the age, and the backend re-checks state and permission.
- The snapshot check calls the hypervisor; fetching it on demand keeps the host page fast when a node is down.
- Back out by reverting the frontend commits; the backend from C-004 to C-006 works without the UI.

## Assumptions

- Assumption (not yet confirmed): the mode labels are "Automatic", "Ask first" and "Never" for `auto`, `confirm` and `never`. If Dennis answers the C-004 question with a single `manual` mode, the select has two choices.
- Assumption (not yet confirmed): the board's "badge on the host list and header" is the stateful badge only; a separate badge per mode is not wanted.
- The bug severities for BUG-178 and BUG-179 are not stated on the board. Question for Dennis if Medium is wrong.

## Definition of done

- [ ] UI changes, Playwright specs, docs, screenshots and `BUGS.md` merged.
- [ ] `tsc`, lint and the named Playwright specs green.
- [ ] `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
