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

- [ ] **DEAD-01** `api/permissions.py`, `auth/rbac.py` — Leftover RBAC files after removal
  The `user-management.md` plan removed RBAC (dropped `user_group_permissions` table, `GroupRole` enum, model file). These two files were not deleted. Both import `app.models.user_group_permission` which no longer exists (confirmed by LSP errors). They contain `require_group_role()` and permission-checking logic that nothing calls.
  **Fix**: Delete both files. Verify nothing imports from them.
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
  **Discovered**: 2026-03-17 (running `./dev.sh start`)

- [x] **BUG-16** `docker-compose.yml` — Redis port not exposed to host
  Redis container had no `ports` mapping. Backend celery workers running locally (not in Docker) couldn't connect to `localhost:6379`. Workers retried endlessly with `Error 111 connecting to localhost:6379. Connection refused`.
  **Fix applied**: Added `ports: ["6379:6379"]` to redis service in docker-compose.yml.
  **Discovered**: 2026-03-17 (running `./dev.sh start`)

---

## Fixed

All 12 original bugs fixed on 2026-03-17.
Type errors TYPE-01 through TYPE-03 fixed on 2026-03-17.
BUG-13 and SEC-01 found during ext-service-management final review and fixed immediately.
BUG-14 found during ext-etc-hosts final review and fixed immediately.
BUG-15 and BUG-16 found while testing dev.sh script and fixed immediately.
