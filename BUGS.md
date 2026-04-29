# Bug Registry

Discovered via full codebase audit on 2026-03-17.
Status: `[ ]` open · `[x]` fixed

---

## Critical / High

- [x] **BUG-01** `discover/page.tsx:80-88` — State mutation during render
  `setPhase()` / `setScanError()` / `setJobId()` called directly in the render body (not inside a `useEffect`). Illegal in React 18 concurrent mode; will cause infinite render loops in strict mode.

- [x] **BUG-02** `auth/users.py:53-63` — `on_after_register` mutates user across session boundaries
  Opens a brand-new `AsyncSessionLocal` session to promote the first user to superuser, then calls `session.add(user)` on an object that was created and committed by a *different* (now-closed) fastapi_users session. Should re-fetch the user by ID within the new session instead.

- [x] **BUG-03** `api/ssh_keys.py:41-42` — `is_default` race condition
  `UPDATE ssh_keys SET is_default=False` runs before the new key is inserted. Two concurrent default-key creation requests both clear the flag and then both set themselves as default, resulting in two default keys. Needs a `SELECT … FOR UPDATE` or serialized logic.

- [x] **BUG-04** Multiple frontend files — Hardcoded `http://localhost:8000` URLs
  `providers.tsx` (lines 15, 35), `login/page.tsx` (lines 18, 30), `register/page.tsx` (lines 19, 64), `sidebar.tsx` (line 51) all hard-code the backend URL instead of reading `process.env.NEXT_PUBLIC_API_URL`. Breaks every non-localhost deployment.

---

## Medium

- [x] **BUG-05** `discover/page.tsx` — `selectedKeyId` default-selection runs on every render
  Same render-time setState pattern as BUG-01. `if (sshKeys && selectedKeyId === null) setSelectedKeyId(...)` is in the render body; must be a `useEffect`.

- [x] **BUG-06** `auth/users.py:28` — `SameSite=Lax` cookie breaks cross-port local dev
  Auth cookie is set with `SameSite=Lax`. Browsers do not send `Lax` cookies on cross-origin (different-port) non-safe-method requests. Frontend on `:3000` → backend on `:8000` POST requests silently drop the cookie, causing 401s. Fix: add a Next.js `rewrites` proxy so all `/api/*` calls go through the same origin.

- [x] **BUG-07** `gitops/importer.py:97-134` — GitOps import leaves `gitops_status=importing` on mid-block failure
  If an exception is raised inside the `if diff.has_changes:` block (after the delete but before status is set to `synced`), the group's `gitops_status` is never updated from `importing`. The advisory lock rolls back the delete, but the status stays stuck until the next successful import.

- [x] **BUG-08** `api/auth_setup.py:20-36` — `register` DB query unguarded
  `setup_status` now has a `try/except` (previously fixed), but the identical `count` query inside `register` on line 26 is still unguarded. A DB blip during first-user registration returns a raw 500 traceback.

---

## Low

- [x] **BUG-09** `groups/[id]/sync/page.tsx` — Sync job poll never stops on persistent API error
  `pollJob` catches errors silently and continues polling indefinitely. No backoff, no max-retry limit, no user-visible error after repeated failures.

- [x] **BUG-10** `git-repos/page.tsx:173-177` — `webhookUrls` array recreated on every render
  Constructed inline in the render body with `window.location.origin`. Should be `useMemo` (or moved outside the component as a constant) to avoid the allocation and the awkward inline `typeof window !== "undefined"` guard.

- [x] **BUG-11** `api/ssh_keys.py:62-69` — TOCTOU in `delete_ssh_key`
  First query checks for host references; a second separate query fetches the key to delete. If the key is deleted between the two queries the endpoint returns 404 for what was actually a concurrent delete. Should use a single `DELETE … RETURNING *` to collapse to one round-trip.

- [x] **BUG-12** `lib/api.ts` — Pydantic array error messages drop field location
  When a 422 error array is joined, only `e.msg` is included — the field path (`e.loc`) is discarded. "Value is not a valid integer" gives no indication of *which* field failed.

