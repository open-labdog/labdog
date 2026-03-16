# User Management, Auth Improvements & RBAC Removal

## TL;DR

> **Quick Summary**: Fix the superuser bootstrap gap (first user auto-promoted), disable public registration (superuser-only user creation), add user management CRUD page, add sidebar user menu with logout + password change, and remove the per-group RBAC system entirely — simplifying to superuser vs. regular user.
>
> **Deliverables**:
> - First-user auto-promotion to superuser on registration
> - Registration gated: only works when zero users exist
> - `GET /auth/setup-status` public endpoint for frontend flow control
> - User management admin API (`/api/admin/users`) with full CRUD + last-superuser protection
> - RBAC removal: drop `UserGroupPermission` model, `GroupRole` enum, `require_group_role`, `get_user_accessible_group_ids`, permissions API
> - Alembic migration to drop `user_group_permissions` table and `grouprole` enum
> - Frontend `/users` page (superuser-only) with create/edit/delete user dialogs
> - Sidebar: user email + logout button at bottom, conditional "Users" nav for superusers
> - Password change dialog triggered from sidebar
> - Updated login page (conditional register link) and register page (setup-status check)
> - Updated backend tests (fixtures, RBAC removal) and E2E tests
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T3 → T5 → T7 → T9

---

## Context

### Original Request
User discovered there is no way to become superuser through the UI, making the entire system unusable for new deployments. The README promises first-user auto-promotion but it was never implemented. Additionally: no user management page exists, no logout button, no password change, and public registration is inappropriate for a security tool.

### Interview Summary
**Key Discussions**:
- First user bootstrap: `/register` accessible ONLY when zero users in DB. First user auto-becomes superuser. After that, registration blocked.
- User management: Full CRUD (create, edit, delete, reset password). Superuser-only.
- Profile: Logout button + password change. Sidebar bottom placement.
- Role simplification: REMOVE per-group roles entirely. Only superuser vs regular user. All authenticated users see everything and can manage everything except user administration.
- Sidebar "Users" nav: conditionally shown for superusers only.

### Metis Review
**Identified Gaps** (addressed):
- **Race condition on first registration**: Use `on_after_register` with `count == 1` check (only committed first user sees count=1)
- **`safe=True` gotcha**: fastapi-users `create(..., safe=True)` strips `is_superuser`. Must use `on_after_register` hook to promote AFTER creation
- **Last superuser protection**: Guard on user update/delete — query `count(*) WHERE is_superuser=true AND id != target_id`, reject if 0
- **RBAC replacement on write endpoints**: User said "anyone with legitimate access is qualified" — all `require_group_role(editor)` becomes `current_active_user`, all `require_group_role(viewer)` becomes `current_active_user`. Endpoints already gated by `current_superuser` stay unchanged.
- **Login page register link**: Becomes dead end after registration closes — conditionally render based on `/auth/setup-status`
- **Test suite breakage**: `conftest.py` fixtures use `UserGroupPermission` + `GroupRole` — must update all fixtures
- **Stale JWT after deactivation**: fastapi-users `current_active_user` checks `is_active` on every request — already handled

---

## Work Objectives

### Core Objective
Make Barricade deployable and usable: fix the superuser bootstrap, give superusers user management, give all users profile controls, and simplify the authorization model.

### Concrete Deliverables
- Backend: `on_after_register` first-user promotion + registration gating
- Backend: `GET /auth/setup-status` endpoint
- Backend: `/api/admin/users` CRUD router (list, create, update, delete, reset-password)
- Backend: Remove RBAC system (5 files affected, 16+ dependency injection sites)
- Backend: Alembic migration to drop `user_group_permissions` + `grouprole` enum
- Frontend: `/users` page with full CRUD UI
- Frontend: Sidebar user menu (email + logout + password change)
- Frontend: Conditional "Users" nav item
- Frontend: Updated registration flow (setup-status gating)
- Tests: Updated backend fixtures + E2E tests

### Definition of Done
- [x] First registered user auto-becomes superuser
- [x] Second registration attempt returns 403
- [x] Superuser can create/edit/delete users via `/users` page
- [x] Last superuser cannot be deleted or demoted
- [x] Sidebar shows user email and logout button
- [x] Password change works from sidebar
- [x] All `require_group_role` references removed
- [x] All `UserGroupPermission` references removed
- [x] `npm run build` passes
- [x] `pytest tests/` passes
- [x] `npx playwright test` passes

### Must Have
- `on_after_register` hook for first-user superuser promotion (NOT `create()` override due to `safe=True`)
- Registration blocked when `SELECT count(*) FROM users > 0` AND request is not from a superuser
- `GET /auth/setup-status` returns `{ needs_setup: bool }` — public, no auth required
- Last-superuser protection: cannot delete or set `is_superuser=false` on the only remaining superuser
- User admin endpoints require `current_superuser`
- Password hashing via `PasswordHelper().hash()` (same as existing `auth/superuser.py`)
- RBAC replacement: `require_group_role(viewer|editor)` → `current_active_user`; `current_superuser` endpoints unchanged
- Sidebar: user email + logout pinned to bottom, "Users" nav only when `user.is_superuser`

