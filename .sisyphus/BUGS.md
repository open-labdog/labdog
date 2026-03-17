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

## Fixed

All 12 bugs fixed on 2026-03-17.