---

## Type Errors (Upstream / Cosmetic)

These are static analysis issues that do not affect runtime behavior. Documented and suppressed.

- [x] **TYPE-01** `auth/users.py` — fastapi-users `UserProtocol` vs SQLAlchemy `Mapped` type mismatch
  `SQLAlchemyUserDatabase`, `BaseUserManager[User, int]`, and `FastAPIUsers[User, int]` all expect `UserProtocol` which defines fields as plain `str`/`bool`/`int`. SQLAlchemy 2.0's `Mapped[str]` is a different type at static analysis time, even though it resolves to `str` at runtime. This is a known upstream incompatibility — fastapi-users hasn't updated their protocols for SQLAlchemy 2.0.
  **Fix applied**: `# type: ignore[type-var]` on the 3 affected lines with explanation comment.
  **Upstream**: https://github.com/fastapi-users/fastapi-users/issues — awaiting protocol update.

- [x] **TYPE-02** `rules/converter.py` — `Sequence[FirewallRule]` vs `list[FirewallRule]`
  `firewall_rules_to_specs()` accepted `list[FirewallRule]` but SQLAlchemy's `.scalars().all()` returns `Sequence[FirewallRule]`. Callers in `api/rules.py`, `api/sync.py`, and `api/drift.py` all pass the SQLAlchemy result directly.
  **Fix applied**: Changed function signature to accept `Sequence[FirewallRule]` (from `collections.abc`).

- [x] **TYPE-03** `api/drift.py`, `tasks/drift.py`, `tasks/sync.py` — `sync_status` assigned as string instead of `SyncStatus` enum
  `host.sync_status = "error"` and `host.sync_status = "in_sync"` assign bare strings to a `Mapped[SyncStatus]` column. Works at runtime (SQLAlchemy coerces), but type checkers flag it.
  **Fix applied**: Changed all assignments to use `SyncStatus.error`, `SyncStatus.in_sync`, etc. Also updated `DriftResult.status` from `str` to `SyncStatus` enum.

---

## Dead Code

- [x] **DEAD-01** `api/permissions.py`, `auth/rbac.py` — Leftover RBAC files after removal
  The `user-management.md` plan removed RBAC (dropped `user_group_permissions` table, `GroupRole` enum, model file). These two files were not deleted. Both import `app.models.user_group_permission` which no longer exists (confirmed by LSP errors). They contain `require_group_role()` and permission-checking logic that nothing calls.
  **Fix applied**: Both files deleted. Verified nothing imports from them.
  **Discovered**: 2026-03-17 (during `ext-service-management` plan review)

---

## Found & Fixed During ext-service-management (2026-03-17)

- [x] **BUG-13** `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Host override delete sends `service_name` instead of `rule_id`
  The services tab on the host detail page called `DELETE /api/hosts/${id}/services/${service_name}` but the backend expects `rule_id` (int). This always resulted in a 404/422. The effective-services response doesn't include rule IDs, so the page had no way to delete host overrides.
  **Fix applied**: Added a second query fetching host overrides (`GET /api/hosts/${id}/services`) which returns `ServiceRule[]` with `id` fields. Delete handler now looks up the override by `service_name` and sends `override.id` to the API.
  **Commit**: `d6bc2d5 fix(ui): use rule_id for host override delete/edit instead of service_name`
  **Discovered by**: F2 code quality review

- [x] **SEC-01** `backend/app/services/collector.py:43,65` — SSH command injection via service_name
  `f"systemctl is-active {name}"` interpolates user-provided service names directly into SSH commands. A name like `nginx; rm -rf /` would pass existing validators (only deny-list and suffix strip) and execute arbitrary commands on remote hosts.
  **Fix applied**: Added regex validator `^[a-zA-Z0-9_@:.-]+$` to both `ServiceRuleCreate` and `ServiceRuleUpdate` in `schemas.py`. Rejects shell metacharacters (`;`, `|`, `&`, `$`, backticks, spaces, etc.) while allowing all valid systemd unit name characters.
  **Commit**: `34a4f7e fix(security): add regex validation to service_name to prevent SSH command injection`
  **Discovered by**: F2 code quality review

- [x] **BUG-14** `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Preview File button broken (plain text response)
  The "Preview File" button in the hosts-file tab called `apiFetch()` which always calls `res.json()`. The `/api/hosts/{id}/hosts-file-preview` endpoint returns `text/plain` (`PlainTextResponse`). Clicking the button threw `SyntaxError: Unexpected token` and showed "Failed to load preview".
  **Fix applied**: Replaced `apiFetch` call with raw `fetch()` using `res.text()` and `credentials: "include"` for auth cookies.
  **Commit**: `8d4617c fix(ui): use res.text() for hosts-file-preview plain text response`
  **Discovered by**: F2 code quality review (ext-etc-hosts)