### Must NOT Have (Guardrails)
- No email verification enforcement (is_verified stays unchecked)
- No password strength rules beyond fastapi-users defaults
- No session management / active session listing / token revocation
- No 2FA or "remember me"
- No user avatars, display names, or profile extension fields
- No pagination/search/filter on users list (firewall tool, not thousands of users)
- No toast notifications (none exist in current UI)
- No loading skeletons (current pattern uses text indicators)
- No separate `/profile` or `/settings` page — password change lives in sidebar dialog
- No audit log entries for user management (not requested)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest + playwright)
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + httpx (backend), Playwright (frontend)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Backend API**: Bash (curl) — send requests, assert status + response fields
- **Frontend**: Playwright — navigate, interact, assert DOM, screenshot

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Backend foundation — 2 parallel):
├── Task 1: First-user auto-promotion + registration gating + setup-status endpoint [unspecified-high]
└── Task 2: User management admin API (CRUD + last-superuser guard) [unspecified-high]

Wave 2 (Backend cleanup — 2 parallel):
├── Task 3: Remove RBAC system from all API endpoints [deep]
└── Task 4: Alembic migration to drop user_group_permissions table [quick]

Wave 3 (Frontend — 3 parallel):
├── Task 5: User management page (/users) with CRUD dialogs [visual-engineering]
├── Task 6: Sidebar user menu (email + logout + password change) [visual-engineering]
└── Task 7: Update registration flow (setup-status, conditional register link) [quick]

Wave 4 (Tests + verify — 2 parallel):
├── Task 8: Update backend test fixtures for RBAC removal [unspecified-high]
└── Task 9: Build verification + full test suite pass [quick]

Wave FINAL (Review — 4 parallel):
├── F1: Plan compliance audit [oracle]
├── F2: Code quality review [unspecified-high]
├── F3: Real manual QA [unspecified-high]
└── F4: Scope fidelity check [deep]

Critical Path: T1 → T3 → T5 → T8 → T9 → F1-F4
Max Concurrent: 3 (Wave 3)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T3, T7, T8 | 1 |
| T2 | — | T5, T8 | 1 |
| T3 | T1 | T4, T5, T6, T8 | 2 |
| T4 | T3 | T8 | 2 |
| T5 | T2, T3 | T9 | 3 |
| T6 | T3 | T9 | 3 |
| T7 | T1 | T9 | 3 |
| T8 | T1, T2, T3, T4 | T9 | 4 |
| T9 | T5, T6, T7, T8 | F1-F4 | 4 |

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|------------|
| 1 | 2 | T1→`unspecified-high`, T2→`unspecified-high` |
| 2 | 2 | T3→`deep`, T4→`quick` |
| 3 | 3 | T5→`visual-engineering`, T6→`visual-engineering`, T7→`quick` |
| 4 | 2 | T8→`unspecified-high`, T9→`quick` |
| FINAL | 4 | F1→`oracle`, F2-F3→`unspecified-high`, F4→`deep` |

---

## TODOs

