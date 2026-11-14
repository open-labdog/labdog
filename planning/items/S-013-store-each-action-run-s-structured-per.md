# S-013: Store each action run's structured per-host results

| | |
|---|---|
| Edition | shared |
| Source | decision, D-10 (recorded in `PLAN.md`, "Added on 2026-11-14 from a decision") |
| Tier / score | P1 / 4.5 |
| Stage | 6 |
| Effort | M |
| Autonomy | B |
| Depends on | S-002 |

## Goal
A pack's playbook can hand back structured data with Ansible `set_stats`, and unbork stores it per host on the run and returns it from the run API. Extensions that report on run results receive it through the run-end hook.

## Scope
In scope:
- A `results` JSONB column on `ActionHostRun`.
- Collecting `set_stats` data in both executors (`action_host.py`, `action_group.py`).
- Returning it from the run API and passing it through the S-002 run-end hook.
- A small "Results" view on the run page.

Out of scope:
- Any reporting, export or aggregation over the results. This item only stores and returns them.
- A schema for the data. It is whatever JSON object the pack sets.
- Built-in pseudo-actions (`_builtin.*`), which run no pack playbook.

## Context
- `backend/app/models/action_run.py`: `ActionHostRun` has `status`, `exit_code`, `output` (text transcript), `error_message`, `snapshot_name`. Nothing structured.
- Per-host executor, `backend/app/tasks/action_host.py`: `_phase_b_playbook` (line 828) calls `run_ansible` with an `event_handler` (`_on_event`) and returns `_PlaybookResult(output, success, exit_code, status)`. `_phase_g_persist` (line 1171) writes the host row.
- Group executor, `backend/app/tasks/action_group.py`: `_run_group_playbook` (line 765) runs one playbook for all members; `_route_runner_events_to_hosts` (line 838) already walks `runner.events`, reads `playbook_on_stats`, and maps inventory names to rows through `_HostCtx.inv_name`. `_persist_member_outcomes` (line 912) writes the rows.
- What ansible-runner exposes (checked in the installed package, `ansible_runner/display_callback/callback/awx_display.py`, line 668): the `playbook_on_stats` event carries `event_data["artifact_data"] = stats.custom.get("_run", {})`. That is the data set with `set_stats` when `per_host` is false (the default). Data set with `per_host: true` is not in any event.
- API: `ActionHostRunOut` in `backend/app/schemas/actions.py` (line 84); `get_run` in `backend/app/api/actions.py` (line 556) returns host runs; `list_runs` leaves `host_runs` empty.
- No pack in `/home/user/unbork-playbooks` or the private pack uses `set_stats` today.
- `PLUGIN_API` is `"1.2"` in `backend/app/auth/permissions.py` (line 71); S-002 and other stage 1 items will have raised it.

## Dependencies
- S-002 (stage 1): the run-end hook. Read its spec and code for the payload type before step 6.
- Latest Community migration today is `0060_schedule_owner.py`; use the next free number.

## Implementation steps
1. Migration `backend/alembic/versions/00NN_action_host_run_results.py`: add nullable JSONB `results` to `action_host_runs`. Add the column to `ActionHostRun` with a comment: set by the pack through `set_stats`; NULL when the playbook set nothing.
2. New helper in `backend/app/ansible_runtime/outcomes.py`: `run_stats(events) -> dict` returns `event_data["artifact_data"]` of the last `playbook_on_stats` event, or `{}`. Add `bounded_results(data: dict) -> dict | None`: returns None for an empty or non-dict value, and when `json.dumps` of the value exceeds 64 KiB returns `{"_truncated": true, "_bytes": n}` instead of the data.
3. `action_host.py`: in `_phase_b_playbook`, read `run_stats(runner.events)` after the run and add `results: dict | None` to `_PlaybookResult`. Pass it to `_phase_g_persist` and write `host_run.results`. Skip for a dry run only if check mode produced nothing; store what is there otherwise. The verify playbook's stats are not stored.
4. `action_group.py`: one playbook serves every member, so define the convention: a top-level key `per_host` in the run stats maps inventory hostname to an object. For each member, `results = {**run_level, **per_host.get(inv_name, {})}` where `run_level` is the stats without `per_host`. Compute it in `_route_runner_events_to_hosts` (add `results: dict[int, dict | None]` to `_MemberOutcomes`) and write it in `_persist_member_outcomes`.
5. API: add `results: dict | None = None` to `ActionHostRunOut`. It is returned by `GET /api/actions/runs/{id}` and omitted from the list endpoint, which already returns no host rows. No new route, so no new permission; the existing `hosts.view` check applies.
6. Run-end hook: add each host's `results` to the payload S-002 defined (per host: `host_id`, `hostname`, `status`, `results`). Bump `PLUGIN_API`'s minor and add a line to the comment above it: "run-end payload carries per-host `results`".
7. Frontend: add `results?: Record<string, unknown> | null` to `ActionHostRun` in `frontend/lib/types.ts`. In `frontend/components/action-run-detail.tsx`, when the selected host run has results, show a collapsed "Results" block above the log: scalar values as a two-column table, anything nested as formatted JSON.
8. Sample pack for tests: add an action to `backend/tests/fixtures/packs/sample` whose playbook calls `ansible.builtin.set_stats` with `data: {packages_upgraded: 3, reboot_required: false}`.
9. Document the convention for pack authors (see Docs).