---

## Infra / DevEx

- [x] **BUG-15** `alembic/versions/0004_service_management.py` — Migration creates servicestate enum twice
  The explicit `sa.Enum(...).create(checkfirst=True)` call creates the enum, then `op.create_table()` with an inline `sa.Enum(...)` column tries to create it again within the same transaction. Fails with `DuplicateObjectError: type "servicestate" already exists`.
  **Fix applied**: Removed explicit `.create()` call; let `create_table` handle enum creation implicitly.
  **Discovered**: 2026-03-17 (running `./dev/dev.sh start`)

- [x] **BUG-16** `docker-compose.yml` — Redis port not exposed to host
  Redis container had no `ports` mapping. Backend celery workers running locally (not in Docker) couldn't connect to `localhost:6379`. Workers retried endlessly with `Error 111 connecting to localhost:6379. Connection refused`.
  **Fix applied**: Added `ports: ["6379:6379"]` to redis service in docker-compose.yml.
  **Discovered**: 2026-03-17 (running `./dev/dev.sh start`)

---

## Found During service-live-control (2026-03-18)

- [x] **BUG-17** `frontend/app/(dashboard)/groups/[id]/rules/page.tsx` — Missing `@dnd-kit/*` dependencies break build
  `npm run build` fails with 3 module-not-found errors: `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`. These packages are imported but not listed in `package.json`.
  **Fix applied**: `cd frontend && npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities`

---

## Found During Full Codebase Audit (2026-03-18)

- [x] **SEC-02** `backend/app/cron/schemas.py:32-37` — SSH command injection via cron job `user` field
  `CronJobCreate.validate_user` only checks `v.strip()` is not empty. No regex validation. A name like `root; cat /etc/shadow` passes validation and gets stored in the database. The collector at `cron/collector.py:54` runs `crontab -l -u {user}` via SSH, interpolating directly into the command. Same class of bug as SEC-01 (service_name injection).
  **Fix applied**: Added regex validator `^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$` (max 32 chars) to both `CronJobCreate` and `CronJobUpdate` `user` field. Rejects shell metacharacters (`;`, `|`, `&`, `$`, backticks, spaces, etc.) while allowing all valid Unix username characters.

- [x] **BUG-18** `frontend/app/(dashboard)/groups/[id]/page.tsx:67` — Group detail page shows ALL hosts instead of group members
  `const groupHosts = hosts ?? []` assigns the full hosts list without filtering. The `Host` type has `group_ids: number[]` so filtering by `host.group_ids.includes(id)` is possible. The comment on line 65 said "API doesn't expose group membership on Host" but this was wrong — `Host.group_ids` exists.
  **Fix applied**: Replaced with `hosts?.filter((h) => h.group_ids?.includes(id)) ?? []`. Removed incorrect comment.