- [x] 1. First-User Auto-Promotion + Registration Gating + Setup-Status Endpoint

  **What to do**:
  - Modify `backend/app/auth/users.py` — override `on_after_register` in `UserManager`:
    ```python
    async def on_after_register(self, user: User, request=None):
        logger.info("User %d (%s) registered.", user.id, user.email)
        # Auto-promote first user to superuser
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(func.count(User.id)))
            count = result.scalar()
            if count == 1:  # This is the first and only user
                user.is_superuser = True
                user.is_verified = True
                session.add(user)
                await session.commit()
                logger.info("First user %d promoted to superuser.", user.id)
    ```
  - Modify registration to block when users exist: Override `create` in `UserManager` to check user count. If count > 0, raise `fastapi.HTTPException(403, "Registration is closed")`. Alternatively, create a custom dependency or middleware on the register router. The cleanest approach: add a `before_register` check via a custom register router that wraps the fastapi-users one.
  - **Simplest approach**: Replace the fastapi-users register router with a custom one:
    - Create `backend/app/api/auth_setup.py` with:
      - `GET /auth/setup-status` (public, no auth) — returns `{ "needs_setup": bool }` where `needs_setup = (SELECT count(*) FROM users) == 0`
      - A custom `POST /auth/register` that checks user count first: if count > 0, return 403 "Registration is closed. Contact an administrator." Otherwise, delegate to the fastapi-users UserManager to create the user.
    - In `backend/app/main.py`: REMOVE the fastapi-users register router (`get_register_router`). Add the custom auth_setup router instead.
  - Import `AsyncSessionLocal` from `app.db` and `select, func` from sqlalchemy in users.py

  **Must NOT do**:
  - Do NOT override `UserManager.create()` — the `safe=True` flag strips is_superuser, making promotion impossible there
  - Do NOT use a raw SQL UPDATE — use SQLAlchemy ORM
  - Do NOT add email verification enforcement
  - Do NOT modify the login/logout router

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: T3, T7, T8
  - **Blocked By**: None

  **References**:
  - `backend/app/auth/users.py:47-66` — UserManager class with `on_after_register` hook
  - `backend/app/auth/superuser.py:17-35` — Existing superuser creation pattern (model construction, commit)
  - `backend/app/main.py:37-42` — Current register router registration (to replace)
  - `backend/app/auth/schemas.py` — UserCreate, UserRead schemas
  - `backend/app/db.py` — AsyncSessionLocal import

  **Acceptance Criteria**:
  - [ ] `GET /auth/setup-status` returns `{"needs_setup": true}` when no users exist
  - [ ] `POST /auth/register` with valid data → 201 (first user)
  - [ ] `GET /users/me` after login shows `is_superuser: true` for first user
  - [ ] `GET /auth/setup-status` returns `{"needs_setup": false}` after first user
  - [ ] Second `POST /auth/register` → 403 "Registration is closed"
  - [ ] Backend starts without errors

  **QA Scenarios**:
  ```
  Scenario: First user becomes superuser
    Tool: Bash (curl)
    Steps:
      1. curl -s http://localhost:8000/auth/setup-status → {"needs_setup": true}
      2. POST /auth/register {"email":"admin@test.com","password":"TestPass1!"}
      3. Assert: 201
      4. Login, GET /users/me → is_superuser: true
    Expected Result: First user auto-promoted
    Evidence: .sisyphus/evidence/task-1-first-user.txt

  Scenario: Second registration blocked
    Tool: Bash (curl)
    Steps:
      1. POST /auth/register {"email":"second@test.com","password":"TestPass1!"}
      2. Assert: 403
    Expected Result: Registration closed
    Evidence: .sisyphus/evidence/task-1-registration-blocked.txt
  ```

  **Commit**: YES
  - Message: `feat(auth): auto-promote first user to superuser and gate registration`
  - Files: `backend/app/auth/users.py`, `backend/app/api/auth_setup.py`, `backend/app/main.py`

- [x] 2. User Management Admin API (CRUD + Last-Superuser Guard)

  **What to do**:
  - Create `backend/app/api/admin_users.py` with router prefix `/admin/users`:
    - `GET /admin/users` (superuser only) — list all users with id, email, is_active, is_superuser, is_verified, created_at, updated_at
    - `POST /admin/users` (superuser only) — create user with email + password. Hash password via `PasswordHelper().hash(password)`. Set is_active=True, is_verified=True, is_superuser=False by default. Optional: `is_superuser` field in request.
    - `PATCH /admin/users/{user_id}` (superuser only) — update email, is_active, is_superuser. **Last-superuser guard**: if setting `is_superuser=False`, check `SELECT count(*) FROM users WHERE is_superuser=true AND id != :user_id` — if result is 0, return 400 "Cannot demote the last superuser"
    - `DELETE /admin/users/{user_id}` (superuser only) — delete user. **Last-superuser guard**: same check — cannot delete the last superuser. Also: cannot delete self (return 400 "Cannot delete yourself").
    - `POST /admin/users/{user_id}/reset-password` (superuser only) — accept `{ password: string }`, hash and update `hashed_password` field.
  - Create Pydantic schemas inline or in a new schemas file:
    ```python
    class AdminUserCreate(BaseModel):
        email: str
        password: str
        is_superuser: bool = False

    class AdminUserUpdate(BaseModel):
        email: str | None = None
        is_active: bool | None = None
        is_superuser: bool | None = None

    class AdminUserResponse(BaseModel):
        id: int
        email: str
        is_active: bool
        is_superuser: bool
        is_verified: bool
        created_at: datetime
        updated_at: datetime
        model_config = {"from_attributes": True}

    class PasswordReset(BaseModel):
        password: str
    ```
  - Register router in `backend/app/main.py` with `prefix="/api"`

  **Must NOT do**:
  - Do NOT allow deleting yourself
  - Do NOT allow demoting the last superuser
  - Do NOT return hashed_password in responses
  - Do NOT add pagination, search, or filtering

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: T5, T8
  - **Blocked By**: None

  **References**:
  - `backend/app/auth/superuser.py:25-26` — Password hashing pattern: `PasswordHelper().hash(password)`
  - `backend/app/api/hosts.py:43-66` — CRUD endpoint pattern with superuser dependency
  - `backend/app/auth/users.py:73` — `current_superuser` dependency import
  - `backend/app/models/user.py` — User model fields
  - `backend/app/main.py:51-60` — Router registration pattern

  **Acceptance Criteria**:
  - [ ] `GET /api/admin/users` as superuser → 200 with user list
  - [ ] `GET /api/admin/users` as regular user → 403
  - [ ] `POST /api/admin/users` creates new user
  - [ ] `DELETE /api/admin/users/{last_superuser_id}` → 400 "Cannot delete the last superuser"
  - [ ] `PATCH /api/admin/users/{last_superuser_id}` with `is_superuser: false` → 400 "Cannot demote the last superuser"
  - [ ] `DELETE /api/admin/users/{self_id}` → 400 "Cannot delete yourself"
  - [ ] `POST /api/admin/users/{id}/reset-password` → 200, password updated

  **QA Scenarios**:
  ```
  Scenario: CRUD lifecycle
    Tool: Bash (curl)
    Steps:
      1. GET /api/admin/users → list with superuser
      2. POST /api/admin/users {"email":"new@test.com","password":"NewPass1!"} → 201
      3. GET /api/admin/users → list now has 2 users
      4. PATCH /api/admin/users/{new_id} {"is_active": false} → 200
      5. DELETE /api/admin/users/{new_id} → 204
    Expected Result: Full CRUD works
    Evidence: .sisyphus/evidence/task-2-user-crud.txt

  Scenario: Last superuser protection
    Tool: Bash (curl)
    Steps:
      1. DELETE /api/admin/users/{only_superuser_id} → 400
      2. PATCH /api/admin/users/{only_superuser_id} {"is_superuser": false} → 400
    Expected Result: Both rejected
    Evidence: .sisyphus/evidence/task-2-last-superuser.txt
  ```

  **Commit**: YES
  - Message: `feat(api): add user management admin endpoints with last-superuser guard`
  - Files: `backend/app/api/admin_users.py`, `backend/app/main.py`

