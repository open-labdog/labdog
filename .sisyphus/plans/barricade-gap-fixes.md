# Barricade Gap Fixes — Redbeat Dependency + Missing Test Modules

## TL;DR

> **Quick Summary**: Fix two known gaps in the completed Barricade core plan: (1) add missing `redbeat` dependency that crashes the celery-beat container, and (2) write 8 missing pytest test modules plus build out the empty conftest.py with shared async fixtures for real PostgreSQL testing via testcontainers.
>
> **Deliverables**:
> - `redbeat>=2.0` added to `backend/pyproject.toml` dependencies
> - `backend/tests/conftest.py` with full async fixture suite (DB, auth clients, factories, Celery mocks)
> - 8 new test modules: test_auth, test_rbac, test_groups, test_hosts, test_sync, test_audit, test_drift, test_merge
> - `asyncio_default_fixture_loop_scope = "session"` in pytest config
> - Total test count: 60+ (36 existing + 24+ new)
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: T2 (conftest) → T3-T10 (test modules) → T11 (validation)

---

## Context

### Original Request
Fix known gaps from the completed core Barricade plan (29/29 tasks done). The CONTINUE.md documents three gaps; this plan addresses two (T18/T19 is handled by the GitOps v2 plan).

### Gaps Being Fixed
1. **T23 — Redbeat dependency**: `docker-compose.yml` references `--scheduler redbeat.RedBeatScheduler` but `redbeat` is not in `pyproject.toml`. The celery-beat container crashes on startup.
2. **T27 — Test coverage**: Only 4 of 12 planned test modules exist (crypto, diff, renderers, rules = 36 tests). 8 modules are missing. `conftest.py` is empty.

### Metis Review — Critical Findings
- **ENCRYPTION_KEY crash**: Default `settings.ENCRYPTION_KEY` decodes to 24 bytes, not 32. Any test touching SSH keys will crash. Fix: patch with `generate_master_key()` before app import.
- **Event loop scope**: Session-scoped async fixtures need `asyncio_default_fixture_loop_scope = "session"` in pytest config. Without it, `ScopeMismatch` error.
- **Celery without Redis**: `run_sync_playbook.delay()` crashes without Redis. Fix: mock Celery task dispatch.
- **Transaction isolation**: Shared DB needs savepoint-based rollback per test (`join_transaction_mode="create_savepoint"`). Without it, tests pollute each other.
- **Superuser promotion**: Must use same session as test, not separate connection (breaks savepoint isolation).
- **Audit log empty**: No API endpoints call `log_action()`. Tests must manually insert AuditLog records.
- **test_merge.py scope**: Must test the API endpoint `GET /api/hosts/{id}/effective-rules`, NOT duplicate unit tests from `test_rules.py::TestMerge`.

---

## Work Objectives

### Core Objective
Make the Barricade celery-beat container start successfully and bring test coverage from 36 to 60+ tests across all backend modules.

### Definition of Done
- [ ] `docker compose build celery-beat` succeeds (redbeat installs)
- [ ] `cd backend && pytest tests/ -v` passes ALL tests (0 failures, 60+ collected)
- [ ] All 12 planned test modules exist and pass
- [ ] conftest.py provides reusable async fixtures for future test development

### Must Have
- `redbeat>=2.0` in pyproject.toml main dependencies
- conftest.py with: session-scoped PostgresContainer, per-test savepoint rollback, authenticated clients (superuser/viewer/editor), Celery task mocks, helper factories
- 8 test modules with 3+ tests each (happy + error + edge)
- `asyncio_default_fixture_loop_scope = "session"` in pytest config

### Must NOT Have (Guardrails)
- ❌ Function-scoped PostgresContainer (kills performance — 24+ container startups)
- ❌ SQLite for tests (must match production PostgreSQL)
- ❌ Mocked DB layer (must test real SQL)
- ❌ Separate DB connections for test helpers (breaks savepoint isolation)
- ❌ test_merge.py duplicating test_rules.py::TestMerge unit tests
- ❌ Tests expecting audit log entries from API actions (endpoints don't write them)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest + pytest-asyncio configured)
- **Automated tests**: YES (this plan IS about tests)
- **Framework**: pytest + pytest-asyncio + httpx + testcontainers
- **DB**: PostgreSQL via testcontainers (session-scoped)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 2 parallel, no dependencies):
├── Task 1: Add redbeat dependency [quick]
└── Task 2: Build conftest.py + pytest config [ultrabrain]