- [x] **BUG-19** `backend/app/api/hosts.py:22-28` — `list_hosts()` doesn't populate `group_ids`
  `HostResponse` schema has `group_ids: list[int] = []` but `list_hosts()` returns raw `Host` objects without populating `group_ids` from the `HostGroupMembership` join table. Every host in the list returns `group_ids: []` (the Pydantic default). Only `get_host()` and `update_host()` manually set it via `setattr(host, "group_ids", ...)`.
  **Fix applied**: Added batch query in `list_hosts()` to populate `group_ids` for all hosts in a single `HostGroupMembership` query (avoids N+1).

- [x] **BUG-20** `backend/app/main.py:37` — CORS `allow_origins` hardcoded to `http://localhost:3000`
  `allow_origins=["http://localhost:3000"]` breaks any non-localhost deployment. Frontend running on a different hostname/port gets CORS rejections.
  **Fix applied**: Reads from `ALLOWED_ORIGINS` env var (comma-separated), defaults to `http://localhost:3000`.

- [x] **BUG-21** `docker-compose.yml:84-98` — `celery-beat` missing `postgres` dependency
  `celery-beat` depends only on `redis`, not `postgres`. The beat scheduler dispatches tasks (drift checks) that immediately query the database. If postgres isn't ready or migrations haven't completed, scheduled tasks crash on startup.
  **Fix applied**: Added `postgres: condition: service_healthy` to `celery-beat.depends_on`.

---

## Found During Packaging + Package-Management (2026-03-18)

- [x] **BUG-22** `frontend/app/(auth)/login/page.tsx:19`, `providers.tsx:16,36`, `register/page.tsx:20,65`, `sidebar.tsx:52` — Auth/user API calls broken after `API_BASE=""` change
  The packaging plan changed `API_BASE` from `http://localhost:8000` to `""` (empty string) for relative URL support. This broke all 7 frontend calls to `/auth/*` and `/users/*` because Next.js has an `app/(auth)/` route group that intercepts `/auth/*` requests before the proxy rewrite can fire. The browser received HTML (the login page) instead of JSON. The `.catch(() => setNeedsSetup(false))` silently swallowed the JSON parse error, so the "First time? Create admin account" link never appeared — users saw only a login form with no way to register.
  **Fix applied**: Moved backend auth routes from `/auth/*` → `/api/auth/*` and user routes from `/users/*` → `/api/users/*` in `main.py` and `auth_setup.py`. Updated all 7 frontend fetch calls to use `/api/auth/*` and `/api/users/*`. Updated test fixtures and test_auth.py to match. All calls now go through the single `/api/:path*` proxy rewrite.
  **Root cause**: Next.js route groups with parentheses `(auth)` don't create URL segments, but the rewrite engine still matches `/auth/*` to the route group before falling through to rewrites.

- [x] **BUG-23** `frontend/next.config.ts:10-11` — Proxy rewrite stripped `/api/` prefix from destination
  The original rewrite rule `source: "/api/:path*"` → `destination: "${API_URL}/:path*"` stripped the `/api/` prefix when proxying. After moving backend routes under `/api/`, the proxy sent `/api/auth/jwt/login` → `http://localhost:8000/auth/jwt/login` (wrong — missing `/api/`).
  **Fix applied**: Changed destination to `${API_URL}/api/:path*` to preserve the prefix.

---

## Found During Group Hosts View (2026-03-19)

- [x] **BUG-24** `frontend/app/(dashboard)/groups/[id]/page.tsx:334-335` — Nested `<button>` inside `<button>` causes hydration error
  A `<Button>` component (which renders `<button>`) is wrapped inside a `<DialogTrigger>` (which also renders `<button>` via `DialogPrimitive.Trigger`). HTML does not allow `<button>` as a descendant of `<button>`. This triggers two React hydration errors:
  1. "In HTML, `<button>` cannot be a descendant of `<button>`" — from `components/ui/button.tsx:52` via `GroupDetailPage` at line 335
  2. "`<button>` cannot contain a nested `<button>`" — from `components/ui/dialog.tsx:15` (`DialogTrigger`) via `GroupDetailPage` at line 334
  **Fix applied**: Used `<DialogTrigger render={<Button variant="outline" size="sm" />}>` so the trigger renders as the Button directly (base-ui `render` prop pattern) instead of wrapping it in its own `<button>`.