- [x] 3. Remove RBAC System From All API Endpoints

  **What to do**:
  - **Delete files**:
    - `backend/app/auth/rbac.py` (entire file — `require_group_role`, `get_user_accessible_group_ids`)
    - `backend/app/api/permissions.py` (entire file — permissions CRUD router)
    - `backend/app/models/user_group_permission.py` (entire file — `UserGroupPermission` model, `GroupRole` enum)
  - **Update `backend/app/main.py`**: Remove `permissions_router` import and `app.include_router(permissions_router, ...)` line
  - **Update `backend/app/api/hosts.py`**:
    - Remove: `from app.auth.rbac import get_user_accessible_group_ids`
    - `list_hosts`: Remove accessible group filtering. Change to simply `result = await db.execute(select(Host))`. Keep `current_active_user` dependency.
    - `get_host`: Remove accessible group check (lines 81-90). Keep `current_active_user` dependency. Just return the host if it exists.
  - **Update `backend/app/api/groups.py`**:
    - Remove: `from app.auth.rbac import get_user_accessible_group_ids, require_group_role`
    - `list_groups`: Remove accessible group filtering. Simply `SELECT * FROM host_groups`. Keep `current_active_user`.
    - `update_group`: Change from `require_group_role(GroupRole.editor)` to `current_active_user`.
    - Keep `current_superuser` on `create_group` and `delete_group` (unchanged).
  - **Update `backend/app/api/rules.py`**:
    - Remove: `from app.auth.rbac import require_group_role` and `from app.models.user_group_permission import GroupRole`
    - All `Depends(require_group_role(GroupRole.viewer))` → `Depends(current_active_user)`
    - All `Depends(require_group_role(GroupRole.editor))` → `Depends(current_active_user)`
    - Keep gitops lockdown checks (403 on rule mutation for GitOps groups) — that's separate from RBAC.
    - `get_effective_rules`: Remove accessible group check. Keep `current_active_user`.
  - **Update `backend/app/api/sync.py`**:
    - Remove RBAC imports.
    - Change `require_group_role(GroupRole.editor)` → `current_active_user` on plan and sync endpoints.
  - **Update `backend/app/models/__init__.py`** (if it imports `UserGroupPermission`/`GroupRole`): Remove those imports.
  - **Use tools**: Run `lsp_find_references` on `require_group_role` and `UserGroupPermission` before deletion to catch ALL usages. Run `ast_grep_search` with pattern `Depends(require_group_role($$$))` (python) to find all injection sites.

  **Must NOT do**:
  - Do NOT change any endpoint that already uses `current_superuser` — those stay as-is
  - Do NOT remove the `current_active_user` or `current_superuser` dependencies themselves
  - Do NOT remove audit logging or gitops lockdown logic
  - Do NOT modify the User model

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Many files affected (6+), need careful dependency tracking to avoid breaking anything
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential after T1)
  - **Blocks**: T4, T5, T6, T8
  - **Blocked By**: T1

  **References**:
  - `backend/app/auth/rbac.py` — File to delete (contains `require_group_role`, `get_user_accessible_group_ids`)
  - `backend/app/api/permissions.py` — File to delete (permissions CRUD router)
  - `backend/app/models/user_group_permission.py` — File to delete (model + enum)
  - `backend/app/api/rules.py` — 5 RBAC dependency usages to replace
  - `backend/app/api/sync.py` — 3 RBAC usages to replace
  - `backend/app/api/groups.py` — 3 RBAC usages to replace
  - `backend/app/api/hosts.py` — 2 RBAC usages to replace (list_hosts, get_host)
  - `backend/app/main.py` — Remove permissions_router

  **Acceptance Criteria**:
  - [ ] `grep -r "require_group_role" backend/app/` → zero matches
  - [ ] `grep -r "UserGroupPermission" backend/app/` → zero matches
  - [ ] `grep -r "GroupRole" backend/app/` → zero matches
  - [ ] `grep -r "permissions_router" backend/app/` → zero matches
  - [ ] Files deleted: `auth/rbac.py`, `api/permissions.py`, `models/user_group_permission.py`
  - [ ] `GET /api/hosts` as regular user → returns ALL hosts (no filtering)
  - [ ] `GET /api/groups` as regular user → returns ALL groups (no filtering)
  - [ ] Backend starts without import errors

  **QA Scenarios**:
  ```
  Scenario: Regular user sees all groups and hosts
    Tool: Bash (curl)
    Steps:
      1. Create a regular (non-superuser) user via admin API
      2. Login as regular user
      3. GET /api/groups → assert returns all groups
      4. GET /api/hosts → assert returns all hosts
    Expected Result: No access filtering
    Evidence: .sisyphus/evidence/task-3-no-rbac-filtering.txt

  Scenario: No RBAC imports remain
    Tool: Bash
    Steps:
      1. grep -r "require_group_role\|UserGroupPermission\|GroupRole" backend/app/
      2. Assert: zero matches
    Expected Result: Complete RBAC removal
    Evidence: .sisyphus/evidence/task-3-rbac-removed.txt
  ```

  **Commit**: YES
  - Message: `refactor(api): remove RBAC system and simplify authorization`
  - Files: deleted `auth/rbac.py`, `api/permissions.py`, `models/user_group_permission.py`; modified `api/rules.py`, `api/sync.py`, `api/groups.py`, `api/hosts.py`, `main.py`