Wave 2 (Test modules — 8 parallel, all depend on T2):
├── Task 3: test_auth.py [quick]
├── Task 4: test_rbac.py [quick]
├── Task 5: test_groups.py [quick]
├── Task 6: test_hosts.py [quick]
├── Task 7: test_sync.py [unspecified-low]
├── Task 8: test_audit.py [quick]
├── Task 9: test_drift.py [quick]
└── Task 10: test_merge.py [quick]

Wave 3 (Validation):
└── Task 11: Full suite run + fix any failures [quick]

Wave FINAL (Review — 4 parallel):
├── F1: Plan compliance audit [oracle]
├── F2: Code quality review [unspecified-high]
├── F3: Real QA — run full test suite [unspecified-high]
└── F4: Scope fidelity check [deep]

Critical Path: T2 → any T3-T10 → T11 → F1-F4
Max Concurrent: 8 (Wave 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T11 | 1 |
| T2 | — | T3-T10 | 1 |
| T3-T10 | T2 | T11 | 2 |
| T11 | T1, T3-T10 | F1-F4 | 3 |

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|------------|
| 1 | 2 | T1→`quick`, T2→`ultrabrain` |
| 2 | 8 | T3-T6,T8-T10→`quick`, T7→`unspecified-low` |
| 3 | 1 | T11→`quick` (+ `git-master` skill) |
| FINAL | 4 | F1→`oracle`, F2→`unspecified-high`, F3→`unspecified-high`, F4→`deep` |

---

## TODOs

- [ ] 1. Add Redbeat Dependency to pyproject.toml

  **What to do**:
  - Add `"redbeat>=2.0",` to the `dependencies` list in `backend/pyproject.toml` (after the `"celery[redis]>=5.4",` line)
  - This fixes the celery-beat container crash — the static `beat_schedule` in `app/tasks/drift.py` works through RedBeatScheduler without code changes

  **Must NOT do**:
  - Do NOT add to `[project.optional-dependencies].dev` — it's a runtime dependency
  - Do NOT modify any Python code — just the dependency list

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Task 11
  - **Blocked By**: None

  **References**:
  - `backend/pyproject.toml:17` — Current `"celery[redis]>=5.4",` line to add after
  - `docker-compose.yml:84` — References `--scheduler redbeat.RedBeatScheduler`

  **Acceptance Criteria**:
  - [ ] `grep "redbeat" backend/pyproject.toml` returns a line containing `"redbeat>=2.0"`
  - [ ] The line is in the `dependencies` list, NOT in `[project.optional-dependencies]`
  - [ ] `cd backend && pip install -e ".[dev]" && python -c "import redbeat; print('OK')"` prints `OK`

  **QA Scenarios**:
  ```
  Scenario: Redbeat dependency installs correctly
    Tool: Bash
    Steps:
      1. Run: cd backend && pip install -e ".[dev]"
      2. Assert: exit code 0
      3. Run: python -c "import redbeat; print(redbeat.__version__)"
      4. Assert: output contains a version string (e.g., "2.x.x")
    Expected Result: Redbeat installs as part of project dependencies
    Evidence: .sisyphus/evidence/task-1-redbeat-install.txt
  ```

  **Commit**: YES (standalone)
  - Message: `fix(deps): add missing redbeat dependency for celery-beat`
  - Files: `backend/pyproject.toml`

- [ ] 2. Build conftest.py + Update Pytest Config

  **What to do**:
  - **Update `backend/pyproject.toml`**: Add `asyncio_default_fixture_loop_scope = "session"` to `[tool.pytest.ini_options]`
  - **Rewrite `backend/tests/conftest.py`** with the following fixtures:

  **Session-scoped fixtures** (run once for entire test suite):
  - `pg_container` — Starts `PostgresContainer`, runs Alembic `upgrade head`, patches `settings.DATABASE_URL`
  - `engine` — Creates `AsyncEngine` from container URL
  - `app` — Returns FastAPI app instance with settings already patched

  **Function-scoped fixtures** (per test, with rollback):
  - `db` — Creates `AsyncSession` with `join_transaction_mode="create_savepoint"`. Overrides `get_db` dependency. Rolls back after test.
  - `client` — Unauthenticated `httpx.AsyncClient` against the app
  - `superuser_client` — Registers user via API, promotes to superuser via `db.execute(text("UPDATE users SET is_superuser=TRUE, is_verified=TRUE WHERE email=..."))`, logs in, returns authenticated client
  - `viewer_client` — Registers viewer user, creates test group, grants `viewer` permission, logs in
  - `editor_client` — Same as viewer but with `editor` role
  - `mock_celery_tasks` — Patches `app.tasks.sync.run_sync_playbook.delay` and `app.tasks.drift.check_host_drift.delay` with `MagicMock`

  **Helper factories** (plain functions exported from conftest):
  - `create_group(db, name, priority)` → HostGroup
  - `create_ssh_key(db, name)` → SSHKey (uses patched ENCRYPTION_KEY)
  - `create_host(db, hostname, ip, ssh_key_id, group_ids)` → Host
  - `create_rule(db, group_id, action, protocol, direction, ...)` → FirewallRule

  **Critical implementation details**:
  1. Patch `settings.ENCRYPTION_KEY = generate_master_key()` BEFORE any app module import (default decodes to 24 bytes → crash)
  2. Patch `settings.DATABASE_URL` with testcontainer async URL for Alembic
  3. Use `AsyncSession(bind=conn, join_transaction_mode="create_savepoint")` — makes `session.commit()` in endpoint handlers create savepoints, not real commits
  4. Override `get_db` with async generator: `async def override(): yield test_session`
  5. Superuser promotion: `db.execute(text("UPDATE users ..."))` directly — NOT separate connection
  6. Run Alembic via subprocess with `cwd=backend/` (same pattern as `test_full_workflow.py:65-72`)
  7. Each authenticated client gets its OWN `httpx.AsyncClient` instance (cookies are per-client)

  **Must NOT do**:
  - Do NOT use function-scoped PostgresContainer (24+ container startups = 60+ seconds)
  - Do NOT use SQLite
  - Do NOT create separate engine/connection for superuser promotion
  - Do NOT use `app.dependency_overrides[get_db] = lambda: session` (must be async generator)

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
    - Reason: Complex async fixture architecture — SQLAlchemy savepoints, pytest-asyncio session scoping, FastAPI dependency overrides, testcontainers. Getting this wrong breaks ALL 8 test modules.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Tasks 3-10
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `backend/tests/integration/test_full_workflow.py:40-72` — Existing testcontainers + Alembic pattern. Follow for container setup, but improve with savepoint isolation.
  - `backend/app/db.py` — `get_db()` async generator. This is the dependency to override.
  - `backend/app/config.py` — `Settings` class with `ENCRYPTION_KEY` and `DATABASE_URL`.
  - `backend/app/crypto/key_management.py:generate_master_key()` — Use to create valid ENCRYPTION_KEY for tests.
  - `backend/app/auth/users.py` — FastAPI-Users auth setup. Login endpoint sets `barricade_auth` cookie.
  - `backend/tests/test_rules.py` — Class-based test organization pattern to follow.
  - `backend/tests/test_renderers.py:8-16` — Helper function pattern (`_sample_rules()`).

  **Acceptance Criteria**:
  - [ ] `grep "asyncio_default_fixture_loop_scope" backend/pyproject.toml` returns match
  - [ ] `grep "join_transaction_mode" backend/tests/conftest.py` returns match
  - [ ] `grep "generate_master_key" backend/tests/conftest.py` returns match
  - [ ] All existing tests still pass: `cd backend && pytest tests/test_crypto.py tests/test_rules.py tests/test_diff.py tests/test_renderers.py -v --tb=short`

  **QA Scenarios**:
  ```
  Scenario: Conftest fixtures are discoverable
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest --fixtures tests/conftest.py 2>&1 | grep -cE "(db|client|superuser_client|viewer_client|editor_client|mock_celery)"
      2. Assert: count >= 6
    Expected Result: All required fixtures are registered
    Evidence: .sisyphus/evidence/task-2-fixtures.txt

  Scenario: Existing tests unbroken
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_crypto.py tests/test_rules.py tests/test_diff.py tests/test_renderers.py -v --tb=short
      2. Assert: exit code 0, 0 failures, 36 tests pass
    Expected Result: Conftest changes don't break existing unit tests
    Evidence: .sisyphus/evidence/task-2-existing-tests.txt
  ```

  **Commit**: YES (grouped with T3-T10 in one commit)
  - Message: `test(backend): add conftest fixtures and 8 integration test modules`
  - Files: `backend/tests/conftest.py`, `backend/pyproject.toml`

- [ ] 3. Write test_auth.py

  **What to do**:
  - Create `backend/tests/test_auth.py` with class `TestAuth`:
    - `test_register_user` — POST `/auth/register` with valid email/password → 201, response contains email
    - `test_login_sets_cookie` — POST `/auth/jwt/login` → 200, `barricade_auth` cookie set
    - `test_get_current_user` — GET `/users/me` with auth cookie → 200, returns email
    - `test_login_wrong_password` — POST `/auth/jwt/login` with bad password → 400
  - Add `pytestmark = pytest.mark.integration` at module level
  - Use `client` fixture for unauthenticated requests, `superuser_client` for authenticated

  **Must NOT do**:
  - Do NOT test OAuth/social login (not implemented)
  - Do NOT test rate limiting (hard to test reliably)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4-10)
  - **Blocks**: Task 11
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/auth/users.py` — Auth endpoints and cookie name (`barricade_auth`)
  - `backend/app/main.py` — Router registration paths for auth

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_auth.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: Auth test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_auth.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Expected Result: All auth tests pass
    Evidence: .sisyphus/evidence/task-3-auth-tests.txt
  ```

  **Commit**: YES (grouped with T2 and T4-T10)