- [x] **BUG-25** `frontend/app/(dashboard)/hosts/[id]/page.tsx:889` — Duplicate `key` props in effective rules table
  `effectiveRules.map((rule) => ...)` uses `key={rule.id}` on `<TableRow>`, but multiple rules can share the same `id` (e.g. group-level rules appearing on multiple hosts, or rules from different sources). React warns: "Each child in a list should have a unique key prop" in the `TableBody` render of `HostDetailPage`.
  **Fix applied**: Changed key to `` `${rule.id}-${rule.group_id}` `` which is unique since rule IDs are unique within a group.

- [x] **BUG-26** `frontend/app/(dashboard)/hosts/[id]/page.tsx:1310` — Duplicate keys in effective hosts table
  `effectiveHosts.map((entry) => ...)` uses `` key={`${entry.source}-${entry.source_id}-${entry.hostname}`} `` but multiple entries can produce the same key (e.g. `system-0-localhost`). React warns: "Encountered two children with the same key". Non-unique keys cause children to be duplicated or omitted.
  **Fix applied**: Added `entry.ip_address` to the key: `` `${entry.source}-${entry.source_id}-${entry.ip_address}-${entry.hostname}` ``.

---

## Found During Single Service Consolidation (2026-03-20)

- [x] **BUG-27** `backend/app/api/services.py:71` — `'str' object has no attribute 'value'` in service create
  `ServiceRuleCreate` schema passes `state` as a string (e.g. `"running"`) but the API handler at line 71 calls `rule.state.value` expecting a `ServiceState` enum instance. Before `db.flush()`, `rule.state` is still the raw string from `model_dump()`, so `.value` crashes with `AttributeError`. Affected all 8 audit log calls across group and host CRUD endpoints.
  **Fix applied**: Replaced `rule.state.value` with `str(rule.state)` at all 8 call sites. Works for both string and enum values since `ServiceState(str, Enum)`.
  **Tests affected**: `test_services.py::test_create_group_service`, `test_services.py::test_effective_services` — now pass.
  **Discovered by**: Running test suite during single-service consolidation

---

## Found During Scan-Configs & Actions UI Review (2026-04-21)

- [x] **BUG-28** `frontend/components/scans/scan-config-dialog.tsx:369-380` — Redundant SSH User field in Add Scan Config dialog
  The dialog asks for an SSH User, but every `SSHKey` already stores its own `ssh_user` (`backend/app/models/ssh_key.py:16`, default `"root"`). The scan runner reads `ScanConfig.ssh_user`, so if the selected key is configured for user `ansible` but the dialog's SSH User stays at `root`, the key's value is silently ignored and the scan SSHes as the wrong user. Two sources of truth, guaranteed to drift.
  **Fix applied**: dropped `ssh_user` from the `scan_configs` table via new alembic migration `a1b2c3d4e5f6_drop_scan_configs_ssh_user.py`. Removed the field from `ScanConfig` model, all three `ScanConfig*` pydantic schemas, `create_scan_config`, the `ScanConfig` frontend type, the zod schema, and the dialog (replaced the two-column SSH User + Port grid with a full-width Port input). `approve_pending_hosts` and `scan_run.py` now read `ssh_user` from the linked `SSHKey`. Test fixtures updated.

- [x] **BUG-29** `frontend/app/(dashboard)/hosts/scans/client-page.tsx:113-177` — Chevron row-actions dropdown is clipped by the table's overflow container
  `RowActions` renders an `absolute top-8 z-50` menu (line 143) inside a `<TableCell>`. The table wrapper at `components/ui/table.tsx:11` sets `overflow-x-auto`; per the CSS spec, once one overflow axis is non-`visible` the other is promoted to `auto`, so the wrapper clips any absolutely-positioned descendant that leaves the cell. The toggle state flips correctly but the menu is invisible/unclickable.
  **Fix applied**: rewrote `RowActions` to render the backdrop + menu via `createPortal(..., document.body)` with `position: fixed`. Position is computed from the trigger's `getBoundingClientRect()` in a `useLayoutEffect` and re-measured on `scroll`/`resize` while open. SSR-guarded with a `mounted` state flag. No new dependency; no change to `table.tsx`.