- [x] 4. Alembic Migration to Drop user_group_permissions Table

  **What to do**:
  - Create new Alembic migration: `cd backend && alembic revision --autogenerate -m "drop user_group_permissions and grouprole enum"`
  - Verify the auto-generated migration drops:
    - `user_group_permissions` table
    - `grouprole` PostgreSQL enum type
  - If autogenerate doesn't catch the enum, manually add: `op.execute("DROP TYPE IF EXISTS grouprole")`
  - Ensure downgrade recreates both table and enum (for reversibility)

  **Must NOT do**:
  - Do NOT modify any other tables in this migration
  - Do NOT drop the users table or any user columns

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (must run after T3 deletes the model files)
  - **Parallel Group**: Wave 2 (after T3)
  - **Blocks**: T8
  - **Blocked By**: T3

  **References**:
  - `backend/alembic/` — Existing migrations directory
  - `backend/alembic.ini` — Alembic config
  - `backend/app/models/user_group_permission.py` — Model being removed (already deleted in T3)

  **Acceptance Criteria**:
  - [ ] `alembic upgrade head` succeeds
  - [ ] `user_group_permissions` table no longer exists in DB
  - [ ] `grouprole` enum type no longer exists in DB
  - [ ] `alembic downgrade -1` recreates both (reversible)

  **QA Scenarios**:
  ```
  Scenario: Migration applies cleanly
    Tool: Bash
    Steps:
      1. cd backend && alembic upgrade head
      2. Assert: exit code 0
      3. python -c "import asyncio; from app.db import AsyncSessionLocal; from sqlalchemy import text; asyncio.run((lambda: None)())" — verify no table
    Expected Result: Clean migration
    Evidence: .sisyphus/evidence/task-4-migration.txt
  ```

  **Commit**: YES
  - Message: `feat(db): add migration to drop user_group_permissions and grouprole enum`
  - Files: `backend/alembic/versions/0003_*.py`