- [ ] 4. Write test_rbac.py

  **What to do**:
  - Create `backend/tests/test_rbac.py` with class `TestRBAC`:
    - `test_superuser_can_create_group` — `superuser_client` POST `/api/groups` → 201
    - `test_viewer_cannot_create_group` — `viewer_client` POST `/api/groups` → 403
    - `test_viewer_can_list_groups` — `viewer_client` GET `/api/groups` → 200
    - `test_unauthenticated_rejected` — `client` (no auth) GET `/api/groups` → 401
  - Add `pytestmark = pytest.mark.integration`

  **Must NOT do**:
  - Do NOT test per-rule permissions (not implemented)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 5-10)
  - **Blocks**: Task 11
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/auth/rbac.py` — RBAC decorators and role definitions
  - `backend/app/api/groups.py` — Group endpoints with RBAC enforcement

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_rbac.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: RBAC test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_rbac.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Expected Result: All RBAC tests pass
    Evidence: .sisyphus/evidence/task-4-rbac-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 5. Write test_groups.py

  **What to do**:
  - Create `backend/tests/test_groups.py` with class `TestGroups`:
    - `test_create_group` — POST `/api/groups` → 201, returns group with id
    - `test_list_groups` — GET `/api/groups` → 200, returns list
    - `test_duplicate_name_409` — POST with same name → 409
    - `test_duplicate_priority_409` — POST with same priority → 409
    - `test_delete_group_with_hosts_400` — DELETE group that has hosts → 400
  - Add `pytestmark = pytest.mark.integration`
  - Use `superuser_client` fixture. Use conftest `create_host` factory for the delete-with-hosts test.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/groups.py` — Group CRUD endpoints
  - `backend/app/schemas/groups.py` — Request/response schemas

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_groups.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: Groups test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_groups.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Evidence: .sisyphus/evidence/task-5-groups-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 6. Write test_hosts.py

  **What to do**:
  - Create `backend/tests/test_hosts.py` with class `TestHosts`:
    - `test_create_host_with_group` — POST `/api/hosts` with group_ids → 201
    - `test_get_host` — GET `/api/hosts/{id}` → 200
    - `test_get_nonexistent_404` — GET `/api/hosts/99999` → 404
    - `test_delete_host` — DELETE `/api/hosts/{id}` → 200 or 204
  - Add `pytestmark = pytest.mark.integration`
  - Use `superuser_client` + conftest factories (`create_group`, `create_ssh_key`)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/hosts.py` — Host CRUD endpoints
  - `backend/app/api/ssh_keys.py` — SSH key endpoints (needed for host creation)

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_hosts.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: Hosts test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_hosts.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Evidence: .sisyphus/evidence/task-6-hosts-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 7. Write test_sync.py

  **What to do**:
  - Create `backend/tests/test_sync.py` with class `TestSync`:
    - `test_plan_host` — POST `/api/sync/hosts/{id}/plan` → 200, returns diff with `has_changes`
    - `test_trigger_sync_creates_job` — POST `/api/sync/hosts/{id}/sync` → 201, returns job with `pending` status, verify `run_sync_playbook.delay` was called via `mock_celery_tasks`
    - `test_get_job_status` — GET `/api/sync/jobs/{id}` → 200
    - `test_trigger_sync_when_running_409` — Create a SyncJob with status=running, trigger sync → 409
  - Add `pytestmark = pytest.mark.integration`
  - Use `superuser_client` + `mock_celery_tasks` fixtures. Requires full setup chain: SSH key → group → rules → host.

  **Must NOT do**:
  - Do NOT test actual ansible-runner execution (requires real target hosts)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Slightly more complex due to Celery mocking and full data setup chain
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/sync.py` — Sync/plan endpoints. Note `.delay()` calls at lines 240, 284.
  - `backend/app/tasks/sync.py` — `run_sync_playbook` task that gets mocked

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_sync.py -v --tb=short` — all pass, ≥3 tests
  - [ ] Tests verify `mock_celery_tasks` was called (not skipped)

  **QA Scenarios**:
  ```
  Scenario: Sync test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_sync.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Evidence: .sisyphus/evidence/task-7-sync-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 8. Write test_audit.py

  **What to do**:
  - Create `backend/tests/test_audit.py` with class `TestAudit`:
    - `test_list_empty` — GET `/api/audit-log` → 200, returns empty list
    - `test_list_with_entries` — Insert 3 `AuditLog` records via `db` fixture directly, GET `/api/audit-log` → returns 3
    - `test_filter_by_entity_type` — Insert records with different `entity_type` values, GET with `?entity_type=rule` → returns only matching
    - `test_cursor_pagination` — Insert 5 records, GET with `?limit=2` → returns 2, use cursor for next page → returns next batch
  - Add `pytestmark = pytest.mark.integration`
  - **CRITICAL**: No API endpoints call `log_action()`. Must manually insert `AuditLog` records via `db` fixture for testing.

  **Must NOT do**:
  - Do NOT expect audit entries from API actions (endpoints don't write them)
  - Do NOT test DELETE/PUT on audit log (endpoints don't exist — that's correct)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/audit.py` — Audit log read endpoints
  - `backend/app/models/audit_log.py` — AuditLog model for direct insertion

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_audit.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: Audit test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_audit.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Evidence: .sisyphus/evidence/task-8-audit-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 9. Write test_drift.py

  **What to do**:
  - Create `backend/tests/test_drift.py` with class `TestDrift`:
    - `test_check_drift_returns_status` — POST `/api/drift/hosts/{id}/check` → 200, response has `status` and `host_id`
    - `test_update_drift_settings` — PUT `/api/drift/hosts/{id}/settings` → 200, toggles `drift_check_enabled`
    - `test_check_drift_nonexistent_404` — POST `/api/drift/hosts/99999/check` → 404
  - Add `pytestmark = pytest.mark.integration`
  - Use `superuser_client`. Requires host setup chain.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/drift.py` — Drift check endpoints
  - `backend/app/drift/detector.py` — Drift detection logic (uses `fetch_current_state_stub`)

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_drift.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: Drift test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_drift.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Evidence: .sisyphus/evidence/task-9-drift-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 10. Write test_merge.py

  **What to do**:
  - Create `backend/tests/test_merge.py` with class `TestMerge`:
    - `test_effective_rules_single_group` — Create group + rules + host via API, GET `/api/hosts/{id}/effective-rules` → returns rules including SSH lockout system rule
    - `test_effective_rules_priority_merge` — Create 2 groups with different priorities, add conflicting rules (same port, different action), assign host to both → higher priority wins
    - `test_effective_rules_no_groups` — Create host without groups → returns only SSH lockout rule
  - Add `pytestmark = pytest.mark.integration`
  - **CRITICAL**: Test the API endpoint, NOT `merge_group_rules()` function (already tested in `test_rules.py::TestMerge`)

  **Must NOT do**:
  - Do NOT duplicate `test_rules.py::TestMerge` unit tests — those test the pure function
  - This tests the API endpoint that exercises merge through real DB records

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/rules.py` — `GET /api/hosts/{host_id}/effective-rules` endpoint
  - `backend/app/rules/merge.py` — `merge_group_rules()` function called by endpoint
  - `backend/tests/test_rules.py:TestMerge` — Existing unit tests to NOT duplicate

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_merge.py -v --tb=short` — all pass, ≥3 tests

  **QA Scenarios**:
  ```
  Scenario: Merge test module passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_merge.py -v --tb=short
      2. Assert: exit code 0, ≥3 tests, 0 failures
    Evidence: .sisyphus/evidence/task-10-merge-tests.txt
  ```

  **Commit**: YES (grouped)

- [ ] 11. Full Test Suite Validation

  **What to do**:
  - Run the complete test suite: `cd backend && pytest tests/ -v --tb=short`
  - Verify all tests pass (existing 36 + new 24+ = 60+ total)
  - If any cross-module failures: fix them
  - Verify test count: `pytest tests/ --co -q | tail -1` shows ≥60 collected
  - Create the commits (see Commit Strategy above)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Tasks 1-10
  - **Blocks**: F1-F4

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/ -v --tb=short` — ALL pass, 0 failures
  - [ ] `cd backend && pytest tests/ --co -q | tail -1` — shows ≥60 tests collected
  - [ ] Two atomic commits created per Commit Strategy

  **QA Scenarios**:
  ```
  Scenario: Full test suite green
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/ -v --tb=short
      2. Assert: exit code 0
      3. Assert: 0 failures in output
      4. Run: pytest tests/ --co -q | tail -1
      5. Assert: number >= 60
    Expected Result: Complete test suite passes
    Evidence: .sisyphus/evidence/task-11-full-suite.txt
  ```

  **Commit**: YES (execute the 2 commits from Commit Strategy)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Verify: redbeat in pyproject.toml, all 12 test modules exist, 60+ tests pass, conftest has required fixtures.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `pytest tests/ -v`. Check test files for: proper assertions, no hardcoded DB URLs, no `as any`, clean imports. Verify Celery mocking is correct.

- [ ] F3. **Real QA** — `unspecified-high`
  Run full test suite from clean state. Verify all 60+ tests pass. Check test isolation (run tests in random order with `pytest --randomly`).

- [ ] F4. **Scope Fidelity Check** — `deep`
  Verify no scope creep: only redbeat dep + test files added. No source code changes (except pyproject.toml config). No new features.

---

## Commit Strategy

| Phase | Commit Message | Key Files |
|-------|---------------|-----------|
| T1 | `fix(deps): add missing redbeat dependency for celery-beat` | `backend/pyproject.toml` |
| T2-T10 | `test(backend): add conftest fixtures and 8 integration test modules` | `backend/tests/conftest.py`, `backend/pyproject.toml`, `backend/tests/test_*.py` (8 files) |

---

## Success Criteria

### Verification Commands
```bash
# Redbeat dependency
grep "redbeat" backend/pyproject.toml  # Expected: "redbeat>=2.0" in dependencies

# Full test suite
cd backend && pytest tests/ -v --tb=short  # Expected: ALL pass, 0 failures

# Test count
cd backend && pytest tests/ --co -q | tail -1  # Expected: 60+ tests collected

# Specific modules
cd backend && pytest tests/test_auth.py tests/test_rbac.py tests/test_groups.py tests/test_hosts.py tests/test_sync.py tests/test_audit.py tests/test_drift.py tests/test_merge.py -v  # Expected: ALL pass
```