- [x] **BUG-30** `backend/app/actions/registry.py:43-79` — Group's Available Actions tab lists "Upgrade Linux packages"
  Package upgrades on a group are intended as a recurring **scheduled workflow**, not an ad-hoc one-shot action. The `linux-upgrade` `ActionDefinition` currently inherits the default `supports_group=True`, so the card renders on the group Actions tab. `linux-os-upgrade` and `k8s-upgrade` are fine on a group (major-version OS jumps and K8s upgrades are one-shot fleet operations).
  **Fix applied**: added `supports_group=False` to the `linux-upgrade` registration. The frontend filter in `actions-tab.tsx` already hides unsupported actions.

- [x] **BUG-31** `frontend/app/(dashboard)/groups/[id]/workflow/client-page.tsx:368-440` — Group Workflow editor exposes an "Action Type" selector; workflows are strictly for package upgrades
  The editor lets the user pick between `linux-upgrade` and `linux-os-upgrade` as the workflow's action type, and conditionally renders codename inputs for `linux-os-upgrade`. Workflows are the schedulable package-upgrade feature only — OS upgrades and K8s upgrades do not belong on a recurring schedule.
  **Fix applied**: removed the Action Type `<Select>` block, the `linux-os-upgrade` conditional codename block, and the `action_key`/`action_parameters` fields from `WorkflowFormState`, `defaultForm`, and `workflowToForm`. `handleSave` now hardcodes `action_key: "linux-upgrade"` and `action_parameters: {}`. Deleted the now-unused `Select*` imports, `ActionDefinition` type import, `GROUP_ACTION_KEYS` constant, and the `actions` useQuery. Backend `action_key` column left untouched (simpler option per the note).

- [x] **BUG-32** `frontend/components/sidebar.tsx:121` — Sidebar highlights both "Hosts" and "Scans" when on `/hosts/scans`
  Active-state check is `pathname.startsWith(item.href)`. On `/hosts/scans`, both `"/hosts"` and `"/hosts/scans"` are prefixes, so both nav items get the active `bg-slate-800` class. Any nested route under an existing nav entry will double-highlight this way (same risk for `/groups/...`, `/hosts/discover`, etc.).
  **Fix applied**: compute a single `activeHref` per render by reducing over all nav items to pick the longest `href` that is either equal to the pathname or is followed by `/`. The nav mapping compares `item.href === activeHref`, so exactly one item highlights.

---

## Found During Actions + Workflows Audit (2026-04-22)

- [x] **BUG-33** `backend/app/api/actions.py:166` — Celery task-name mismatch leaves action runs stuck in `queued`
  The API endpoint dispatched `celery_app.send_task("actions.run", args=[run_id])`, but the orchestrator task is registered as `name="app.tasks.action_orchestrator.run_action"` (`backend/app/tasks/action_orchestrator.py:24-28`). `send_task` is fire-and-forget; no worker recognised the message, so every action run sat as `status=queued` forever with no error surfaced anywhere. Discovered when a linux-upgrade run from the UI silently hung.
  **Fix applied**: changed the dispatched name to `"app.tasks.action_orchestrator.run_action"` so it matches the registrar.

- [x] **BUG-34** `backend/app/api/actions.py` — Route ordering shadowed every `/runs*` endpoint with a 404
  `@router.get("/{action_key}")` was registered before the `/runs`, `/runs/{id}`, `/runs/{id}/stream`, `/runs/{id}/cancel`, and `/runs/{id}/hosts/{host_id}/output` routes. Starlette matches routes in registration order, so a request to `/actions/runs` matched `/{action_key}` with `action_key="runs"`, which isn't in the registry — the catalog endpoint returned 404 "Action not found" instead of yielding to the more-specific route. This broke the action-runs list page and the SSE log stream that powers the run-detail view.
  **Fix applied**: moved `GET /{action_key}` to the end of the router, after all `/runs*` routes, with a comment explaining the ordering constraint.