- [x] 5. User Management Frontend Page (/users)

  **What to do**:
  - Create `frontend/app/(dashboard)/users/page.tsx`:
    - **Header**: "Users" title + "New User" button
    - **Table**: columns — Email, Status (Active/Inactive badge), Superuser (Yes badge / —), Created, Actions (Edit/Delete)
    - **Create User Dialog**: form with email, password, confirm password, is_superuser checkbox. POST /api/admin/users
    - **Edit User Dialog**: form with email, is_active toggle, is_superuser toggle. PATCH /api/admin/users/{id}
    - **Reset Password Dialog**: new password + confirm. POST /api/admin/users/{id}/reset-password
    - **Delete Confirmation Dialog**: "Are you sure? This cannot be undone." DELETE /api/admin/users/{id}
    - Error handling for last-superuser guard (400 responses from API)
    - **Superuser gate**: If `!user?.is_superuser`, show "Access denied" and redirect
  - Add TypeScript types to `frontend/lib/types.ts`:
    ```typescript
    export interface AdminUser {
      id: number
      email: string
      is_active: boolean
      is_superuser: boolean
      is_verified: boolean
      created_at: string
      updated_at: string
    }
    ```
  - Use `useAuth()` hook from `@/lib/auth` to check superuser status
  - Follow EXACT same patterns as `frontend/app/(dashboard)/ssh-keys/page.tsx`

  **Must NOT do**:
  - Do NOT add pagination, search, or filtering
  - Do NOT show hashed passwords
  - Do NOT add loading skeletons
  - Do NOT add toast notifications
  - Do NOT create a separate `/profile` or `/settings` page

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T6, T7)
  - **Blocks**: T9
  - **Blocked By**: T2, T3

  **References**:
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — VERY similar CRUD page pattern (Dialog + Table + create/delete)
  - `frontend/app/(dashboard)/git-repos/page.tsx` — Another CRUD page with edit dialog
  - `frontend/lib/api.ts` — `apiFetch` pattern
  - `frontend/lib/auth.ts` — `useAuth()` hook for superuser check
  - `frontend/components/ui/dialog.tsx` — Dialog component

  **Acceptance Criteria**:
  - [ ] `/users` page renders with user table
  - [ ] "New User" dialog creates user
  - [ ] Edit dialog updates user (email, active, superuser)
  - [ ] Delete shows confirmation and removes user
  - [ ] Reset password dialog works
  - [ ] Last-superuser guard errors shown (from 400 API responses)
  - [ ] Non-superuser sees "Access denied"
  - [ ] `npm run build` passes

  **QA Scenarios**:
  ```
  Scenario: Create and manage a user
    Tool: Playwright
    Steps:
      1. Login as superuser
      2. Navigate to /users
      3. Assert: users table visible
      4. Click "New User"
      5. Fill email: "testuser@barricade.io", password: "TestPass1!", confirm: "TestPass1!"
      6. Submit
      7. Assert: new user appears in table
    Expected Result: User created via UI
    Evidence: .sisyphus/evidence/task-5-user-crud.png

  Scenario: Non-superuser blocked
    Tool: Playwright
    Steps:
      1. Login as regular user
      2. Navigate to /users
      3. Assert: "Access denied" or redirect
    Expected Result: Non-superusers cannot access
    Evidence: .sisyphus/evidence/task-5-access-denied.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add user management page with CRUD dialogs`
  - Files: `frontend/app/(dashboard)/users/page.tsx`, `frontend/lib/types.ts`

- [x] 6. Sidebar User Menu (Email + Logout + Password Change + Conditional Nav)

  **What to do**:
  - Modify `frontend/components/sidebar.tsx`:
    - Import `useAuth` from `@/lib/auth`
    - Import `useState` from React
    - Import Dialog components for password change
    - Import `Button` from ui/button
    - Import `apiFetch` from `@/lib/api`
    - **Conditional "Users" nav item**: Only show `{ href: '/users', label: 'Users' }` when `user?.is_superuser` is true. Move from static array to dynamic rendering.
    - **User section at sidebar bottom**: Pin to bottom of sidebar using flex layout:
      ```
      <div className="mt-auto border-t border-slate-700 pt-4">
        <div className="text-sm text-slate-300 truncate">{user?.email}</div>
        <div className="flex gap-2 mt-2">
          <button onClick={openPasswordDialog}>Change Password</button>
          <button onClick={logout}>Log Out</button>
        </div>
      </div>
      ```
    - **Password change dialog**: Inline Dialog with current password, new password, confirm new password. Calls `PATCH /users/me` with `{ password: newPassword }` (fastapi-users built-in endpoint).
    - Make the sidebar `<aside>` use `flex flex-col` so the nav stays at top and user section pins to bottom.

  **Must NOT do**:
  - Do NOT create a separate profile page
  - Do NOT add user avatar or display name
  - Do NOT add session management
  - Do NOT modify the AuthProvider

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T5, T7)
  - **Blocks**: T9
  - **Blocked By**: T3

  **References**:
  - `frontend/components/sidebar.tsx` — File to modify (current: static nav array, no user context)
  - `frontend/lib/auth.ts` — `useAuth()` returns `{ user, loading, logout }`
  - `frontend/app/providers.tsx:33-43` — `logout()` function (already implemented: POST /auth/jwt/logout → redirect to /login)
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — Dialog + form pattern to follow for password change
  - `frontend/components/ui/dialog.tsx` — Dialog component

  **Acceptance Criteria**:
  - [ ] Sidebar shows user email at bottom
  - [ ] "Log Out" button works (redirects to /login)
  - [ ] "Change Password" opens dialog with form
  - [ ] Password change submits to `PATCH /users/me` and shows success/error
  - [ ] "Users" nav item visible for superusers
  - [ ] "Users" nav item NOT visible for regular users
  - [ ] `npm run build` passes

  **QA Scenarios**:
  ```
  Scenario: Logout works
    Tool: Playwright
    Steps:
      1. Login as any user
      2. Assert: sidebar shows user email at bottom
      3. Click "Log Out"
      4. Assert: redirected to /login
    Expected Result: Clean logout
    Evidence: .sisyphus/evidence/task-6-logout.png

  Scenario: Superuser sees Users nav
    Tool: Playwright
    Steps:
      1. Login as superuser
      2. Assert: sidebar contains "Users" link
      3. Logout, login as regular user
      4. Assert: sidebar does NOT contain "Users" link
    Expected Result: Conditional nav rendering
    Evidence: .sisyphus/evidence/task-6-conditional-nav.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add sidebar user menu with logout and password change`
  - Files: `frontend/components/sidebar.tsx`