## Acceptance criteria
- A per-host run of a playbook that calls `set_stats` with `data: {a: 1}` stores `{"a": 1}` in that host's `results`, and `GET /api/actions/runs/{id}` returns it under `host_runs[].results`.
- A group-dispatch run whose playbook sets `{summary: "ok", per_host: {"node-1": {x: 1}}}` stores `{"summary": "ok", "x": 1}` on node-1 and `{"summary": "ok"}` on the other members.
- A playbook that sets nothing leaves `results` NULL, and the API returns `null`.
- Results larger than 64 KiB are replaced by the truncation marker and the run still finishes normally.
- A failed host keeps whatever stats were set before the failure.
- The run-end hook receives the same per-host results, and `PLUGIN_API`'s minor is one higher than before this item.

## Test plan
- Extend `backend/tests/test_action_host_phases.py` and `backend/tests/test_action_group_paths.py` with fake runner events carrying `artifact_data`; new `backend/tests/test_action_run_results.py` for the helper, the size cap and the API shape.
- `cd backend && source .venv/bin/activate && pytest tests/test_action_run_results.py tests/test_action_host_phases.py tests/test_action_group_paths.py tests/test_action_host.py tests/test_action_group.py -q`
- The hook test S-002 added: extend it to assert `results` is present.
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test run-log.spec.ts`.
- Running stack on lin-manager-2: register a working copy of the sample pack (`LABDOG_PLAYBOOKS_LOCAL`), run the new sample action on a host named "test", and check the API and the run page. This is the only way to confirm that the real ansible-runner version emits `artifact_data`; the unit tests fake the event.
- Full backend suite once before the stage review.

## Docs and changelog
- `docs/ui/actions.md`, pack author section: "Returning results with `set_stats`", including the `per_host` convention for group-dispatch actions, the 64 KiB cap, and that results are visible to everyone who can view the run.
- `docs/examples/action-packs/`: add `set_stats` to one starter pack.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Structured run results.** Data a playbook sets with `set_stats` is stored per host and returned by the run API. Plugin API minor bump."

## Risks and rollback
- A pack can put a secret in its stats. Results are shown to anyone with `hosts.view`, like the log. Document it; do not try to redact arbitrary JSON.
- `per_host: true` data silently never arrives. The docs must say to leave `per_host` off and, for group actions, to use the `per_host` key instead.
- Rollback: revert the commit and downgrade the migration; the column is nullable. Revert the `PLUGIN_API` bump only if nothing released depends on it.

## Assumptions
- Assumption (not yet confirmed): the `per_host` key convention for group-dispatch actions. The alternative is a custom callback plugin to capture `per_host: true` stats, which is much more work. Question for Dennis: is the convention acceptable to pack authors?
- Assumption (not yet confirmed): a 64 KiB cap per host.
- Assumption (not yet confirmed): results are not copied into the audit log.
- Depends on the shape S-002 gives the run-end payload; if S-002 passes only a run id, step 6 reduces to documenting that extensions read `results` from the row, and no bump is needed.

## Definition of done
- [ ] Migration, both executors, API field, hook payload and run page block merged behind review.
- [ ] Tests pass; full backend suite green; `tsc` and lint clean.
- [ ] Docs, example pack and `CHANGELOG.md` updated; `PLUGIN_API` comment updated if bumped.
- [ ] Stage 6 behaviour 4 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