- [x] **BUG-35** `backend/app/tasks/workflow_orchestrator.py:185` — Dead set comprehension discards computed value
  `{hr.status for hr in host_runs}` was a set comprehension whose result was never assigned or used. Harmless at runtime but wasted CPU and misled readers about whether this line had side effects.
  **Fix applied**: removed the dead expression. The subsequent `success_count`/`failed_count` sums are the real aggregations.

- [x] **BUG-36** `backend/app/tasks/action_orchestrator.py:169` & `workflow_orchestrator.py:146` — Batch wait is swallowed, aggregation runs before children finish
  Both orchestrators call `result.join(timeout=3600, propagate=False)` on a Celery group of child host-tasks. Celery blocks all synchronous result-waiting from within a task by default (deadlock risk when the worker pool is saturated), raising `RuntimeError("Never call result.get() within a task!")`. Because the `join()` is wrapped in `try/except Exception` that merely logs a warning, the orchestrator then proceeds straight to the Phase 3 aggregation loop *before the children have run* — so it reads `succeeded=0, failed=0` from empty/queued host runs and writes `status='succeeded'` on the parent. Users saw runs reported as successful even when every host failed. The workflow_orchestrator comment even wrongly claimed `join()` was safer than `get()` — they are blocked identically.
  Discovered when the manual unstick of action_run #1 completed in ~60ms with "succeeded (0/1 hosts succeeded)" logged verbatim.
  **Fix applied**: wrap the `result.join(...)` in `celery.result.allow_join_result()` — the documented opt-in that lets an orchestrator genuinely block on its children. Updated the misleading comment in workflow_orchestrator.py.
  **Known limitation**: `allow_join_result` fixes correctness but can still deadlock if the worker pool is fully saturated with orchestrators. A future refactor to Celery chords would remove that risk; out of scope here.

---

## Found During Multi-Group Sync Live Test (2026-04-28)

- [x] **BUG-37** All per-group module sync API endpoints + GitOps task — Celery `.delay()` fires before the `SyncJob` row commits, sporadic `NoResultFound` under load. Surfaced 2026-04-28 from staging on `10.10.101.5` (3 unhandled `NoResultFound` over a 5-min window of 161 successful results); fixed 2026-04-28.
  **Original triage said 15 sites; on inspection the bug only affected 8**: the seven *per-group* sync endpoints and the GitOps `_trigger_group_sync`. The *per-host* endpoints (firewall, services, packages, hosts, cron, users, resolver) already commit before delay and were not affected.
  **Fix**: dispatch after commit. Each per-group route now collects `(job_id, host_id)` tuples in the loop, commits all rows together, then runs a second loop that calls `task.delay(...)`. The GitOps `_trigger_group_sync` was made return-only (returns the pending list to its caller); `_process_webhook_async` accumulates pending dispatches across all groups, commits once at the end of the webhook delivery, then drains the list. This handles the mid-loop-exception case correctly: a rollback now erases the staged jobs *before* any task has been enqueued.
  **Test**: `tests/test_bug37_dispatch_after_commit.py` — 7 pytest cases covering all five module endpoints + the firewall path + a fault-injection variant that forces a `commit()` failure and asserts no `.delay()` was called.

---

## Fixed

All 12 original bugs fixed on 2026-03-17.
Type errors TYPE-01 through TYPE-03 fixed on 2026-03-17.
BUG-13 and SEC-01 found during ext-service-management final review and fixed immediately.
BUG-14 found during ext-etc-hosts final review and fixed immediately.
BUG-15 and BUG-16 found while testing dev.sh script and fixed immediately.
DEAD-01 and BUG-17 verified as already resolved during 2026-03-18 audit.