- [x] 7. Update Registration Flow (Setup-Status Gating)

  **What to do**:
  - Modify `frontend/app/(auth)/login/page.tsx`:
    - Fetch `GET /auth/setup-status` on mount (use fetch, not useQuery — this is the auth layout)
    - If `needs_setup` is true, show the "Don't have an account? Register" link
    - If `needs_setup` is false, hide the register link entirely
  - Modify `frontend/app/(auth)/register/page.tsx`:
    - On mount, fetch `GET /auth/setup-status`
    - If `needs_setup` is false, show a message: "Registration is closed. Contact your administrator." with a link back to login
    - If `needs_setup` is true, show the registration form (existing behavior)
    - After successful registration, redirect to `/login` with a success indication
  - Modify `frontend/middleware.ts`:
    - Keep `/register` in `PUBLIC_PATHS` (it needs to be accessible for first-user setup)
    - The register page itself handles showing "closed" message — middleware doesn't need changes

  **Must NOT do**:
  - Do NOT remove the register page entirely (needed for first-user setup)
  - Do NOT add email verification flow
  - Do NOT modify the registration form fields

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T5, T6)
  - **Blocks**: T9
  - **Blocked By**: T1

  **References**:
  - `frontend/app/(auth)/login/page.tsx` — Login page with register link (lines 81-85)
  - `frontend/app/(auth)/register/page.tsx` — Registration page (fetch /auth/setup-status on mount)
  - `frontend/middleware.ts` — Auth middleware (PUBLIC_PATHS)

  **Acceptance Criteria**:
  - [ ] Login page shows "Register" link when no users exist
  - [ ] Login page hides "Register" link when users exist
  - [ ] Register page shows form when no users exist
  - [ ] Register page shows "Registration closed" when users exist
  - [ ] `npm run build` passes

  **QA Scenarios**:
  ```
  Scenario: Registration closed message
    Tool: Playwright
    Steps:
      1. With users in DB, navigate to /register
      2. Assert: page shows "Registration is closed" message
      3. Assert: no registration form visible
    Expected Result: Blocked with clear message
    Evidence: .sisyphus/evidence/task-7-registration-closed.png
  ```

  **Commit**: YES
  - Message: `refactor(ui): gate registration page behind setup-status check`
  - Files: `frontend/app/(auth)/login/page.tsx`, `frontend/app/(auth)/register/page.tsx`

