# C-016: Protected hosts: a per-host never-write-config flag

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Protected hosts: a per-host "never write config here" flag" |
| Tier / score | P1 / 7.0 |
| Stage | 4 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

An operator can mark a host as protected. unbork then never writes its desired configuration to that host, whatever triggers the sync, while inventory, state collection, drift reports and metrics keep working.

## Scope

In scope:

- `hosts.protected`, the API field, an audit row when it changes.
- Refusal of every config sync: at the API, in the sync orchestrator task, and as a skip in group syncs.
- The CA certificate deploy path and the two post-run hooks.
- A badge and a toggle in the UI, and "would differ" wording for drift on a protected host.

Out of scope (see Assumptions; proposed as a follow-up item):

- Refusing action runs on a protected host unless the pack manifest opts in (`allow_on_protected`). That changes the public pack contract.
- Limiting the AI SSH tool (`backend/app/ai/tools/ssh.py`) to read-only commands on a protected host.
- Whether overview counts leave protected hosts out (the board's open question).

## Context

- `backend/app/models/host.py::Host` has no such flag. Today the only protection is keeping the host out of every group.
- Sync entry points at the API: `backend/app/api/sync.py` `trigger_host_sync` (line 308), `trigger_group_sync` (393), `trigger_bulk_sync` (476), `trigger_group_bulk_sync` (622); the per-tab routes in `resolver_sync.py`, `cron_sync.py`, `package_sync.py`, `service_sync.py`, `hosts_sync.py`, `user_sync.py` and `packages.py` (about line 186); the shared group fan-out `backend/app/api/_group_sync.py::sync_group_members` (line 53).
- Every one of them ends in `app.tasks.host_sync_orchestrator.run_host_sync` (line 925): the per-tab tasks delegate to it, `backend/app/tasks/gitops.py:288` and `backend/app/tasks/host_lock.py:981` (dispatch of a pending job) call it, and `_builtin.sync` awaits the same coroutine (`backend/app/tasks/builtin_dispatchers.py`, about line 597). `_prepare_run` (line 401) already holds one guard of this kind: it drops `firewall` and pre-records an error row when the backend is unknown.
- `SyncJob.status` (`backend/app/models/sync_job.py`, `JobStatus`) has `pending`, `running`, `success`, `failed`, `cancelled`. There is no "skipped".
- The CA path does not go through `run_host_sync`: `backend/app/ca_certs/actions.py::enqueue_ca_cert_action_for_host` (line 45) dispatches `run_ca_cert_action`, and `auto_enqueue_for_new_membership` (line 87) calls it when a host joins a group (`backend/app/api/hosts.py::update_host`). `host_lock.py:967` re-dispatches a pending CA job.
- Post-run hooks: `backend/app/sync/post_run.py::queue_post_run_sync` (line 56) and `dispatch_post_run_register` (line 115), called from `action_host.py` (about line 1205) and `action_group.py::_queue_post_run_followups` (line 1702). The second writes desired state.
- `PUT /api/hosts/{host_id}` (`backend/app/api/hosts.py:346`) declares `require("config.edit")`.
- UI: `frontend/app/(dashboard)/hosts/client-page.tsx` (list), `hosts/[id]/client-page.tsx` (header), `hosts/[id]/_dialogs/edit-host.tsx`, `hosts/[id]/_dialogs/sync-preview.tsx`.

## Dependencies

- None. Latest Community migration is `0060_schedule_owner.py`; C-004 adds one in this stage too, so take the next free number at the time.
- A host named "test" for the final check.

## Implementation steps

1. Migration `backend/alembic/versions/00NN_host_protected.py`: `hosts.protected BOOLEAN NOT NULL DEFAULT false`. Add the column to `Host`.
2. `backend/app/schemas/hosts.py`: `protected: bool | None` on `HostUpdate` and `HostCreate`, `protected: bool` on the response. In `update_host`, write `log_action(db, "host_protection_changed", "host", host.id, user.id, {"protected": old}, {"protected": new})` when it changes.
3. New `backend/app/sync/protection.py`: `PROTECTED_DETAIL = "Host is protected: unbork does not write configuration to it."`, `class HostProtected(Exception)`, `refuse_if_protected(host)` raising `HTTPException(409, detail={"code": "host_protected", "message": PROTECTED_DETAIL})`, and `async protected_ids(db, host_ids) -> set[int]`.
4. API, host-targeted: call `refuse_if_protected` before the `SyncJob` is created in `trigger_host_sync`, `trigger_bulk_sync` and each per-tab host sync route listed under Context. No job row is created for a refused request. Plan and preview routes (`plan_host`, `preview_host`) stay allowed: they only read.
5. API, group fan-out: in `_group_sync.py::sync_group_members`, `trigger_group_sync`, `trigger_group_bulk_sync` and `packages.py`, remove protected members before creating jobs and return them in the response as `skipped_protected: [host_id, ...]` beside the existing `skipped` count. They are not failures.
6. Orchestrator: in `host_sync_orchestrator.py::_prepare_run`, after loading the host, raise `HostProtected` when `host.protected`. In `_async_run`, catch it before any workspace or SSH key is created: set the job `failed` with `error_message=PROTECTED_DETAIL`, do not touch `HostModuleStatus`, write the usual `sync_completed` audit row with outcome `refused_protected`, and release the host through the normal `finally` path so the next pending op is dispatched. This is the backstop for GitOps, `_builtin.sync`, pending re-dispatch and a flag set after a job was queued.
7. CA certificates: `enqueue_ca_cert_action_for_host` returns None for a protected host (its documented "could not be enqueued" result), so `auto_enqueue_for_new_membership` becomes inert. Add the same check at the start of the `run_ca_cert_action` task as a backstop.
8. Post-run hooks: `queue_post_run_sync` and `dispatch_post_run_register` return early for a protected host and log one line into the run (`[post-run] skipped: host is protected`).
9. GitOps: where `backend/app/tasks/gitops.py` fans out (about line 288), skip protected hosts and say so in the task's log. The orchestrator backstop still applies.
10. Drift: no change to collection. In the drift API or UI text, a protected host's differences read "would differ (protected)" and offer no sync button.
11. Frontend: a "protected" badge in the host list and header; a toggle with a one-line explanation in `edit-host.tsx` (disabled without `useCan("config.edit")`); sync buttons on the host page and in `sync-preview.tsx` disabled with the reason; group sync results list skipped protected members. Add the field to `frontend/lib/types.ts`.
12. Tests in `backend/tests/test_protected_hosts.py`, one class per layer: every host-targeted sync route returns 409 and creates no `SyncJob`; a group sync and a group bulk sync skip the protected member, sync the other and report it; `run_host_sync` on a protected host ends `failed` with the message and never calls the Ansible runner; the CA enqueue returns None and joining a group enqueues nothing; both post-run hooks skip; changing the flag writes the audit row; unprotecting restores normal behaviour. Add a route-walking test that fails when a new `POST /api/.../sync` route does not call `refuse_if_protected`, in the style of `tests/test_permissions.py`.

## Acceptance criteria

- `POST /api/sync/hosts/{id}/sync`, `/bulk` and each per-tab host sync route return 409 with `code=host_protected` for a protected host, and no `SyncJob` row exists afterwards.
- A group sync over a group with one protected and one normal member creates one job and reports the protected host as skipped.
- A `SyncJob` that reaches `run_host_sync` for a protected host ends `failed` with the protection message; no playbook ran; the host's module statuses are unchanged; the next pending op for the host is dispatched.
- Adding a protected host to a group that has CA certificates enqueues no CA job.
- Facts collection and drift checks still run on a protected host.
- The flag change is in the audit log. The UI shows the badge and disables sync with the reason.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_protected_hosts.py tests/test_host_sync_orchestrator.py tests/test_ca_certs_actions.py tests/test_hosts.py tests/test_permissions.py -q`; full suite once before the stage review.
- `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test hosts.spec.ts sync.spec.ts`.
- Real host: on local Docker on `lin-manager-2`, mark a host named "test" protected, put it in a group with a hosts-file entry, sync the group, and confirm over SSH that `/etc/hosts` on it did not change. Use no other host.

## Docs and changelog

- `docs/ui/hosts.md`: a "Protected hosts" section (what is refused, what still works, that actions are not blocked yet). `docs/ui/groups.md`: membership is allowed and inert. `docs/ui/drift-detection.md`: the "would differ" wording.
- `CHANGELOG.md` `[Unreleased]`, Added: "Hosts can be marked protected: unbork still inventories and watches them, and refuses every configuration sync to them."

## Risks and rollback

- A missed write path defeats the flag. The orchestrator backstop covers everything that goes through `run_host_sync`; the CA path and post-run register are the known exceptions and have their own checks and tests.
- Operators may read "protected" as "nothing runs here". The docs and the toggle text state that actions and the assistant are not blocked by this item.
- Back out by reverting the commits and running the migration's `downgrade()`. Until then, clearing the flag restores normal behaviour.

## Assumptions

- Assumption (not yet confirmed): this item stops at configuration writes, as its title says. The board sketch also wants actions refused unless the manifest opts in, and the AI SSH tool limited to reading. Both are left out to keep the item at M and to keep a manifest change out of it. Question for Dennis: add them as a follow-up item, or widen this one?
- Assumption (not yet confirmed): the flag is changed with `config.edit`, the right `PUT /api/hosts/{id}` already asks for. The board note predates roles ("anyone can flip it"). Question for Dennis: should lifting protection need an admin?
- Assumption (not yet confirmed): the name stays `protected`, the board's working name.
- Assumption (not yet confirmed): a refused job that reaches the orchestrator is `failed`, because `JobStatus` has no skipped value and adding one touches a Postgres enum.

## Definition of done

- [ ] Migration, model, schema, API refusals, orchestrator backstop, CA and post-run checks, UI merged.
- [ ] Backend tests (API, orchestrator, group) and the named Playwright specs green; ruff, tsc and lint clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