- [x] 8. Update Backend Test Fixtures for RBAC Removal

  **What to do**:
  - Modify `backend/tests/conftest.py`:
    - Remove ALL imports of `UserGroupPermission`, `GroupRole`
    - Remove `viewer_client` and `editor_client` fixtures (or convert them to simple regular-user clients)
    - Keep `superuser_client` fixture (already works via direct User creation)
    - Add a `regular_user_client` fixture if not already present (create a non-superuser user)
  - Update any test files that reference `viewer_client`, `editor_client`, `GroupRole`, or `UserGroupPermission`:
    - Search all test files for these references
    - Replace `viewer_client`/`editor_client` with `regular_user_client` or `superuser_client` as appropriate
    - Remove permission-granting setup code (no longer needed)
  - Add basic tests for the new admin user endpoints in `backend/tests/test_admin_users.py`:
    - Test list users (superuser → 200, regular → 403)
    - Test create user
    - Test last-superuser guard
    - Test password reset
  - Add test for registration gating in `backend/tests/test_auth.py`:
    - Test first registration succeeds + is_superuser=true
    - Test second registration returns 403
    - Test setup-status endpoint
  - Ensure `pytest tests/ --ignore=tests/integration -v` passes with zero failures

  **Must NOT do**:
  - Do NOT delete test files that test still-valid functionality
  - Do NOT add tests for removed features (RBAC)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: T9
  - **Blocked By**: T1, T2, T3, T4

  **References**:
  - `backend/tests/conftest.py` — Test fixtures (lines with UserGroupPermission/GroupRole)
  - `backend/tests/test_auth.py` — Auth tests to update
  - `backend/tests/test_sync.py` — May reference RBAC fixtures
  - `backend/tests/test_discovery.py` — May reference RBAC fixtures

  **Acceptance Criteria**:
  - [ ] `grep -r "UserGroupPermission\|GroupRole\|viewer_client\|editor_client" backend/tests/` → zero matches (or only in migration-related files)
  - [ ] `pytest tests/ --ignore=tests/integration -v` → all pass, zero failures
  - [ ] New test_admin_users.py has ≥5 tests
  - [ ] New registration gating tests in test_auth.py

  **Commit**: YES
  - Message: `test: update fixtures and add tests for RBAC removal and registration gating`
  - Files: `backend/tests/conftest.py`, `backend/tests/test_admin_users.py`, `backend/tests/test_auth.py`, any updated test files

- [x] 9. Build Verification + Full Test Suite Pass

  **What to do**:
  - Run `cd frontend && npm run build` — must pass with 0 errors
  - Verify `/users` page appears in build output
  - Run `cd backend && pytest tests/ --ignore=tests/integration -v` — all pass
  - Run `cd backend && ruff check app/` — no lint errors
  - Fix any issues found

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (after T8)
  - **Blocks**: F1-F4
  - **Blocked By**: T5, T6, T7, T8

  **Acceptance Criteria**:
  - [ ] `npm run build` exits 0
  - [ ] `/users` in build output
  - [ ] `pytest tests/ --ignore=tests/integration` → all pass
  - [ ] `ruff check app/` → clean

  **Commit**: YES (if fixes needed)
  - Message: `fix: resolve build and test issues from user management changes`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `npm run build` + `pytest tests/ --ignore=tests/integration -v` + ruff. Review all changed files for: `as any`/type ignores, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Build [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill)
  Start from clean state. Register first user → verify superuser. Try register second user → verify blocked. Create user via admin page. Log out. Log in as new user → verify no "Users" nav. Change password. Log out/in with new password.
  Output: `Scenarios [N/N pass] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Task | Commit Message | Key Files |
|------|---------------|-----------|
| T1 | `feat(auth): auto-promote first user to superuser and gate registration` | `auth/users.py`, `api/auth_setup.py`, `main.py` |
| T2 | `feat(api): add user management admin endpoints with last-superuser guard` | `api/admin_users.py`, `main.py` |
| T3 | `refactor(api): remove RBAC system and simplify authorization` | `auth/rbac.py`, `api/permissions.py`, `models/user_group_permission.py`, `api/rules.py`, `api/sync.py`, `api/groups.py`, `api/hosts.py` |
| T4 | `feat(db): add migration to drop user_group_permissions and grouprole enum` | `alembic/versions/0003_*.py` |
| T5 | `feat(ui): add user management page with CRUD dialogs` | `frontend/app/(dashboard)/users/page.tsx` |
| T6 | `feat(ui): add sidebar user menu with logout and password change` | `frontend/components/sidebar.tsx` |
| T7 | `refactor(ui): gate registration page behind setup-status check` | `frontend/app/(auth)/login/page.tsx`, `frontend/app/(auth)/register/page.tsx`, `frontend/middleware.ts` |
| T8 | `test: update fixtures and E2E tests for RBAC removal and registration gating` | `tests/conftest.py`, `e2e/*.spec.ts` |

---

## Success Criteria

### Verification Commands
```bash
# Backend tests pass
cd backend && pytest tests/ --ignore=tests/integration -v

# Frontend builds
cd frontend && npm run build

# First user is superuser
curl -s http://localhost:8000/auth/setup-status  # {"needs_setup": true}
# Register first user → auto superuser
# curl setup-status again → {"needs_setup": false}
# Second register → 403

# RBAC fully removed
grep -r "require_group_role" backend/app/  # zero matches
grep -r "UserGroupPermission" backend/app/  # zero matches

# E2E
cd frontend && npx playwright test
```

### Final Checklist
- [x] All "Must Have" present
- [x] All "Must NOT Have" absent
- [x] All tests pass
- [x] First-user flow works end-to-end
- [x] User management CRUD works
- [x] Logout + password change works
