# Barricade — Ansible Firewall Management Web Application

## TL;DR

> **Quick Summary**: Build "Barricade" — a web application for centrally managing Linux firewall rules (nftables, firewalld, ufw) across dozens-to-hundreds of hosts, using Ansible as the enforcement layer. The web app is the single source of truth: users edit rules in a polished UI, preview diffs, and trigger syncs that push rules via dynamically-generated Ansible playbooks.
>
> **Deliverables**:
> - FastAPI backend with JWT auth, per-host-group RBAC, REST API
> - React frontend (Next.js + shadcn/ui + Tailwind) with rule management, host management, sync UI, audit log
> - Celery + Redis background task system for async Ansible execution via ansible-runner
> - PostgreSQL database: hosts, groups, rules, users, audit log, sync status
> - Ansible playbook generator for nftables (template-based), firewalld (module-based), ufw (file-based)
> - Plan-before-apply diff engine (terraform plan pattern)
> - Drift detection (periodic + manual)
> - Docker Compose deployment
>
> **Estimated Effort**: XL
> **Parallel Execution**: YES — 8 waves
> **Critical Path**: Scaffold → DB Models → Auth → Host CRUD → Rule Engine → Ansible Integration → Drift Detection → Final QA

---

## Context

### Original Request
User wants a web application to manage Linux firewalls using Ansible. The application should fetch current rules, edit them in a UI, and trigger syncs. The web app is always the source of truth — rules are never edited directly on hosts.

### Interview Summary
**Key Discussions**:
- **Database**: Confirmed needed for persistent storage, audit trail, sync status tracking
- **Firewall backends**: nftables, firewalld, ufw — NOT iptables (legacy). Each host has one backend, auto-detected on first connection with manual override
- **Tech stack**: FastAPI + React chosen for native ansible-runner integration, async-first architecture, modern UI
- **Auth model**: Multi-user with JWT, per-host-group RBAC (admin/editor/viewer roles)
- **Rule model**: Group-only — rules assigned to host groups, not individual hosts. Hosts can belong to multiple groups with priority-based merge (higher priority wins on conflict)
- **Plan-before-apply**: Must-have for v1 — show diff of current vs desired rules before sync
- **SSH key storage**: AES-256 encrypted in DB, master key via environment variable
- **Drift detection**: User-selectable periodic checks + manual "check now"
- **Audit trail**: Full logging of all actions with before/after state
- **Real-time updates**: Polling (3-5s) for v1, WebSocket upgrade in v2
- **IPv6**: Supported in v1

**Research Findings**:
- **ansible-runner**: Official Python library used by AWX. `run_async()` + `event_handler` for streaming. Production-grade.
- **nftables**: Template-based atomic replacement (`flush ruleset` + `nft -f`) is most reliable. `nft -j list ruleset` for JSON drift detection.
- **firewalld**: `ansible.posix.firewalld` + `firewalld_info`. Zone-based. `permanent: true` + `immediate: true` always.
- **ufw**: Write directly to `/etc/ufw/user.rules` + `ufw reload` (NOT `ufw reset` which causes lockout window). Slurp `/etc/ufw/user.rules` for drift.
- **No competitor**: No open-source tool fills this exact gap.

### Metis Review
**Identified Gaps** (all addressed):
- **SSH lockout prevention**: Auto-inject non-deletable SSH allow rule for Barricade server IP
- **Host-group cardinality**: Multiple groups per host, priority-based merge
- **Initial state import**: Show current rules on host add, let user decide what to keep
- **IPv6**: Include in v1 (inet family for nftables, dual-stack for others)
- **Concurrent sync**: Advisory lock per host, reject simultaneous syncs
- **Empty group sync**: Reject to prevent accidental lockout
- **ansible-runner pitfalls**: Use `tempfile.mkdtemp()` per job, decrypt keys inside Celery task only, pass host_id not key material, set timeout + cancel_callback
- **UFW reconciliation**: Write files directly, never use `ufw reset`
- **Known hosts**: `ssh-keyscan` on host add

---

## Work Objectives

### Core Objective
Build a production-ready web application ("Barricade") that enables centralized, auditable management of Linux firewall rules across a fleet of hosts, using Ansible as the enforcement mechanism and a polished React UI as the management interface.

### Concrete Deliverables
- `barricade/` project root with backend + frontend
- `barricade/backend/` — FastAPI app, Celery workers, Ansible playbook generator
- `barricade/frontend/` — Next.js app with shadcn/ui components
- `docker-compose.yml` — full deployment stack (FastAPI, Celery worker, Celery beat, Redis, PostgreSQL, Next.js)
- Database migrations via Alembic
- Ansible playbook templates for nftables, firewalld, ufw
- Full test suite (pytest + Playwright)

### Definition of Done
- [ ] User can register, log in, and manage hosts/groups with RBAC
- [ ] User can create/edit/delete firewall rules on host groups
- [ ] User can preview diff of changes before applying (plan-before-apply)
- [ ] User can trigger sync that pushes rules to hosts via Ansible
- [ ] Sync status and output visible in UI with 3-5s polling
- [ ] Drift detection works (periodic + manual) with in-sync/out-of-sync/unknown per host
- [ ] Full audit trail of all actions with before/after state
- [ ] SSH lockout prevention: non-deletable SSH allow rule auto-injected
- [ ] All three firewall backends work: nftables, firewalld, ufw
- [ ] Docker Compose deploys the entire stack
- [ ] All tests pass (pytest + Playwright)

### Must Have
- JWT authentication with user registration and login
- Per-host-group RBAC (admin/editor/viewer)
- Host CRUD with SSH key upload (AES-256 encrypted in DB)
- Firewall backend auto-detection + manual override
- Host group CRUD with priority ordering
- Rule CRUD with priority/ordering within groups
- Support for: allow/deny, TCP/UDP/ICMP, port/port-range, source/dest CIDR (IPv4+IPv6), direction (INPUT/OUTPUT)
- Auto-injected SSH lockout prevention rule (non-deletable)
- Plan-before-apply diff display
- Ansible sync via Celery + ansible-runner
- Drift detection (periodic + manual)
- Full audit log (append-only)
- Host initial state import on add
- Docker Compose deployment

### Must NOT Have (Guardrails)
- ❌ iptables backend support
- ❌ NAT, port forwarding, or FORWARD chain rules
- ❌ Email/Slack/webhook notifications
- ❌ Rule templates or presets
- ❌ Host auto-discovery / network scanning
- ❌ Multi-tenancy / organization model
- ❌ Abstract factory patterns for firewall backends — use simple if/elif dispatch
- ❌ GraphQL — REST only
- ❌ WebSocket for v1 — polling only
- ❌ `ufw reset` for reconciliation — file write + reload only
- ❌ `ANSIBLE_HOST_KEY_CHECKING=False` in production
- ❌ SSH keys with passphrases (v1 limitation)
- ❌ Excessive comments/JSDoc — code should be self-documenting
- ❌ More than 4 custom error types

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO (greenfield)
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + pytest-asyncio + httpx (backend), Playwright (frontend)
- **DB for tests**: PostgreSQL via Docker (no SQLite — async + JSONB compatibility)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **API endpoints**: Use Bash (curl or httpx) — send requests, assert status + response fields
- **Backend logic**: Use Bash (pytest) — run test suites, assert pass counts
- **Frontend UI**: Use Playwright (playwright skill) — navigate, interact, assert DOM, screenshot
- **Ansible playbooks**: Use Bash — run `ansible-playbook --syntax-check` on generated output
- **nftables templates**: Use Bash — run `nft -c -f <file>` for validation

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — scaffolding + config):
├── Task 1: Backend project scaffold (FastAPI + pyproject.toml + structure) [quick]
├── Task 2: Frontend project scaffold (Next.js + shadcn/ui + Tailwind) [quick]
├── Task 3: Docker Compose full stack [quick]
└── Task 4: Database foundation (SQLAlchemy + Alembic + core models) [unspecified-high]

Wave 2 (Auth + Encryption — parallel, no cross-deps):
├── Task 5: SSH key encryption module (AES-256-GCM) [unspecified-high]
├── Task 6: Auth system (fastapi-users + JWT + user registration) [unspecified-high]
└── Task 7: RBAC middleware (per-host-group permissions) [deep]

Wave 3 (Host + Group Management — depends on auth + models):
├── Task 8: Host Group CRUD API [unspecified-high]
├── Task 9: Host CRUD API + SSH key upload + firewall auto-detect [unspecified-high]
├── Task 10: Frontend auth pages (login, register, protected routes) [visual-engineering]
└── Task 11: Frontend host/group management UI [visual-engineering]

Wave 4 (Rule Engine — depends on host/group models):
├── Task 12: Abstract rule model + validation + priority merge logic [deep]
├── Task 13: Rule CRUD API + SSH lockout prevention [unspecified-high]
├── Task 14: Backend-specific rule renderers (nftables, firewalld, ufw) [deep]
└── Task 15: Frontend rule management UI [visual-engineering]

Wave 5 (Ansible Integration — depends on rule renderers):
├── Task 16: Ansible playbook generator (DB rules → playbook YAML per backend) [deep]
├── Task 17: Celery task infrastructure + ansible-runner wrapper [unspecified-high]
├── Task 18: Plan/diff engine (current host state vs DB desired state) [deep]
└── Task 19: Host initial state import (fetch + display + import flow) [unspecified-high]

Wave 6 (Sync + Drift — depends on Ansible integration):
├── Task 20: Sync execution flow (trigger → Celery → ansible-runner → status update) [unspecified-high]
├── Task 21: Frontend sync UI (plan diff view, apply button, task status polling) [visual-engineering]
├── Task 22: Drift detection engine (per-backend state collection + comparison) [deep]
└── Task 23: Drift detection scheduling (Celery beat periodic + manual trigger) [unspecified-high]

Wave 7 (Audit + Polish — depends on core features):
├── Task 24: Audit logging system (middleware + model hooks, append-only) [unspecified-high]
├── Task 25: Frontend drift status dashboard [visual-engineering]
├── Task 26: Frontend audit log viewer [visual-engineering]
└── Task 27: Backend tests (pytest suite for all API endpoints + logic) [unspecified-high]

Wave 8 (Integration + E2E — depends on all features):
├── Task 28: Frontend Playwright E2E tests [unspecified-high]
└── Task 29: End-to-end integration test (full workflow validation) [deep]

Wave FINAL (Independent review — 4 parallel):
├── Task F1: Plan compliance audit [oracle]
├── Task F2: Code quality review [unspecified-high]
├── Task F3: Real manual QA via Playwright [unspecified-high]
└── Task F4: Scope fidelity check [deep]

Critical Path: T1 → T4 → T6 → T8 → T12 → T14 → T16 → T18 → T20 → T22 → T24 → T28 → F1-F4
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 4 (Waves 1, 3, 4, 5, 6, 7)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T4, T5, T6, T7 | 1 |
| T2 | — | T10, T11 | 1 |
| T3 | — | T20 | 1 |
| T4 | T1 | T5, T6, T7, T8, T9 | 1 |
| T5 | T4 | T9 | 2 |
| T6 | T4 | T7, T8, T9, T10 | 2 |
| T7 | T6 | T8, T9, T13 | 2 |
| T8 | T6, T7 | T9, T11, T12 | 3 |
| T9 | T5, T7, T8 | T11, T16, T19 | 3 |
| T10 | T2, T6 | T11 | 3 |
| T11 | T2, T8, T9, T10 | T15 | 3 |
| T12 | T8 | T13, T14 | 4 |
| T13 | T7, T12 | T15, T16 | 4 |
| T14 | T12 | T16, T18, T22 | 4 |
| T15 | T11, T13 | T21 | 4 |
| T16 | T9, T13, T14 | T17, T18, T20 | 5 |
| T17 | T16 | T20, T23 | 5 |
| T18 | T14, T16 | T20, T21 | 5 |
| T19 | T9, T14 | T21 | 5 |
| T20 | T3, T17, T18 | T21, T22, T27 | 6 |
| T21 | T15, T18, T19, T20 | T25 | 6 |
| T22 | T14, T20 | T23, T25 | 6 |
| T23 | T17, T22 | T25 | 6 |
| T24 | T6, T13, T20 | T26, T27 | 7 |
| T25 | T21, T22, T23 | T28 | 7 |
| T26 | T24 | T28 | 7 |
| T27 | T20, T24 | T29 | 7 |
| T28 | T25, T26 | F1-F4 | 8 |
| T29 | T27, T28 | F1-F4 | 8 |

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|------------|
| 1 | 4 | T1→`quick`, T2→`quick`, T3→`quick`, T4→`unspecified-high` |
| 2 | 3 | T5→`unspecified-high`, T6→`unspecified-high`, T7→`deep` |
| 3 | 4 | T8→`unspecified-high`, T9→`unspecified-high`, T10→`visual-engineering`, T11→`visual-engineering` |
| 4 | 4 | T12→`deep`, T13→`unspecified-high`, T14→`deep`, T15→`visual-engineering` |
| 5 | 4 | T16→`deep`, T17→`unspecified-high`, T18→`deep`, T19→`unspecified-high` |
| 6 | 4 | T20→`unspecified-high`, T21→`visual-engineering`, T22→`deep`, T23→`unspecified-high` |
| 7 | 4 | T24→`unspecified-high`, T25→`visual-engineering`, T26→`visual-engineering`, T27→`unspecified-high` |
| 8 | 2 | T28→`unspecified-high`, T29→`deep` |
| FINAL | 4 | F1→`oracle`, F2→`unspecified-high`, F3→`unspecified-high`, F4→`deep` |

---

## TODOs

- [x] 1. Backend Project Scaffold

  **What to do**:
  - Create `barricade/backend/` directory structure:
    ```
    backend/
    ├── app/
    │   ├── __init__.py
    │   ├── main.py          # FastAPI app factory
    │   ├── config.py         # Settings via pydantic-settings (env vars)
    │   ├── api/              # Route modules
    │   │   └── __init__.py
    │   ├── models/           # SQLAlchemy/SQLModel models
    │   │   └── __init__.py
    │   ├── schemas/          # Pydantic request/response schemas
    │   │   └── __init__.py
    │   ├── services/         # Business logic
    │   │   └── __init__.py
    │   ├── tasks/            # Celery tasks
    │   │   └── __init__.py
    │   ├── rules/            # Rule model + renderers
    │   │   └── __init__.py
    │   ├── ansible/          # Playbook generator
    │   │   └── __init__.py
    │   ├── crypto/           # Encryption utilities
    │   │   └── __init__.py
    │   └── audit/            # Audit logging
    │       └── __init__.py
    ├── tests/
    │   └── __init__.py
    ├── alembic/              # DB migrations
    ├── pyproject.toml        # Dependencies + tool config
    └── alembic.ini
    ```
  - `pyproject.toml` with dependencies: fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic-settings, celery[redis], ansible-runner, cryptography, fastapi-users[sqlalchemy], httpx (dev), pytest (dev), pytest-asyncio (dev)
  - `app/main.py`: FastAPI app with `/health` endpoint returning `{"status": "ok"}`
  - `app/config.py`: Settings class reading from env vars (DATABASE_URL, REDIS_URL, SECRET_KEY, ENCRYPTION_KEY)
  - Ruff for linting config in pyproject.toml

  **Must NOT do**:
  - Do NOT install iptables-related packages
  - Do NOT add GraphQL dependencies
  - Do NOT create abstract base classes or factory patterns yet

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Standard project scaffolding with known file structure
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5, 6, 7
  - **Blocked By**: None

  **References**:
  - FastAPI project structure: https://fastapi.tiangolo.com/tutorial/bigger-applications/
  - pydantic-settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

  **Acceptance Criteria**:
  - [ ] `cd barricade/backend && python -c "from app.main import app; print(app.title)"` prints app name
  - [ ] `cd barricade/backend && pip install -e ".[dev]"` installs without errors
  - [ ] `curl http://localhost:8000/health` returns `{"status": "ok"}` when uvicorn runs

  **QA Scenarios**:
  ```
  Scenario: Backend app starts and health check responds
    Tool: Bash
    Preconditions: Dependencies installed via pip install -e ".[dev]"
    Steps:
      1. Run: cd barricade/backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 &
      2. Wait 3 seconds for startup
      3. Run: curl -s http://localhost:8000/health
      4. Assert response body is exactly: {"status":"ok"}
      5. Assert HTTP status code is 200
      6. Kill uvicorn process
    Expected Result: Health endpoint returns 200 with {"status":"ok"}
    Failure Indicators: Connection refused, non-200 status, missing response body
    Evidence: .sisyphus/evidence/task-1-health-check.txt
  ```

  **Commit**: YES
  - Message: `feat(backend): scaffold FastAPI project structure`
  - Files: `barricade/backend/`

- [x] 2. Frontend Project Scaffold

  **What to do**:
  - Create `barricade/frontend/` with Next.js 15 (App Router):
    ```
    frontend/
    ├── app/
    │   ├── layout.tsx        # Root layout with providers
    │   ├── page.tsx          # Landing/dashboard redirect
    │   └── globals.css       # Tailwind imports
    ├── components/
    │   └── ui/               # shadcn/ui components
    ├── lib/
    │   ├── api.ts            # API client (fetch wrapper with auth)
    │   └── utils.ts          # cn() utility
    ├── package.json
    ├── tailwind.config.ts
    ├── tsconfig.json
    └── next.config.ts
    ```
  - Initialize shadcn/ui with "default" theme, dark mode support
  - Install base shadcn components: button, card, input, label, table, dialog, dropdown-menu, badge, toast, tabs, separator
  - Install TanStack Query for server state management
  - Set up a consistent dark theme suitable for infrastructure tooling (think Grafana/Semaphore aesthetic)
  - Create a basic layout shell: sidebar navigation + main content area

  **Must NOT do**:
  - Do NOT add GraphQL client
  - Do NOT add WebSocket client libraries (polling only for v1)
  - Do NOT build actual pages yet — just the shell

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Standard Next.js scaffolding with known tools
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Needed for layout shell and dark theme setup

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Tasks 10, 11
  - **Blocked By**: None

  **References**:
  - shadcn/ui installation: https://ui.shadcn.com/docs/installation/next
  - TanStack Query with Next.js: https://tanstack.com/query/latest/docs/framework/react/guides/ssr

  **Acceptance Criteria**:
  - [ ] `cd barricade/frontend && npm run build` completes without errors
  - [ ] `cd barricade/frontend && npm run dev` starts on port 3000
  - [ ] App shell renders with sidebar navigation and dark theme

  **QA Scenarios**:
  ```
  Scenario: Frontend builds and renders shell layout
    Tool: Bash
    Preconditions: Node.js installed, dependencies via npm install
    Steps:
      1. Run: cd barricade/frontend && npm run build
      2. Assert: exit code 0, no TypeScript errors in output
      3. Run: npm run start &
      4. Wait 3 seconds
      5. Run: curl -s http://localhost:3000 | grep -c "Barricade"
      6. Assert: count >= 1 (page contains "Barricade")
    Expected Result: Build succeeds, app serves HTML with "Barricade" title
    Failure Indicators: Build errors, TypeScript errors, blank page
    Evidence: .sisyphus/evidence/task-2-frontend-build.txt
  ```

  **Commit**: YES
  - Message: `feat(frontend): scaffold Next.js + shadcn/ui project`
  - Files: `barricade/frontend/`

- [x] 3. Docker Compose Full Stack

  **What to do**:
  - Create `barricade/docker-compose.yml` with services:
    - `postgres`: PostgreSQL 16, volume for data persistence, healthcheck
    - `redis`: Redis 7, healthcheck
    - `backend`: FastAPI app (Dockerfile), depends_on postgres + redis, env vars for DB/Redis/secrets
    - `celery-worker`: Same image as backend, runs `celery -A app.tasks worker --max-tasks-per-child=100 -Q default,long_running`, depends_on redis + postgres
    - `celery-beat`: Same image, runs `celery -A app.tasks beat --scheduler redbeat.RedBeatScheduler`, depends_on redis
    - `frontend`: Next.js app (Dockerfile), depends_on backend
    - `migrate`: One-shot container running `alembic upgrade head`, depends_on postgres
  - Create `barricade/backend/Dockerfile` (Python 3.12, multi-stage build, non-root user)
  - Create `barricade/frontend/Dockerfile` (Node 20, multi-stage build)
  - Create `barricade/.env.example` with all required env vars (DATABASE_URL, REDIS_URL, ENCRYPTION_KEY, SECRET_KEY)
  - **CRITICAL**: ENCRYPTION_KEY must be env var, NEVER in a committed file
  - Use separate Celery queues: `default` (fast API-triggered tasks) and `long_running` (Ansible playbook execution)

  **Must NOT do**:
  - Do NOT use SQLite anywhere
  - Do NOT hardcode secrets in docker-compose.yml
  - Do NOT skip healthchecks

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Standard Docker Compose setup with known services
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 20
  - **Blocked By**: None (can scaffold before backend/frontend are complete)

  **References**:
  - Celery with Redis: https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html
  - redbeat scheduler: https://github.com/sibson/redbeat

  **Acceptance Criteria**:
  - [ ] `docker-compose config` validates without errors
  - [ ] `.env.example` contains all required variables with placeholder values
  - [ ] Celery worker config uses `--max-tasks-per-child=100` and `-Q default,long_running`
  - [ ] PostgreSQL service has healthcheck and persistent volume

  **QA Scenarios**:
  ```
  Scenario: Docker Compose config validates
    Tool: Bash
    Preconditions: Docker and docker-compose installed
    Steps:
      1. Run: cd barricade && cp .env.example .env
      2. Run: docker-compose config > /dev/null 2>&1
      3. Assert: exit code 0
      4. Run: docker-compose config --services | sort
      5. Assert output contains: backend, celery-beat, celery-worker, frontend, migrate, postgres, redis
    Expected Result: Config valid, all 7 services defined
    Failure Indicators: YAML parse error, missing service definitions
    Evidence: .sisyphus/evidence/task-3-docker-config.txt
  ```

  **Commit**: YES
  - Message: `feat(infra): add Docker Compose full stack`
  - Files: `barricade/docker-compose.yml`, `barricade/backend/Dockerfile`, `barricade/frontend/Dockerfile`, `barricade/.env.example`

- [x] 4. Database Foundation (SQLAlchemy + Alembic + Core Models)

  **What to do**:
  - Set up async SQLAlchemy engine with `asyncpg` driver
  - Configure Alembic with naming conventions (MUST be set before first migration):
    ```python
    convention = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
    ```
  - Create async session dependency (`get_db`) for FastAPI
  - Define core SQLAlchemy models:
    - **User**: id, email, hashed_password, is_active, is_superuser, created_at, updated_at
    - **HostGroup**: id, name, description, priority (integer, higher = wins in merge), created_at, updated_at
    - **Host**: id, hostname, ip_address, ssh_port (default 22), firewall_backend (enum: nftables/firewalld/ufw/unknown), ssh_key_id (FK), sync_status (enum: pending/in_sync/out_of_sync/unknown/error), last_sync_at, last_drift_check_at, created_at, updated_at
    - **HostGroupMembership**: host_id, group_id (M2M join table)
    - **SSHKey**: id, name, encrypted_private_key (bytes), public_key (text), is_default (boolean), created_at
    - **FirewallRule**: id, group_id (FK), action (enum: allow/deny/reject), protocol (enum: tcp/udp/icmp/any), direction (enum: input/output), source_cidr (nullable, supports IPv4+IPv6), destination_cidr (nullable), port_start (nullable int), port_end (nullable int for ranges), priority (int for ordering within group), comment, is_system (boolean — true for auto-injected SSH rule), created_at, updated_at
    - **SyncJob**: id, host_id (FK), group_id (FK), status (enum: pending/running/success/failed/cancelled), started_at, completed_at, ansible_output (text), error_message (nullable), triggered_by_user_id (FK)
    - **AuditLog**: id, user_id (FK, nullable for system actions), action (string), entity_type (string), entity_id (int), before_state (JSONB, nullable), after_state (JSONB, nullable), ip_address, created_at — **APPEND-ONLY** (no update/delete)
    - **UserGroupPermission**: user_id, group_id, role (enum: admin/editor/viewer)
  - Generate first Alembic migration
  - Run migration against PostgreSQL

  **Must NOT do**:
  - Do NOT use SQLite-compatible column types — use PostgreSQL-native types (JSONB, etc.)
  - Do NOT make AuditLog deletable — no cascade delete, no soft delete
  - Do NOT create abstract factory patterns for models

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Complex model design with many tables, relationships, and constraints
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T1 for project structure)
  - **Parallel Group**: Wave 1 (sequential after T1)
  - **Blocks**: Tasks 5, 6, 7, 8, 9
  - **Blocked By**: Task 1

  **References**:
  - SQLAlchemy async: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
  - Alembic naming conventions: https://alembic.sqlalchemy.org/en/latest/naming.html
  - FastAPI + SQLAlchemy async pattern: https://fastapi.tiangolo.com/tutorial/sql-databases/

  **Acceptance Criteria**:
  - [ ] `alembic upgrade head` runs without errors against PostgreSQL
  - [ ] `alembic downgrade -1` rolls back cleanly
  - [ ] All models importable: `from app.models import User, Host, HostGroup, FirewallRule, ...`
  - [ ] Async session dependency works in FastAPI endpoint
  - [ ] JSONB columns used for AuditLog before_state/after_state

  **QA Scenarios**:
  ```
  Scenario: Database migrations run successfully
    Tool: Bash
    Preconditions: PostgreSQL running via Docker, DATABASE_URL set
    Steps:
      1. Run: cd barricade/backend && alembic upgrade head
      2. Assert: exit code 0, output contains "Running upgrade"
      3. Run: alembic current
      4. Assert: output shows current revision (not "None")
      5. Run: alembic downgrade -1
      6. Assert: exit code 0
      7. Run: alembic upgrade head
      8. Assert: exit code 0
    Expected Result: Migrations apply and roll back cleanly
    Failure Indicators: SQL errors, missing tables, constraint violations
    Evidence: .sisyphus/evidence/task-4-migrations.txt

  Scenario: Models create records correctly
    Tool: Bash
    Preconditions: Migrations applied, async engine configured
    Steps:
      1. Run: cd barricade/backend && python -c "
         import asyncio
         from app.models import HostGroup
         from app.db import async_session
         async def test():
           async with async_session() as session:
             g = HostGroup(name='test-group', priority=100)
             session.add(g)
             await session.commit()
             await session.refresh(g)
             print(f'Created group id={g.id} name={g.name}')
             await session.delete(g)
             await session.commit()
             print('Cleanup done')
         asyncio.run(test())
         "
      2. Assert: output contains "Created group id=" and "Cleanup done"
    Expected Result: Model CRUD works via async session
    Failure Indicators: Import error, connection error, SQL error
    Evidence: .sisyphus/evidence/task-4-model-crud.txt
  ```

  **Commit**: YES
  - Message: `feat(db): add SQLAlchemy models + Alembic migrations`
  - Files: `barricade/backend/app/models/`, `barricade/backend/app/db.py`, `barricade/backend/alembic/`

- [x] 5. SSH Key Encryption Module (AES-256-GCM)

  **What to do**:
  - Create `app/crypto/encryption.py`:
    - `encrypt_ssh_key(plaintext_key: str, master_key: bytes) -> bytes` — AES-256-GCM encryption
    - `decrypt_ssh_key(encrypted_data: bytes, master_key: bytes) -> str` — AES-256-GCM decryption
    - Use `cryptography` library (Fernet is AES-CBC; prefer AES-GCM directly for authenticated encryption)
    - Generate random 12-byte nonce per encryption (stored with ciphertext)
    - Format: `nonce (12 bytes) || ciphertext || tag (16 bytes)`
  - Create `app/crypto/key_management.py`:
    - `get_master_key() -> bytes` — reads `ENCRYPTION_KEY` from env var, base64-decodes it
    - `generate_master_key() -> str` — generates a new 32-byte key, returns base64-encoded for .env
  - Validate that key is passphraseless before encrypting (attempt to load with `paramiko` or `cryptography.hazmat`)
  - Add CLI command to generate a master key: `python -m app.crypto.key_management`

  **Must NOT do**:
  - Do NOT store master key in any file in the repo
  - Do NOT use Fernet (AES-CBC) — use AES-GCM directly
  - Do NOT support passphrased keys in v1

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Cryptography requires careful implementation; security-critical code
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 7)
  - **Blocks**: Task 9
  - **Blocked By**: Task 4

  **References**:
  - Python cryptography AES-GCM: https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM

  **Acceptance Criteria**:
  - [ ] Encrypt → decrypt roundtrip returns original key
  - [ ] Decryption with wrong master key raises error
  - [ ] Passphrased key upload rejected with clear error
  - [ ] `python -m app.crypto.key_management` outputs a valid base64 key

  **QA Scenarios**:
  ```
  Scenario: SSH key encryption roundtrip
    Tool: Bash
    Preconditions: cryptography library installed
    Steps:
      1. Run: cd barricade/backend && python -c "
         from app.crypto.encryption import encrypt_ssh_key, decrypt_ssh_key
         from app.crypto.key_management import generate_master_key
         import base64
         key = base64.b64decode(generate_master_key())
         original = 'ssh-rsa AAAAB3NzaC1yc2EAAA... test@host'
         encrypted = encrypt_ssh_key(original, key)
         decrypted = decrypt_ssh_key(encrypted, key)
         assert decrypted == original, f'Mismatch: {decrypted} != {original}'
         print('PASS: roundtrip successful')
         "
      2. Assert output contains "PASS: roundtrip successful"
    Expected Result: Encryption roundtrip preserves key content exactly
    Failure Indicators: Assertion error, import error, cryptography error
    Evidence: .sisyphus/evidence/task-5-crypto-roundtrip.txt

  Scenario: Wrong master key fails decryption
    Tool: Bash
    Preconditions: Same as above
    Steps:
      1. Run: cd barricade/backend && python -c "
         from app.crypto.encryption import encrypt_ssh_key, decrypt_ssh_key
         from app.crypto.key_management import generate_master_key
         import base64
         key1 = base64.b64decode(generate_master_key())
         key2 = base64.b64decode(generate_master_key())
         encrypted = encrypt_ssh_key('test-key-data', key1)
         try:
           decrypt_ssh_key(encrypted, key2)
           print('FAIL: should have raised error')
         except Exception as e:
           print(f'PASS: decryption failed as expected: {type(e).__name__}')
         "
      2. Assert output contains "PASS: decryption failed as expected"
    Expected Result: Wrong key raises cryptographic error
    Evidence: .sisyphus/evidence/task-5-wrong-key.txt
  ```

  **Commit**: YES
  - Message: `feat(crypto): add SSH key encryption module (AES-256-GCM)`
  - Files: `barricade/backend/app/crypto/`

- [x] 6. Auth System (fastapi-users + JWT)

  **What to do**:
  - Integrate `fastapi-users` with async SQLAlchemy backend:
    - User model adapter (extend existing User model from Task 4)
    - JWT strategy with configurable expiry (default 24h)
    - Auth router: `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me`
    - JWT tokens via httpOnly cookies (NOT localStorage — prevents XSS)
  - Configure CORS for frontend origin
  - Create initial superuser via env var or CLI command (`python -m app.auth.create_superuser`)
  - Password requirements: min 8 chars, at least 1 uppercase, 1 digit
  - Rate limit login endpoint: max 5 attempts per minute per IP

  **Must NOT do**:
  - Do NOT store JWT in localStorage
  - Do NOT return password hashes in any API response
  - Do NOT implement OAuth/social login in v1

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Auth is security-critical, needs careful fastapi-users integration
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 7)
  - **Blocks**: Tasks 7, 8, 9, 10
  - **Blocked By**: Task 4

  **References**:
  - fastapi-users docs: https://fastapi-users.github.io/fastapi-users/latest/
  - fastapi-users SQLAlchemy: https://fastapi-users.github.io/fastapi-users/latest/configuration/databases/sqlalchemy/

  **Acceptance Criteria**:
  - [ ] POST `/auth/register` creates user, returns 201
  - [ ] POST `/auth/login` returns JWT in httpOnly cookie
  - [ ] GET `/auth/me` with valid cookie returns user info
  - [ ] GET `/auth/me` without cookie returns 401
  - [ ] Password "short" rejected with validation error
  - [ ] 6th login attempt within 1 minute returns 429

  **QA Scenarios**:
  ```
  Scenario: Full auth flow (register → login → me → unauthorized)
    Tool: Bash (curl)
    Preconditions: Backend running with PostgreSQL
    Steps:
      1. Run: curl -s -w "%{http_code}" -X POST http://localhost:8000/auth/register \
           -H "Content-Type: application/json" \
           -d '{"email":"test@barricade.io","password":"SecurePass1"}'
      2. Assert: HTTP 201, response contains "email":"test@barricade.io"
      3. Run: curl -s -w "%{http_code}" -c cookies.txt -X POST http://localhost:8000/auth/login \
           -H "Content-Type: application/x-www-form-urlencoded" \
           -d "username=test@barricade.io&password=SecurePass1"
      4. Assert: HTTP 200 or 204, cookies.txt contains auth cookie
      5. Run: curl -s -w "%{http_code}" -b cookies.txt http://localhost:8000/auth/me
      6. Assert: HTTP 200, response contains "email":"test@barricade.io"
      7. Run: curl -s -w "%{http_code}" http://localhost:8000/auth/me
      8. Assert: HTTP 401
    Expected Result: Full auth cycle works, unauthorized access blocked
    Evidence: .sisyphus/evidence/task-6-auth-flow.txt
  ```

  **Commit**: YES
  - Message: `feat(auth): add JWT authentication with fastapi-users`
  - Files: `barricade/backend/app/auth/`

- [x] 7. RBAC Middleware (Per-Host-Group Permissions)

  **What to do**:
  - Create `app/auth/rbac.py` with FastAPI `Depends()` decorators:
    - `require_group_permission(group_id: int, min_role: Role)` — checks UserGroupPermission table
    - `require_admin()` — checks is_superuser flag
    - Roles: `viewer` (read-only), `editor` (CRUD rules, trigger sync), `admin` (manage hosts, users, SSH keys in group)
  - Permission check logic:
    - Superusers bypass all group permission checks
    - Users see ONLY groups they have permissions for (and hosts in those groups)
    - `viewer`: GET endpoints only for their groups
    - `editor`: GET + POST/PUT/DELETE on rules, trigger sync for their groups
    - `admin`: Full access to hosts, SSH keys, rules, sync within their groups
  - Create API endpoint: `POST /groups/{id}/permissions` — assign user to group with role (superuser only)
  - Create API endpoint: `GET /groups/{id}/permissions` — list users with access (admin+ for that group)
  - List endpoints should filter by user's accessible groups (no data leakage)

  **Must NOT do**:
  - Do NOT implement per-rule permissions
  - Do NOT implement per-host permissions (group-level only)
  - Do NOT create an abstract permission framework — simple if/elif on role

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Authorization logic is subtle — must handle edge cases (no permission = 403, cross-group data leakage)
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T6 completes)
  - **Parallel Group**: Wave 2 (sequential after T6)
  - **Blocks**: Tasks 8, 9, 13
  - **Blocked By**: Task 6

  **References**:
  - FastAPI dependencies: https://fastapi.tiangolo.com/tutorial/dependencies/

  **Acceptance Criteria**:
  - [ ] User with `viewer` role on group X gets 200 on GET, 403 on POST/PUT/DELETE
  - [ ] User with `editor` role on group X gets 200 on rule CRUD, 403 on host management
  - [ ] User with NO permission on group Y gets 403 on all endpoints for group Y
  - [ ] Superuser gets 200 on all endpoints regardless of group permissions
  - [ ] List endpoints return ONLY resources from user's permitted groups

  **QA Scenarios**:
  ```
  Scenario: RBAC enforces group-level access
    Tool: Bash (curl)
    Preconditions: Two users created, one with viewer role on group-1, one with no permissions
    Steps:
      1. Login as viewer-user, get cookie
      2. GET /api/groups/1/rules → Assert 200
      3. POST /api/groups/1/rules → Assert 403
      4. GET /api/groups/2/rules (no permission) → Assert 403
      5. Login as no-permission user
      6. GET /api/groups/1/rules → Assert 403
    Expected Result: viewer can read own group, cannot write; no-permission user blocked entirely
    Evidence: .sisyphus/evidence/task-7-rbac-enforcement.txt

  Scenario: Superuser bypasses all RBAC
    Tool: Bash (curl)
    Preconditions: Superuser account exists
    Steps:
      1. Login as superuser
      2. GET /api/groups/1/rules → Assert 200
      3. POST /api/groups/1/rules (with valid rule body) → Assert 201
      4. GET /api/groups/999/rules (non-existent) → Assert 404 (not 403)
    Expected Result: Superuser can access all groups
    Evidence: .sisyphus/evidence/task-7-superuser-bypass.txt
  ```

  **Commit**: YES
  - Message: `feat(rbac): add per-host-group permission middleware`
  - Files: `barricade/backend/app/auth/rbac.py`

- [x] 8. Host Group CRUD API

  **What to do**:
  - Create `app/api/groups.py` with REST endpoints:
    - `GET /api/groups` — list groups (filtered by user permissions)
    - `POST /api/groups` — create group (superuser only). Fields: name, description, priority
    - `GET /api/groups/{id}` — get group detail with host count and rule count
    - `PUT /api/groups/{id}` — update group (admin+ on group)
    - `DELETE /api/groups/{id}` — delete group (superuser only). Reject if group has hosts assigned.
  - Pydantic schemas for request/response (GroupCreate, GroupUpdate, GroupResponse)
  - Priority field: integer, higher = higher priority (used in rule merge). Must be unique across groups.
  - Include host count and rule count in response for dashboard display
  - Validate: group name unique, priority unique, cannot delete group with hosts

  **Must NOT do**:
  - Do NOT implement nested group hierarchies
  - Do NOT allow group deletion if hosts are still assigned

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Standard CRUD but with RBAC integration and validation rules
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 9, 10, 11)
  - **Blocks**: Tasks 9, 11, 12
  - **Blocked By**: Tasks 6, 7

  **Acceptance Criteria**:
  - [ ] Full CRUD works with proper RBAC enforcement
  - [ ] Duplicate group name returns 409
  - [ ] Duplicate priority returns 409
  - [ ] Delete group with hosts returns 400

  **QA Scenarios**:
  ```
  Scenario: Group CRUD lifecycle
    Tool: Bash (curl)
    Preconditions: Superuser logged in
    Steps:
      1. POST /api/groups {"name":"web-servers","description":"Web tier","priority":100} → Assert 201
      2. GET /api/groups → Assert response contains "web-servers"
      3. PUT /api/groups/{id} {"description":"Updated"} → Assert 200
      4. POST /api/groups {"name":"web-servers","priority":200} → Assert 409 (duplicate name)
      5. DELETE /api/groups/{id} → Assert 204
    Expected Result: Full CRUD with validation
    Evidence: .sisyphus/evidence/task-8-group-crud.txt
  ```

  **Commit**: YES
  - Message: `feat(api): add host group CRUD endpoints`
  - Files: `barricade/backend/app/api/groups.py`, `barricade/backend/app/schemas/groups.py`

- [x] 9. Host CRUD API + SSH Key Upload + Firewall Auto-Detection

  **What to do**:
  - Create `app/api/hosts.py` with REST endpoints:
    - `GET /api/hosts` — list hosts (filtered by user's permitted groups)
    - `POST /api/hosts` — add host. Fields: hostname, ip_address, ssh_port (default 22), group_ids (array of group IDs)
    - `GET /api/hosts/{id}` — host detail with group memberships, sync status, last drift check
    - `PUT /api/hosts/{id}` — update host
    - `DELETE /api/hosts/{id}` — remove host (admin+ on all associated groups)
    - `POST /api/hosts/{id}/detect-firewall` — trigger firewall auto-detection
  - Create `app/api/ssh_keys.py` with REST endpoints:
    - `GET /api/ssh-keys` — list keys (names + public keys only, NEVER private key)
    - `POST /api/ssh-keys` — upload SSH key (encrypts before storage). Fields: name, private_key, is_default
    - `DELETE /api/ssh-keys/{id}` — delete key (reject if hosts reference it)
    - `GET /api/ssh-keys/{id}/public` — download public key
  - Host-group association: M2M via HostGroupMembership table
  - SSH key: encrypted via Task 5's encryption module before DB storage
  - On host add: run `ssh-keyscan {ip}` to add to known_hosts file
  - Firewall auto-detection: run Ansible ad-hoc command to check which firewall service is active:
    ```
    systemctl is-active nftables firewalld ufw
    ```
    Parse output → set firewall_backend field

  **Must NOT do**:
  - Do NOT return encrypted/decrypted private key in any API response
  - Do NOT support SSH key passphrases
  - Do NOT allow host deletion without removing from all groups first (or cascade)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multiple endpoints, SSH key handling, Ansible integration for detection
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 10, 11)
  - **Blocks**: Tasks 11, 16, 19
  - **Blocked By**: Tasks 5, 7, 8

  **Acceptance Criteria**:
  - [ ] Host CRUD works with M2M group assignment
  - [ ] SSH key private key never appears in API responses
  - [ ] SSH key encrypted in DB (raw DB query shows ciphertext, not plaintext)
  - [ ] `POST /api/hosts/{id}/detect-firewall` returns detected backend
  - [ ] Host list filtered by user's permitted groups

  **QA Scenarios**:
  ```
  Scenario: Host creation with group assignment
    Tool: Bash (curl)
    Preconditions: Group "web-servers" exists with id=1, superuser logged in
    Steps:
      1. POST /api/ssh-keys {"name":"default","private_key":"<test-key>","is_default":true} → Assert 201
      2. POST /api/hosts {"hostname":"web01","ip_address":"10.0.1.1","ssh_port":22,"group_ids":[1]} → Assert 201
      3. GET /api/hosts/{id} → Assert response contains "hostname":"web01", groups contains "web-servers"
      4. GET /api/ssh-keys → Assert response contains "name":"default", NO "private_key" field
    Expected Result: Host created with group, key stored securely
    Evidence: .sisyphus/evidence/task-9-host-crud.txt

  Scenario: SSH key never exposed via API
    Tool: Bash (curl)
    Preconditions: SSH key uploaded
    Steps:
      1. GET /api/ssh-keys → Assert response does NOT contain "private_key" or "encrypted_private_key"
      2. GET /api/ssh-keys/{id} → Assert same
      3. GET /api/hosts/{id} → Assert SSH key reference shows id + name only
    Expected Result: Private key material never in API responses
    Evidence: .sisyphus/evidence/task-9-key-security.txt
  ```

  **Commit**: YES
  - Message: `feat(api): add host CRUD with SSH key upload + firewall auto-detect`
  - Files: `barricade/backend/app/api/hosts.py`, `barricade/backend/app/api/ssh_keys.py`, `barricade/backend/app/schemas/hosts.py`

- [x] 10. Frontend Auth Pages (Login, Register, Protected Routes)

  **What to do**:
  - Create `frontend/app/(auth)/login/page.tsx` — login form with email + password
  - Create `frontend/app/(auth)/register/page.tsx` — registration form
  - Create auth middleware (`frontend/middleware.ts`) — redirect to /login if no auth cookie
  - Create `frontend/lib/auth.ts` — auth context provider, current user state, logout function
  - API client (`frontend/lib/api.ts`) — fetch wrapper that includes cookies, handles 401 → redirect to login
  - Use shadcn/ui form components: Input, Button, Label, Card
  - Show validation errors from backend (email taken, weak password, etc.)
  - After login: redirect to `/dashboard`
  - Style: dark theme, centered card layout for auth pages, "Barricade" logo/title

  **Must NOT do**:
  - Do NOT store tokens in localStorage
  - Do NOT implement OAuth/social login
  - Do NOT build the dashboard page content yet (just redirect target)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: UI implementation with form components, auth flow, styling
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Auth pages should look polished and professional

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9, 11)
  - **Blocks**: Task 11
  - **Blocked By**: Tasks 2, 6

  **Acceptance Criteria**:
  - [ ] Login page renders with email/password form
  - [ ] Successful login redirects to /dashboard
  - [ ] Invalid credentials shows error message
  - [ ] Unauthenticated access to /dashboard redirects to /login
  - [ ] Register page creates account and redirects to login

  **QA Scenarios**:
  ```
  Scenario: Login flow end-to-end
    Tool: Playwright
    Preconditions: Backend running, user "test@barricade.io" registered
    Steps:
      1. Navigate to http://localhost:3000/login
      2. Assert: page contains input[name="email"] and input[name="password"]
      3. Fill input[name="email"] with "test@barricade.io"
      4. Fill input[name="password"] with "SecurePass1"
      5. Click button[type="submit"]
      6. Wait for navigation to /dashboard (timeout: 5s)
      7. Assert: URL is /dashboard
      8. Assert: page does NOT contain "Login" heading
    Expected Result: Login succeeds, redirects to dashboard
    Evidence: .sisyphus/evidence/task-10-login-flow.png

  Scenario: Unauthenticated redirect
    Tool: Playwright
    Preconditions: No auth cookies
    Steps:
      1. Navigate to http://localhost:3000/dashboard
      2. Wait for redirect (timeout: 5s)
      3. Assert: URL contains "/login"
    Expected Result: Unauthenticated users redirected to login
    Evidence: .sisyphus/evidence/task-10-unauth-redirect.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add auth pages (login, register, protected routes)`
  - Files: `barricade/frontend/app/(auth)/`, `barricade/frontend/middleware.ts`, `barricade/frontend/lib/auth.ts`

- [x] 11. Frontend Host & Group Management UI

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/page.tsx` — list groups in a table (name, priority, host count, rule count)
  - Create `frontend/app/(dashboard)/groups/[id]/page.tsx` — group detail with hosts list and rules list
  - Create `frontend/app/(dashboard)/groups/new/page.tsx` — create group form
  - Create `frontend/app/(dashboard)/hosts/page.tsx` — list all hosts (filterable by group)
  - Create `frontend/app/(dashboard)/hosts/[id]/page.tsx` — host detail (hostname, IP, firewall backend, sync status, groups)
  - Create `frontend/app/(dashboard)/hosts/new/page.tsx` — add host form with group selection (multi-select), SSH key selection
  - Create `frontend/app/(dashboard)/ssh-keys/page.tsx` — SSH key management (list, upload, delete)
  - Host status badges: `in_sync` (green), `out_of_sync` (amber), `unknown` (gray), `error` (red), `pending` (blue)
  - Use TanStack Query for data fetching with 10s refetch interval on lists
  - Group priority displayed as badge with number
  - Firewall backend icon/badge per host (nftables, firewalld, ufw, unknown)
  - Sidebar navigation: Dashboard, Groups, Hosts, SSH Keys, Audit Log

  **Must NOT do**:
  - Do NOT build rule management UI (Task 15)
  - Do NOT build sync/drift UI (Tasks 21, 25)
  - Do NOT add auto-discovery features

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Multiple UI pages with tables, forms, status indicators
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Polished infrastructure dashboard aesthetic

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T8, T9, T10)
  - **Parallel Group**: Wave 3 (after deps)
  - **Blocks**: Task 15
  - **Blocked By**: Tasks 2, 8, 9, 10

  **Acceptance Criteria**:
  - [ ] Groups page lists all groups with host/rule counts
  - [ ] Hosts page lists hosts with status badges and firewall backend indicators
  - [ ] Add host form allows multi-group selection and SSH key selection
  - [ ] SSH keys page allows upload and lists keys (no private key shown)
  - [ ] Sidebar navigation works across all pages

  **QA Scenarios**:
  ```
  Scenario: Group and host management workflow
    Tool: Playwright
    Preconditions: Logged in as superuser, backend running
    Steps:
      1. Navigate to /groups
      2. Click "New Group" button
      3. Fill name: "web-servers", description: "Web tier", priority: 100
      4. Click submit → Assert redirect to /groups, table contains "web-servers"
      5. Navigate to /hosts/new
      6. Fill hostname: "web01", ip: "10.0.1.1"
      7. Select group "web-servers" from multi-select
      8. Click submit → Assert redirect to /hosts, table contains "web01"
      9. Click on "web01" row → Assert host detail page shows group "web-servers"
    Expected Result: Full group + host creation workflow via UI
    Evidence: .sisyphus/evidence/task-11-host-group-workflow.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add host and group management pages`
  - Files: `barricade/frontend/app/(dashboard)/groups/`, `barricade/frontend/app/(dashboard)/hosts/`, `barricade/frontend/app/(dashboard)/ssh-keys/`

- [x] 12. Abstract Rule Model + Validation + Priority Merge Logic

  **What to do**:
  - Create `app/rules/model.py` — the canonical rule abstraction:
    ```python
    class FirewallRuleSpec:
        action: Literal["allow", "deny", "reject"]
        protocol: Literal["tcp", "udp", "icmp", "any"]
        direction: Literal["input", "output"]
        source_cidr: Optional[str]       # IPv4 or IPv6 CIDR, e.g. "10.0.0.0/8" or "::1/128"
        destination_cidr: Optional[str]
        port_start: Optional[int]         # Single port or range start (1-65535)
        port_end: Optional[int]           # Range end (null = single port)
        comment: Optional[str]
        is_system: bool = False           # True for auto-injected SSH lockout rule
    ```
  - Create `app/rules/validation.py`:
    - Validate CIDR notation (IPv4 and IPv6) using `ipaddress` stdlib
    - Validate port range (1-65535, port_end >= port_start if both set)
    - Validate ICMP rules have no port (protocol=icmp → port fields must be null)
    - Warn (not block) if rule allows 0.0.0.0/0 on all ports (effectively disables firewall)
    - Detect duplicate rules within same group (same action+protocol+direction+ports+cidrs)
  - Create `app/rules/merge.py` — priority-based merge for hosts in multiple groups:
    - Collect rules from all groups the host belongs to
    - Sort by group priority (higher priority first)
    - On conflict (same port+protocol+direction but different action): higher-priority group wins
    - Auto-inject SSH lockout prevention rule at the top (always allow SSH from Barricade server IP, is_system=True)
    - Return final merged ruleset as ordered list
  - The SSH lockout rule must:
    - Be auto-injected in every merged ruleset
    - Use the Barricade server's IP (from config/env var `BARRICADE_SERVER_IP`)
    - Be marked `is_system=True`
    - Not be deletable/editable via UI or API

  **Must NOT do**:
  - Do NOT implement NAT/forwarding rules
  - Do NOT implement FORWARD chain direction
  - Do NOT create abstract factory pattern — keep merge logic as plain functions

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Complex merge logic with conflict resolution, IPv4+IPv6 validation, edge cases
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 13, 14, 15)
  - **Blocks**: Tasks 13, 14
  - **Blocked By**: Task 8

  **Acceptance Criteria**:
  - [ ] Valid IPv4 and IPv6 CIDR accepted, invalid rejected
  - [ ] Port ranges validated (1-65535, end >= start)
  - [ ] ICMP rules with port fields are rejected
  - [ ] Duplicate rule detection works
  - [ ] Priority merge: higher priority group wins on conflict
  - [ ] SSH lockout rule always present in merged output, cannot be removed
  - [ ] 0.0.0.0/0 all-ports rule triggers warning (not error)

  **QA Scenarios**:
  ```
  Scenario: Priority-based rule merge with conflict
    Tool: Bash
    Preconditions: merge module imported
    Steps:
      1. Run: python -c "
         from app.rules.merge import merge_group_rules
         group_high = {'id': 1, 'priority': 200, 'rules': [
           {'action': 'deny', 'protocol': 'tcp', 'direction': 'input', 'port_start': 80}
         ]}
         group_low = {'id': 2, 'priority': 100, 'rules': [
           {'action': 'allow', 'protocol': 'tcp', 'direction': 'input', 'port_start': 80}
         ]}
         merged = merge_group_rules([group_high, group_low], server_ip='10.0.0.1')
         port80 = [r for r in merged if r.get('port_start') == 80]
         assert len(port80) == 1, f'Expected 1 port-80 rule, got {len(port80)}'
         assert port80[0]['action'] == 'deny', f'Expected deny (high priority), got {port80[0][\"action\"]}'
         ssh = [r for r in merged if r.get('is_system')]
         assert len(ssh) >= 1, 'Missing SSH lockout rule'
         print('PASS: merge conflict resolved by priority, SSH rule present')
         "
      2. Assert output contains "PASS"
    Expected Result: Higher priority group's deny overrides lower priority's allow
    Evidence: .sisyphus/evidence/task-12-merge-conflict.txt

  Scenario: IPv6 CIDR validation
    Tool: Bash
    Steps:
      1. Validate "::1/128" → accepted
      2. Validate "2001:db8::/32" → accepted
      3. Validate "not-an-ip" → rejected
      4. Validate "10.0.0.0/33" → rejected (invalid prefix length)
    Expected Result: Both IPv4 and IPv6 validated correctly
    Evidence: .sisyphus/evidence/task-12-ipv6-validation.txt
  ```

  **Commit**: YES
  - Message: `feat(rules): add abstract rule model + validation + priority merge`
  - Files: `barricade/backend/app/rules/model.py`, `barricade/backend/app/rules/validation.py`, `barricade/backend/app/rules/merge.py`

- [x] 13. Rule CRUD API + SSH Lockout Prevention

  **What to do**:
  - Create `app/api/rules.py` with REST endpoints:
    - `GET /api/groups/{group_id}/rules` — list rules for group (ordered by priority)
    - `POST /api/groups/{group_id}/rules` — create rule (editor+ on group). Validate via Task 12's validation.
    - `PUT /api/groups/{group_id}/rules/{rule_id}` — update rule (editor+)
    - `DELETE /api/groups/{group_id}/rules/{rule_id}` — delete rule (editor+)
    - `PUT /api/groups/{group_id}/rules/reorder` — batch update rule priorities
    - `GET /api/hosts/{host_id}/effective-rules` — get merged ruleset for a host (all groups, priority-merged, includes SSH lockout rule)
  - SSH lockout prevention enforcement:
    - `is_system=True` rules cannot be updated or deleted via API → return 403 with clear message
    - Effective rules endpoint always includes the auto-injected SSH rule
  - Pydantic schemas with full validation (from Task 12)
  - Return 400 with specific errors for invalid rules (bad CIDR, port out of range, etc.)
  - Audit log entry on every create/update/delete (prepare hook for Task 24)

  **Must NOT do**:
  - Do NOT allow creation of `is_system=True` rules via API (only auto-injected)
  - Do NOT allow delete of system rules
  - Do NOT implement rule templates

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: CRUD with RBAC, validation, lockout prevention enforcement
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 12, 14, 15)
  - **Blocks**: Tasks 15, 16
  - **Blocked By**: Tasks 7, 12

  **Acceptance Criteria**:
  - [ ] Rule CRUD works with RBAC (viewer: read, editor: write, admin: write)
  - [ ] Invalid rules rejected with 400 and descriptive errors
  - [ ] System rules (is_system=True) cannot be modified/deleted (403)
  - [ ] Effective rules endpoint returns merged rules with SSH lockout rule
  - [ ] Rule reorder endpoint batch-updates priorities

  **QA Scenarios**:
  ```
  Scenario: Cannot delete SSH lockout system rule
    Tool: Bash (curl)
    Preconditions: Host exists, effective rules include system SSH rule
    Steps:
      1. GET /api/hosts/{id}/effective-rules → find rule with is_system=true, note rule_id
      2. DELETE /api/groups/{gid}/rules/{system_rule_id} → Assert 403
      3. PUT /api/groups/{gid}/rules/{system_rule_id} {"action":"deny"} → Assert 403
    Expected Result: System rules immutable via API
    Evidence: .sisyphus/evidence/task-13-lockout-prevention.txt
  ```

  **Commit**: YES
  - Message: `feat(api): add rule CRUD endpoints + SSH lockout prevention`
  - Files: `barricade/backend/app/api/rules.py`, `barricade/backend/app/schemas/rules.py`

- [x] 14. Backend-Specific Rule Renderers (nftables, firewalld, ufw)

  **What to do**:
  - Create `app/rules/renderers/` with one module per backend:
  - `app/rules/renderers/nftables.py` — renders merged ruleset to `/etc/nftables.conf` template:
    ```nft
    #!/usr/sbin/nft -f
    flush ruleset
    table inet filter {
      chain input {
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        iif lo accept
        # Auto-injected SSH lockout prevention
        ip saddr 10.0.0.1 tcp dport 22 accept comment "barricade-system-ssh"
        # User-defined rules (from merged ruleset)
        ip saddr 192.168.1.0/24 tcp dport 80 accept comment "allow-http-from-lan"
        ...
      }
      chain output {
        type filter hook output priority 0; policy accept;
        ...
      }
    }
    ```
  - `app/rules/renderers/firewalld.py` — renders to list of `ansible.posix.firewalld` task dicts:
    - Map rules to firewalld module parameters (port, rich_rule, source, zone)
    - Use rich rules for source/dest CIDR-based rules
    - Always set `permanent: true, immediate: true`
  - `app/rules/renderers/ufw.py` — renders to `/etc/ufw/user.rules` file content:
    - Generate iptables-format rules that UFW uses internally
    - Do NOT use `ufw reset` — write the rules file directly + `ufw reload`
    - Handle IPv6 rules in separate `/etc/ufw/user6.rules`
  - Each renderer takes a `List[FirewallRuleSpec]` (merged output from Task 12) and returns backend-specific output
  - Include `ct state established,related accept` and `iif lo accept` in nftables template automatically
  - Use `inet` family (not `ip`) for nftables to handle IPv4+IPv6 transparently

  **Must NOT do**:
  - Do NOT create abstract base class / factory pattern — simple module-level functions
  - Do NOT render NAT/forwarding rules
  - Do NOT use `ufw reset` anywhere

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Three different firewall syntaxes, each with unique edge cases. Security-critical output.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 12, 13, 15)
  - **Blocks**: Tasks 16, 18, 22
  - **Blocked By**: Task 12

  **Acceptance Criteria**:
  - [ ] nftables renderer produces valid nft config (passes `nft -c -f` on a test system)
  - [ ] firewalld renderer produces valid task dicts with correct rich_rule syntax
  - [ ] ufw renderer produces valid user.rules format
  - [ ] All renderers include SSH lockout rule when is_system rules are present
  - [ ] IPv6 CIDRs render correctly in all backends
  - [ ] nftables uses `inet` family (not `ip`)
  - [ ] nftables includes `ct state established,related accept` and `iif lo accept`

  **QA Scenarios**:
  ```
  Scenario: nftables renderer produces valid config
    Tool: Bash
    Steps:
      1. Generate nftables config from test rules (allow tcp/80, deny tcp/3306, system SSH)
      2. Write to temp file
      3. Run: nft -c -f /tmp/test-nftables.conf (check mode, no apply)
      4. Assert exit code 0
      5. Assert file contains "flush ruleset"
      6. Assert file contains 'comment "barricade-system-ssh"'
      7. Assert file contains "ct state established,related accept"
    Expected Result: Generated nftables config is syntactically valid
    Evidence: .sisyphus/evidence/task-14-nftables-render.txt

  Scenario: firewalld renderer produces correct rich rules
    Tool: Bash
    Steps:
      1. Generate firewalld tasks from rule: allow tcp/443 from 10.0.0.0/8
      2. Assert task dict contains rich_rule matching: rule family="ipv4" source address="10.0.0.0/8" port port="443" protocol="tcp" accept
      3. Assert permanent: true and immediate: true present
    Expected Result: Rich rule syntax correct for firewalld module
    Evidence: .sisyphus/evidence/task-14-firewalld-render.txt
  ```

  **Commit**: YES
  - Message: `feat(rules): add nftables, firewalld, ufw rule renderers`
  - Files: `barricade/backend/app/rules/renderers/`

- [x] 15. Frontend Rule Management UI

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/rules/page.tsx` — rule list for a group:
    - Table with columns: Priority, Action (color-coded badge), Protocol, Direction, Source, Destination, Port(s), Comment
    - Action badges: allow (green), deny (red), reject (amber)
    - System rules visually distinct (locked icon, cannot be edited/deleted)
    - Drag-and-drop reordering (update priority on drop)
    - "Add Rule" button opens dialog
    - Edit/delete buttons per row (disabled for system rules)
  - Rule create/edit dialog (shadcn Dialog):
    - Form fields: action (select), protocol (select), direction (select), source CIDR (input with validation), dest CIDR (input), port/port-range (input), comment (textarea)
    - Inline validation: CIDR format, port range, ICMP has no port
    - Warning banner when source is 0.0.0.0/0 with all ports
  - Create `frontend/app/(dashboard)/hosts/[id]/effective-rules/page.tsx`:
    - Read-only merged ruleset view
    - Shows which group each rule came from (color-coded by group)
    - System rules highlighted

  **Must NOT do**:
  - Do NOT implement rule templates or presets
  - Do NOT allow editing system rules in UI

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Complex UI with drag-and-drop, color coding, inline validation, dialogs
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Rule editor needs clear, intuitive design for firewall concepts

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T11, T13)
  - **Parallel Group**: Wave 4 (after deps)
  - **Blocks**: Task 21
  - **Blocked By**: Tasks 11, 13

  **Acceptance Criteria**:
  - [ ] Rules displayed in priority-ordered table with color-coded badges
  - [ ] Create rule dialog validates input (bad CIDR, port out of range)
  - [ ] System rules cannot be edited or deleted (buttons disabled)
  - [ ] Drag-and-drop reorder updates priority via API
  - [ ] Effective rules page shows merged ruleset with group origin

  **QA Scenarios**:
  ```
  Scenario: Create rule via dialog
    Tool: Playwright
    Preconditions: Logged in, group exists
    Steps:
      1. Navigate to /groups/{id}/rules
      2. Click "Add Rule" button
      3. Select action: "allow"
      4. Select protocol: "tcp"
      5. Select direction: "input"
      6. Fill source CIDR: "10.0.0.0/8"
      7. Fill port: "443"
      8. Fill comment: "HTTPS from internal"
      9. Click "Save"
      10. Assert: dialog closes, table contains row with port 443, action "allow"
    Expected Result: Rule created and visible in table
    Evidence: .sisyphus/evidence/task-15-create-rule.png

  Scenario: System rule not editable
    Tool: Playwright
    Steps:
      1. Navigate to /groups/{id}/rules
      2. Find row with system badge / locked icon
      3. Assert: edit button is disabled or absent
      4. Assert: delete button is disabled or absent
    Expected Result: System rules are read-only in UI
    Evidence: .sisyphus/evidence/task-15-system-rule-locked.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add rule management pages with drag-and-drop reorder`
  - Files: `barricade/frontend/app/(dashboard)/groups/[id]/rules/`

- [x] 16. Ansible Playbook Generator (DB Rules → Playbook YAML per Backend)

  **What to do**:
  - Create `app/ansible/generator.py`:
    - `generate_playbook(host: Host, rules: List[FirewallRuleSpec]) -> str` — returns YAML string
    - Dispatches to backend-specific generation based on `host.firewall_backend`
    - **nftables**: Template task — `ansible.builtin.template` to write `/etc/nftables.conf` from rendered template (Task 14), validate with `nft -c -f %s`, then `ansible.builtin.service: nftables state=reloaded`
    - **firewalld**: Per-rule tasks — sequence of `ansible.posix.firewalld` tasks from renderer output. Always `permanent: true, immediate: true`. Add `firewalld_info` task at start to capture before-state.
    - **ufw**: File tasks — `ansible.builtin.copy` to write `/etc/ufw/user.rules` and `/etc/ufw/user6.rules`, then `ansible.builtin.command: ufw reload`. Capture before-state by slurping existing files first.
  - Create `app/ansible/inventory.py`:
    - Generate dynamic Ansible inventory JSON from Host model (hostname, ip, ssh_port, ssh_key path)
    - SSH key written to temp file in `/dev/shm/` (tmpfs) for the duration of the job, cleaned in finally block
  - Create `app/ansible/templates/` directory with Jinja2 templates for nftables.conf
  - All generated playbooks must:
    - Use `become: true` (firewall commands need root)
    - Set `gather_facts: false` (we already know what we need)
    - Include `ansible_ssh_private_key_file` pointing to temp key location
    - Include `ansible_host`, `ansible_port` variables
  - Generated playbooks validated with `ansible-playbook --syntax-check`

  **Must NOT do**:
  - Do NOT generate iptables playbooks
  - Do NOT write SSH keys to persistent disk — `/dev/shm/` only
  - Do NOT pass key material through function arguments that might be logged

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Ansible playbook generation is the core of the system. Must produce valid, safe playbooks for 3 backends.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Tasks 17, 18, 19)
  - **Blocks**: Tasks 17, 18, 20
  - **Blocked By**: Tasks 9, 13, 14

  **Acceptance Criteria**:
  - [ ] nftables playbook passes `ansible-playbook --syntax-check`
  - [ ] firewalld playbook passes syntax check
  - [ ] ufw playbook passes syntax check
  - [ ] SSH key written to `/dev/shm/`, not `/tmp/` or persistent disk
  - [ ] Generated inventory includes correct host vars
  - [ ] All playbooks use `become: true`

  **QA Scenarios**:
  ```
  Scenario: Generated nftables playbook is syntactically valid
    Tool: Bash
    Steps:
      1. Generate playbook for a test host with nftables backend and 3 rules
      2. Write to /tmp/test-playbook.yml
      3. Run: ansible-playbook --syntax-check /tmp/test-playbook.yml
      4. Assert exit code 0
      5. Assert playbook YAML contains "become: true"
      6. Assert playbook references nftables.conf template
    Expected Result: Valid Ansible playbook generated
    Evidence: .sisyphus/evidence/task-16-playbook-syntax.txt

  Scenario: SSH key isolation in tmpfs
    Tool: Bash
    Steps:
      1. Call inventory generator for a test host
      2. Assert: ssh_key path starts with "/dev/shm/"
      3. Assert: file exists at that path during generation
      4. After cleanup: assert file no longer exists
    Expected Result: SSH key exists only in tmpfs, cleaned after use
    Evidence: .sisyphus/evidence/task-16-ssh-key-tmpfs.txt
  ```

  **Commit**: YES
  - Message: `feat(ansible): add playbook generator for nftables/firewalld/ufw`
  - Files: `barricade/backend/app/ansible/`

- [x] 17. Celery Task Infrastructure + ansible-runner Wrapper

  **What to do**:
  - Create `app/tasks/__init__.py` — Celery app configuration:
    - Broker: Redis (from REDIS_URL env)
    - Result backend: Redis
    - Queues: `default` (fast tasks), `long_running` (Ansible playbooks)
    - `task_always_eager = False` (True only in test config)
    - `worker_max_tasks_per_child = 100` (prevent memory leaks)
  - Create `app/tasks/ansible_runner_wrapper.py`:
    - `run_playbook(host_id: int, playbook_yaml: str, job_id: int)` — Celery task on `long_running` queue
    - Inside task:
      1. Fetch host + SSH key from DB (decrypt key inside task, NOT passed as arg)
      2. Create `tempfile.mkdtemp()` for `private_data_dir`
      3. Write SSH key to `/dev/shm/barricade-{job_id}.key`, chmod 600
      4. Write playbook YAML to `private_data_dir/project/playbook.yml`
      5. Write inventory to `private_data_dir/inventory/hosts`
      6. Call `ansible_runner.run()` with:
         - `event_handler`: updates SyncJob status in DB per event
         - `timeout`: configurable (default 300s)
         - `cancel_callback`: checks if job was cancelled in DB
      7. On completion: update SyncJob with status, stdout, stats
      8. `finally` block: remove SSH key from `/dev/shm/`, remove `private_data_dir`
  - Create `app/tasks/models.py` — SyncJob status enum and state machine:
    - Valid transitions: pending→running, running→success, running→failed, running→cancelled, pending→cancelled
  - Celery beat schedule for drift detection (placeholder, configured in Task 23)

  **Must NOT do**:
  - Do NOT pass SSH key material as Celery task arguments (they're logged to Redis)
  - Do NOT use `run_async()` inside Celery (Celery already provides async via worker pool)
  - Do NOT skip the finally block for cleanup

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Celery + ansible-runner integration with security constraints
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Tasks 16, 18, 19)
  - **Blocks**: Tasks 20, 23
  - **Blocked By**: Task 16

  **Acceptance Criteria**:
  - [ ] Celery worker starts and connects to Redis
  - [ ] Task runs ansible-runner with correct private_data_dir isolation
  - [ ] SSH key cleaned from `/dev/shm/` after task completion (success or failure)
  - [ ] SyncJob status updated in DB during and after execution
  - [ ] Task respects timeout setting
  - [ ] Task args do NOT contain SSH key material (verify in Redis)

  **QA Scenarios**:
  ```
  Scenario: Celery task cleanup on failure
    Tool: Bash
    Preconditions: Celery worker running, Redis available
    Steps:
      1. Submit a task with intentionally invalid playbook (will fail)
      2. Wait for task completion (poll SyncJob status, max 30s)
      3. Assert: SyncJob status is "failed"
      4. Assert: no files in /dev/shm/ matching barricade-*
      5. Assert: private_data_dir temp directory removed
    Expected Result: Resources cleaned up even on failure
    Evidence: .sisyphus/evidence/task-17-cleanup-on-failure.txt
  ```

  **Commit**: YES
  - Message: `feat(tasks): add Celery infrastructure + ansible-runner wrapper`
  - Files: `barricade/backend/app/tasks/`

- [x] 18. Plan/Diff Engine (Current Host State vs DB Desired State)

  **What to do**:
  - Create `app/sync/diff.py`:
    - `fetch_current_state(host: Host) -> List[FirewallRuleSpec]` — runs Ansible ad-hoc to read current firewall state:
      - **nftables**: `nft -j list ruleset` → parse JSON → convert to FirewallRuleSpec list
      - **firewalld**: `ansible.posix.firewalld_info` → parse zones/ports/rich_rules → convert
      - **ufw**: slurp `/etc/ufw/user.rules` → parse iptables format → convert
    - `compute_diff(current: List[FirewallRuleSpec], desired: List[FirewallRuleSpec]) -> RulesetDiff`:
      ```python
      class RulesetDiff:
          rules_to_add: List[FirewallRuleSpec]    # in desired, not in current
          rules_to_remove: List[FirewallRuleSpec]  # in current, not in desired
          rules_unchanged: List[FirewallRuleSpec]   # in both
          has_changes: bool
      ```
    - Rule matching: compare by action+protocol+direction+ports+cidrs (ignore comments, priority)
  - Create `app/api/sync.py`:
    - `POST /api/hosts/{host_id}/plan` — fetch current state, compute diff, return RulesetDiff (does NOT apply)
    - `POST /api/groups/{group_id}/plan` — fetch state for ALL hosts in group, return per-host diffs
  - This implements the "terraform plan" pattern: show what WILL change before applying

  **Must NOT do**:
  - Do NOT apply changes in the plan endpoint — plan is read-only preview
  - Do NOT cache current state permanently — always fetch fresh on plan

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Parsing 3 different firewall output formats + diff logic is complex
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Tasks 16, 17, 19)
  - **Blocks**: Tasks 20, 21
  - **Blocked By**: Tasks 14, 16

  **Acceptance Criteria**:
  - [ ] nftables JSON output parsed correctly into FirewallRuleSpec list
  - [ ] firewalld info parsed correctly
  - [ ] ufw user.rules parsed correctly
  - [ ] Diff correctly identifies additions, removals, unchanged rules
  - [ ] Plan endpoint returns diff without applying changes
  - [ ] Group plan returns per-host diffs

  **QA Scenarios**:
  ```
  Scenario: Diff detects rule additions and removals
    Tool: Bash
    Steps:
      1. Create mock current state: [allow tcp/22, allow tcp/80]
      2. Create desired state: [allow tcp/22, allow tcp/443]
      3. Compute diff
      4. Assert: rules_to_add contains tcp/443
      5. Assert: rules_to_remove contains tcp/80
      6. Assert: rules_unchanged contains tcp/22
      7. Assert: has_changes is True
    Expected Result: Diff correctly categorizes rules
    Evidence: .sisyphus/evidence/task-18-diff-logic.txt
  ```

  **Commit**: YES
  - Message: `feat(sync): add plan/diff engine for current vs desired state`
  - Files: `barricade/backend/app/sync/diff.py`, `barricade/backend/app/api/sync.py`

- [x] 19. Host Initial State Import Flow

  **What to do**:
  - Create `app/api/hosts.py` endpoint addition:
    - `GET /api/hosts/{host_id}/current-rules` — fetch current firewall rules from host (uses Task 18's fetch_current_state)
    - `POST /api/hosts/{host_id}/import-rules` — import selected rules into a specified group:
      - Request body: `{ "group_id": int, "rule_indices": [0, 2, 5] }` — which fetched rules to import
      - Creates FirewallRule records in DB for selected rules
  - Frontend flow (add to host detail page):
    - "Import Current Rules" button on host detail page
    - Shows current rules from host in a checkbox list
    - User selects which rules to import
    - User selects target group
    - Click "Import" → creates rules in selected group
  - This is used when adding an existing host to bring its current config into Barricade's management

  **Must NOT do**:
  - Do NOT auto-import all rules without user selection
  - Do NOT modify firewall on host during import (read-only operation)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Backend endpoint + frontend component, moderate complexity
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Import UI needs clear checkbox selection pattern

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Tasks 16, 17, 18)
  - **Blocks**: Task 21
  - **Blocked By**: Tasks 9, 14

  **Acceptance Criteria**:
  - [ ] Current rules endpoint returns parsed rules from host
  - [ ] Import endpoint creates FirewallRule records for selected rules
  - [ ] Only selected rules imported (not all)
  - [ ] Import does NOT modify firewall on host
  - [ ] Frontend shows rules with checkboxes and group selection

  **QA Scenarios**:
  ```
  Scenario: Selective rule import
    Tool: Bash (curl)
    Steps:
      1. GET /api/hosts/{id}/current-rules → Assert returns list of rules
      2. POST /api/hosts/{id}/import-rules {"group_id":1, "rule_indices":[0,2]}
      3. Assert 201
      4. GET /api/groups/1/rules → Assert 2 new rules created matching imported ones
    Expected Result: Only selected rules imported into group
    Evidence: .sisyphus/evidence/task-19-selective-import.txt
  ```

  **Commit**: YES
  - Message: `feat(hosts): add initial state import flow`
  - Files: `barricade/backend/app/api/hosts.py` (additions), `barricade/frontend/app/(dashboard)/hosts/[id]/import/`

- [x] 20. Sync Execution Flow (Trigger → Celery → ansible-runner → Status Update)

  **What to do**:
  - Create `app/api/sync.py` endpoints (additions):
    - `POST /api/hosts/{host_id}/sync` — trigger sync for single host (editor+ on host's groups):
      1. Check advisory lock — reject if sync already running for this host (409)
      2. Get merged rules for host (from Task 12)
      3. Render rules for host's backend (from Task 14)
      4. Generate playbook (from Task 16)
      5. Create SyncJob record (status=pending)
      6. Dispatch Celery task (from Task 17) with host_id and job_id
      7. Return SyncJob with id for polling
    - `POST /api/groups/{group_id}/sync` — trigger sync for ALL hosts in group:
      - Creates one SyncJob per host
      - Dispatches parallel Celery tasks
      - Returns list of SyncJob IDs
    - `GET /api/jobs/{job_id}` — get sync job status + output (for polling)
    - `GET /api/jobs` — list recent jobs (filterable by host, group, status)
  - Advisory lock implementation: use PostgreSQL `pg_advisory_xact_lock(host_id)` or a `sync_lock` column on Host
  - Empty group sync rejection: if group has zero rules, return 400 with "Cannot sync empty ruleset — this would remove all firewall rules and potentially lock you out"

  **Must NOT do**:
  - Do NOT allow concurrent syncs on the same host
  - Do NOT allow syncing groups with zero rules
  - Do NOT use WebSocket — polling via `GET /api/jobs/{id}` only

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Orchestration logic tying together multiple components
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with Tasks 21, 22, 23)
  - **Blocks**: Tasks 21, 22, 27
  - **Blocked By**: Tasks 3, 17, 18

  **Acceptance Criteria**:
  - [ ] Single host sync creates SyncJob and dispatches Celery task
  - [ ] Group sync dispatches parallel tasks for all hosts
  - [ ] Concurrent sync on same host returns 409
  - [ ] Empty group sync returns 400 with clear message
  - [ ] Job status endpoint returns current status + ansible output
  - [ ] Job list endpoint supports filtering

  **QA Scenarios**:
  ```
  Scenario: Concurrent sync rejection
    Tool: Bash (curl)
    Steps:
      1. POST /api/hosts/{id}/sync → Assert 200/201, note job_id
      2. Immediately POST /api/hosts/{id}/sync again → Assert 409
      3. Wait for first job to complete (poll GET /api/jobs/{job_id})
      4. POST /api/hosts/{id}/sync → Assert 200/201 (allowed after previous completes)
    Expected Result: Second concurrent sync rejected, third after completion allowed
    Evidence: .sisyphus/evidence/task-20-concurrent-rejection.txt

  Scenario: Empty group sync rejected
    Tool: Bash (curl)
    Steps:
      1. Create group with zero rules
      2. Add host to group
      3. POST /api/groups/{id}/sync → Assert 400
      4. Assert response body contains "empty" or "lock"
    Expected Result: Empty ruleset sync prevented
    Evidence: .sisyphus/evidence/task-20-empty-group-rejection.txt
  ```

  **Commit**: YES
  - Message: `feat(sync): add full sync execution flow with concurrency control`
  - Files: `barricade/backend/app/api/sync.py` (additions), `barricade/backend/app/tasks/sync.py`

- [x] 21. Frontend Sync UI (Plan Diff View, Apply Button, Status Polling)

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/sync/page.tsx` — group sync page:
    - "Preview Changes" button → calls group plan endpoint → displays per-host diffs
    - Diff display: side-by-side or unified diff view
      - Rules to add: green highlight with "+" prefix
      - Rules to remove: red highlight with "-" prefix
      - Unchanged rules: dim/gray
    - Per-host accordion: expand to see that host's diff
    - "Apply Changes" button (disabled until preview loaded)
    - Confirmation dialog before apply: "This will modify firewall rules on N hosts. Continue?"
  - Create sync status component:
    - After apply: show SyncJob statuses for each host
    - Poll `GET /api/jobs/{id}` every 3 seconds
    - Status indicators: pending (spinner), running (animated), success (green check), failed (red X)
    - Show Ansible output in collapsible panel per host
    - Stop polling when all jobs reach terminal state (success/failed/cancelled)
  - Add "Sync" button to host detail page for single-host sync (with same preview flow)

  **Must NOT do**:
  - Do NOT use WebSocket — polling only
  - Do NOT auto-apply without user confirmation

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Complex UI with diff visualization, polling status, animations
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Diff visualization and status indicators need careful design

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with Tasks 20, 22, 23)
  - **Blocks**: Task 25
  - **Blocked By**: Tasks 15, 18, 19, 20

  **Acceptance Criteria**:
  - [ ] Preview shows per-host rule diffs with add/remove/unchanged
  - [ ] Apply requires confirmation dialog
  - [ ] Job status polls every 3s and shows current state
  - [ ] Polling stops when all jobs reach terminal state
  - [ ] Ansible output viewable per host
  - [ ] Failed jobs show error details

  **QA Scenarios**:
  ```
  Scenario: Sync preview and apply flow
    Tool: Playwright
    Preconditions: Group with rules, host assigned, logged in as editor
    Steps:
      1. Navigate to /groups/{id}/sync
      2. Click "Preview Changes"
      3. Wait for diff to load (timeout: 15s)
      4. Assert: page contains elements with class indicating additions (green)
      5. Click "Apply Changes"
      6. Assert: confirmation dialog appears
      7. Click "Confirm"
      8. Assert: status indicators appear (spinner/animated)
      9. Wait for completion (timeout: 60s, poll every 3s)
      10. Assert: status shows success (green check) or failure (red X)
    Expected Result: Full preview → confirm → apply → status tracking flow
    Evidence: .sisyphus/evidence/task-21-sync-flow.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add sync UI with plan diff view and status polling`
  - Files: `barricade/frontend/app/(dashboard)/groups/[id]/sync/`

- [x] 22. Drift Detection Engine (Per-Backend State Collection + Comparison)

  **What to do**:
  - Create `app/drift/detector.py`:
    - `check_drift(host: Host) -> DriftResult`:
      ```python
      class DriftResult:
          host_id: int
          status: Literal["in_sync", "out_of_sync", "error", "unknown"]
          diff: Optional[RulesetDiff]   # reuse diff from Task 18
          checked_at: datetime
          error_message: Optional[str]
      ```
    - Uses Task 18's `fetch_current_state()` to get actual rules
    - Uses Task 12's merge to get desired rules
    - Uses Task 18's `compute_diff()` to compare
    - Updates Host model: `sync_status`, `last_drift_check_at`
  - Create `app/drift/collector.py` — per-backend state collection:
    - Reuses the parsing logic from Task 18's `fetch_current_state()`
    - Handles connection failures gracefully (set status to "error", log error, don't crash)
    - Set `timeout=30` for ansible-runner collection tasks
  - Create `app/api/drift.py`:
    - `POST /api/hosts/{host_id}/check-drift` — manual drift check (editor+ on host's groups)
    - `GET /api/hosts/{host_id}/drift-status` — get latest drift result
    - `POST /api/groups/{group_id}/check-drift` — check all hosts in group

  **Must NOT do**:
  - Do NOT modify firewall during drift check (read-only)
  - Do NOT crash on unreachable hosts — set status to "error"

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Error handling for unreachable hosts, state parsing reuse, status management
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with Tasks 20, 21, 23)
  - **Blocks**: Tasks 23, 25
  - **Blocked By**: Tasks 14, 20

  **Acceptance Criteria**:
  - [ ] Drift check returns in_sync when rules match
  - [ ] Drift check returns out_of_sync with diff when rules differ
  - [ ] Unreachable host returns error status (not crash)
  - [ ] Host model updated with sync_status and last_drift_check_at
  - [ ] Group-level drift check works for all hosts

  **QA Scenarios**:
  ```
  Scenario: Drift detection identifies out-of-sync host
    Tool: Bash
    Steps:
      1. Sync host to known good state
      2. Manually modify rules on host (add unexpected rule)
      3. POST /api/hosts/{id}/check-drift
      4. Assert: response status is "out_of_sync"
      5. Assert: diff contains the unexpected rule in rules_to_remove
    Expected Result: Drift correctly detected
    Evidence: .sisyphus/evidence/task-22-drift-detected.txt

  Scenario: Unreachable host handled gracefully
    Tool: Bash
    Steps:
      1. POST /api/hosts/{unreachable_host_id}/check-drift
      2. Assert: response status is "error"
      3. Assert: error_message is not null
      4. Assert: no unhandled exception in backend logs
    Expected Result: Error status set without crash
    Evidence: .sisyphus/evidence/task-22-unreachable-host.txt
  ```

  **Commit**: YES
  - Message: `feat(drift): add drift detection engine with per-backend state collection`
  - Files: `barricade/backend/app/drift/`

- [x] 23. Drift Detection Scheduling (Celery Beat + Manual Trigger)

  **What to do**:
  - Create `app/tasks/drift.py`:
    - `check_host_drift` — Celery task (long_running queue) that runs drift check for a single host
    - `check_all_drift` — Celery task that dispatches `check_host_drift` for all hosts with periodic checks enabled
  - Configure Celery beat schedule:
    - Use `redbeat.RedBeatScheduler` to prevent duplicate schedules
    - Default schedule: every 30 minutes (configurable via env var `DRIFT_CHECK_INTERVAL_MINUTES`)
    - Only check hosts that have `drift_check_enabled=True` (add this boolean to Host model)
  - Add to Host model: `drift_check_enabled` boolean (default False)
  - Add API endpoint: `PUT /api/hosts/{id}/drift-settings` — enable/disable periodic drift check
  - Add toggle in host detail UI: "Enable periodic drift checks" switch

  **Must NOT do**:
  - Do NOT use default Celery beat scheduler (use redbeat to prevent duplicates)
  - Do NOT check all hosts regardless of setting — respect drift_check_enabled

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Celery beat configuration, redbeat integration, settings API
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with Tasks 20, 21, 22)
  - **Blocks**: Task 25
  - **Blocked By**: Tasks 17, 22

  **Acceptance Criteria**:
  - [ ] Celery beat runs drift checks on configured interval
  - [ ] Only hosts with drift_check_enabled=True are checked
  - [ ] Redbeat prevents duplicate schedules
  - [ ] Manual API trigger works independently of schedule
  - [ ] Drift check interval configurable via env var

  **QA Scenarios**:
  ```
  Scenario: Periodic drift check runs on schedule
    Tool: Bash
    Steps:
      1. Set DRIFT_CHECK_INTERVAL_MINUTES=1 (for testing)
      2. Enable drift check on a host
      3. Start Celery beat
      4. Wait 90 seconds
      5. Check host's last_drift_check_at → Assert it was updated within last 90s
    Expected Result: Beat scheduled task executes on interval
    Evidence: .sisyphus/evidence/task-23-periodic-drift.txt
  ```

  **Commit**: YES
  - Message: `feat(drift): add Celery beat scheduling with redbeat`
  - Files: `barricade/backend/app/tasks/drift.py`

- [x] 24. Audit Logging System (Middleware + Model Hooks, Append-Only)

  **What to do**:
  - Create `app/audit/logger.py`:
    - `log_action(user_id, action, entity_type, entity_id, before_state, after_state, ip_address)` — creates AuditLog record
    - Actions tracked: `rule.create`, `rule.update`, `rule.delete`, `host.create`, `host.update`, `host.delete`, `group.create`, `group.update`, `group.delete`, `ssh_key.create`, `ssh_key.delete`, `sync.trigger`, `sync.complete`, `sync.failed`, `drift.check`, `user.login`, `user.register`, `permission.grant`, `permission.revoke`
    - `before_state` / `after_state`: JSONB snapshots of the entity before/after change
    - For deletes: `after_state` is null
    - For creates: `before_state` is null
  - Create FastAPI middleware to capture IP address from request
  - Integrate audit logging into all CRUD endpoints (Tasks 8, 9, 13):
    - On create: capture after_state
    - On update: capture before_state and after_state
    - On delete: capture before_state
  - Create `app/api/audit.py`:
    - `GET /api/audit-log` — list audit entries (superuser + admin). Filterable by: entity_type, entity_id, user_id, action, date range
    - Pagination: cursor-based (not offset) for efficiency on large tables
    - NEVER allow DELETE or PUT on audit entries (append-only)
  - **AuditLog table constraints**: no UPDATE trigger, no CASCADE DELETE from parent entities

  **Must NOT do**:
  - Do NOT allow deletion of audit log entries via any API
  - Do NOT allow updates to audit log entries
  - Do NOT log SSH key private key content in before/after state (log key id + name only)
  - Do NOT add excessive logging — one entry per user action, not per SQL query

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Middleware integration, JSONB state capture, security constraints
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 25, 26, 27)
  - **Blocks**: Tasks 26, 27
  - **Blocked By**: Tasks 6, 13, 20

  **Acceptance Criteria**:
  - [ ] Every CRUD action creates an audit log entry
  - [ ] before_state/after_state correctly captured as JSONB
  - [ ] No DELETE or PUT endpoint exists for audit log
  - [ ] SSH key private content never in audit log
  - [ ] Audit API supports filtering by entity_type, user, date range
  - [ ] Cursor-based pagination works

  **QA Scenarios**:
  ```
  Scenario: Rule change creates audit entry
    Tool: Bash (curl)
    Steps:
      1. Create rule via POST /api/groups/{id}/rules → note rule_id
      2. GET /api/audit-log?entity_type=rule&entity_id={rule_id}
      3. Assert: entry exists with action="rule.create", before_state=null, after_state contains rule data
      4. Update rule via PUT
      5. GET /api/audit-log?entity_type=rule&entity_id={rule_id}
      6. Assert: entry exists with action="rule.update", both before_state and after_state present
    Expected Result: Full audit trail for rule lifecycle
    Evidence: .sisyphus/evidence/task-24-audit-trail.txt

  Scenario: Audit log is immutable
    Tool: Bash (curl)
    Steps:
      1. GET /api/audit-log → note first entry id
      2. DELETE /api/audit-log/{id} → Assert 405 (Method Not Allowed) or 404
      3. PUT /api/audit-log/{id} → Assert 405 or 404
    Expected Result: No mutation endpoints exist for audit log
    Evidence: .sisyphus/evidence/task-24-audit-immutable.txt
  ```

  **Commit**: YES
  - Message: `feat(audit): add full audit logging system (append-only)`
  - Files: `barricade/backend/app/audit/`, `barricade/backend/app/api/audit.py`

- [x] 25. Frontend Drift Status Dashboard

  **What to do**:
  - Create `frontend/app/(dashboard)/page.tsx` — main dashboard page:
    - Summary cards at top: total hosts, in-sync count, out-of-sync count, error count, unknown count
    - Host status table: hostname, group(s), firewall backend, sync status (color badge), last drift check time, last sync time
    - Status badges: in_sync (green), out_of_sync (amber), error (red), unknown (gray), pending (blue)
    - Click on out-of-sync host → navigates to host detail showing drift diff
    - "Check All" button → triggers drift check for all enabled hosts
  - Create drift detail component (used in host detail page):
    - Shows side-by-side: desired rules vs actual rules
    - Highlights differences (additions in green, removals in red)
    - "Sync Now" button to resolve drift
  - Auto-refresh: TanStack Query refetch every 10s on dashboard

  **Must NOT do**:
  - Do NOT add charts/graphs (keep it simple — table + badges)
  - Do NOT add email/Slack notification links

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Dashboard with status indicators, summary cards, data tables
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Dashboard should look clean and professional like Grafana/Semaphore

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 24, 26, 27)
  - **Blocks**: Task 28
  - **Blocked By**: Tasks 21, 22, 23

  **Acceptance Criteria**:
  - [ ] Dashboard shows summary cards with correct counts
  - [ ] Host table shows sync status with color-coded badges
  - [ ] Out-of-sync hosts link to drift diff view
  - [ ] "Check All" button triggers drift checks
  - [ ] Data auto-refreshes every 10s

  **QA Scenarios**:
  ```
  Scenario: Dashboard displays correct sync status
    Tool: Playwright
    Preconditions: Multiple hosts with different sync statuses
    Steps:
      1. Navigate to /dashboard
      2. Assert: summary cards show counts matching backend data
      3. Assert: table rows have appropriate color badges
      4. Click on out-of-sync host row
      5. Assert: navigates to host detail with drift diff visible
    Expected Result: Dashboard accurately reflects host fleet status
    Evidence: .sisyphus/evidence/task-25-dashboard.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add drift status dashboard`
  - Files: `barricade/frontend/app/(dashboard)/page.tsx`

- [x] 26. Frontend Audit Log Viewer

  **What to do**:
  - Create `frontend/app/(dashboard)/audit/page.tsx` — audit log viewer:
    - Table: timestamp, user, action, entity type, entity (link), IP address
    - Expandable row detail: shows before/after state as JSON diff
    - Filters: action type dropdown, entity type dropdown, user dropdown, date range picker
    - Cursor-based pagination ("Load more" button, not page numbers)
    - Action badges color-coded: create (green), update (blue), delete (red), sync (purple), auth (gray)
  - Use shadcn components: Table, Badge, Select, DatePicker (or Calendar), Button, Collapsible

  **Must NOT do**:
  - Do NOT add delete/edit buttons for audit entries (read-only view)
  - Do NOT add export functionality in v1

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Data table with filters, expandable rows, JSON diff display
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Clean audit log viewer with good readability

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 24, 25, 27)
  - **Blocks**: Task 28
  - **Blocked By**: Task 24

  **Acceptance Criteria**:
  - [ ] Audit entries displayed in descending time order
  - [ ] Filters work: action type, entity type, user, date range
  - [ ] Expandable rows show before/after JSON diff
  - [ ] Cursor-based pagination ("Load more")
  - [ ] No edit/delete controls present

  **QA Scenarios**:
  ```
  Scenario: Audit log displays and filters
    Tool: Playwright
    Preconditions: Multiple audit entries exist from previous operations
    Steps:
      1. Navigate to /audit
      2. Assert: table has rows with timestamps, users, actions
      3. Select filter: action type = "rule.create"
      4. Assert: only rule.create entries visible
      5. Click on a row to expand
      6. Assert: before/after state JSON is visible
    Expected Result: Audit log viewable with working filters
    Evidence: .sisyphus/evidence/task-26-audit-viewer.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add audit log viewer with filters`
  - Files: `barricade/frontend/app/(dashboard)/audit/`

- [x] 27. Backend Test Suite (pytest for All Endpoints + Logic)

  **What to do**:
  - Create `barricade/backend/tests/conftest.py`:
    - Async test fixtures using `pytest-asyncio`
    - Test database: PostgreSQL (same as production, use `testcontainers` or separate test DB)
    - Nested transactions with savepoints for test isolation (each test rolls back)
    - `httpx.AsyncClient` with `ASGITransport` for API testing
    - Fixture: authenticated client (superuser), authenticated client (viewer), unauthenticated client
    - Celery fixtures: `task_always_eager=True`, `task_eager_propagates=True`
  - Test modules:
    - `tests/test_auth.py` — register, login, me, unauthorized, weak password
    - `tests/test_rbac.py` — viewer/editor/admin access per endpoint, cross-group rejection
    - `tests/test_groups.py` — CRUD, validation (duplicate name/priority), delete with hosts
    - `tests/test_hosts.py` — CRUD, SSH key upload encryption, group assignment, firewall detection
    - `tests/test_rules.py` — CRUD, validation (CIDR, ports, ICMP), system rule protection, reorder
    - `tests/test_crypto.py` — encrypt/decrypt roundtrip, wrong key, key generation
    - `tests/test_renderers.py` — nftables/firewalld/ufw rendering correctness
    - `tests/test_merge.py` — priority merge, conflict resolution, SSH lockout injection
    - `tests/test_diff.py` — diff computation (additions, removals, unchanged)
    - `tests/test_sync.py` — sync trigger, concurrent rejection, empty group rejection
    - `tests/test_audit.py` — audit entries created, immutability, filtering
    - `tests/test_drift.py` — drift detection, unreachable host handling
  - Minimum: 3 test cases per module covering happy path + error path + edge case

  **Must NOT do**:
  - Do NOT use SQLite for tests — must match production PostgreSQL
  - Do NOT mock the database — use real PostgreSQL with transaction rollback
  - Do NOT skip auth/RBAC tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Comprehensive test suite covering all backend functionality
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 24, 25, 26)
  - **Blocks**: Task 29
  - **Blocked By**: Tasks 20, 24

  **Acceptance Criteria**:
  - [ ] `pytest -v` passes all tests
  - [ ] All API endpoints have at least 1 happy path + 1 error path test
  - [ ] RBAC tested: correct access + denied access per role
  - [ ] Crypto roundtrip tested
  - [ ] Renderers produce valid output format
  - [ ] Test isolation: tests can run in any order

  **QA Scenarios**:
  ```
  Scenario: Full test suite passes
    Tool: Bash
    Steps:
      1. Run: cd barricade/backend && pytest -v --tb=short
      2. Assert: exit code 0
      3. Assert: output shows 0 failures
      4. Assert: test count >= 36 (3 per 12 modules)
    Expected Result: All backend tests pass
    Evidence: .sisyphus/evidence/task-27-pytest-results.txt
  ```

  **Commit**: YES
  - Message: `test(backend): add comprehensive pytest suite`
  - Files: `barricade/backend/tests/`

- [ ] 28. Frontend Playwright E2E Tests

  **What to do**:
  - Create `barricade/frontend/e2e/` with Playwright test files:
    - `e2e/auth.spec.ts` — register, login, logout, protected route redirect
    - `e2e/groups.spec.ts` — create group, edit, delete, list
    - `e2e/hosts.spec.ts` — add host, assign to group, view detail, SSH key upload
    - `e2e/rules.spec.ts` — create rule, edit, delete, reorder, system rule protection
    - `e2e/sync.spec.ts` — preview changes, confirm apply, poll status
    - `e2e/audit.spec.ts` — view audit log, filter, expand detail
    - `e2e/dashboard.spec.ts` — summary cards, status badges, navigation
  - Configure Playwright: headless Chromium, base URL from env, screenshot on failure
  - Each test: login first (use helper), perform actions, assert results
  - Take screenshots at key steps for evidence

  **Must NOT do**:
  - Do NOT use fragile selectors (prefer data-testid or role-based selectors)
  - Do NOT skip auth in E2E tests (test the full flow)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: E2E test coverage across all frontend pages
  - **Skills**: [`playwright`]
    - `playwright`: Required for browser automation testing

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 8 (with Task 29)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 25, 26

  **Acceptance Criteria**:
  - [ ] `npx playwright test` passes all tests
  - [ ] All major workflows covered (auth, CRUD, sync, drift, audit)
  - [ ] Screenshots captured on test failure
  - [ ] Tests use stable selectors (data-testid)

  **QA Scenarios**:
  ```
  Scenario: Playwright suite passes
    Tool: Bash
    Steps:
      1. Run: cd barricade/frontend && npx playwright test --reporter=list
      2. Assert: exit code 0
      3. Assert: output shows 0 failures
      4. Assert: test count >= 7 (one per spec file)
    Expected Result: All E2E tests pass
    Evidence: .sisyphus/evidence/task-28-playwright-results.txt
  ```

  **Commit**: YES
  - Message: `test(frontend): add Playwright E2E test suite`
  - Files: `barricade/frontend/e2e/`

- [ ] 29. End-to-End Integration Test (Full Workflow Validation)

  **What to do**:
  - Create `barricade/tests/integration/test_full_workflow.py`:
    - Bring up full Docker Compose stack
    - Run complete workflow via API + assertions:
      1. Register user → login → get token
      2. Create host group "web-servers" with priority 100
      3. Upload SSH key
      4. Add host "test-host" with IP, assign to group
      5. Detect firewall backend on host
      6. Create 3 rules on group (allow SSH, allow HTTP, deny all)
      7. Get effective rules → verify SSH lockout rule present
      8. Preview changes (plan) → verify diff shows additions
      9. Trigger sync → poll until complete → verify success
      10. Check drift → verify in_sync
      11. Check audit log → verify entries for all actions
    - This validates the ENTIRE pipeline end-to-end
  - Must work against a real (or containerized) target host — can use a Docker container with SSH as the target

  **Must NOT do**:
  - Do NOT skip any step in the workflow
  - Do NOT mock Ansible execution — run real ansible-runner

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Complex integration test requiring full system orchestration
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 8 (with Task 28)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 27, 28

  **Acceptance Criteria**:
  - [ ] Full workflow completes without errors
  - [ ] All API assertions pass
  - [ ] Sync actually modifies firewall on target host
  - [ ] Drift check confirms in_sync after sync
  - [ ] Audit log contains complete trail of all actions

  **QA Scenarios**:
  ```
  Scenario: Complete workflow end-to-end
    Tool: Bash
    Steps:
      1. docker-compose up -d
      2. Wait for all services healthy
      3. Run: pytest tests/integration/test_full_workflow.py -v
      4. Assert: exit code 0
      5. Assert: all workflow steps pass
    Expected Result: Full pipeline works from registration to drift check
    Evidence: .sisyphus/evidence/task-29-e2e-integration.txt
  ```

  **Commit**: YES
  - Message: `test(e2e): add full workflow integration test`
  - Files: `barricade/tests/integration/`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run linter + `pytest`. Review all changed files for: `as any`/type ignores, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names (data/result/item/temp). Verify no secrets in code (grep for key patterns).
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill)
  Start from clean state (docker-compose down -v && docker-compose up). Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (login → create group → add host → add rules → plan → sync → drift check). Test edge cases: empty state, invalid input, unauthorized access. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual code. Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT Have" compliance: no iptables, no NAT, no notifications, no abstract factories, no GraphQL. Flag unaccounted features.
  Output: `Tasks [N/N compliant] | Forbidden [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Phase | Commit Message | Key Files |
|-------|---------------|-----------|
| T1 | `feat(backend): scaffold FastAPI project structure` | backend/ |
| T2 | `feat(frontend): scaffold Next.js + shadcn/ui project` | frontend/ |
| T3 | `feat(infra): add Docker Compose full stack` | docker-compose.yml |
| T4 | `feat(db): add SQLAlchemy models + Alembic migrations` | backend/models/, alembic/ |
| T5 | `feat(crypto): add SSH key encryption module` | backend/crypto/ |
| T6 | `feat(auth): add JWT auth with fastapi-users` | backend/auth/ |
| T7 | `feat(rbac): add per-host-group permission middleware` | backend/auth/rbac.py |
| T8 | `feat(api): add host group CRUD endpoints` | backend/api/groups.py |
| T9 | `feat(api): add host CRUD with SSH key + auto-detect` | backend/api/hosts.py |
| T10 | `feat(ui): add auth pages (login, register)` | frontend/app/auth/ |
| T11 | `feat(ui): add host and group management pages` | frontend/app/hosts/, frontend/app/groups/ |
| T12 | `feat(rules): add abstract rule model + validation + merge` | backend/rules/model.py |
| T13 | `feat(api): add rule CRUD + SSH lockout prevention` | backend/api/rules.py |
| T14 | `feat(rules): add backend-specific rule renderers` | backend/rules/renderers/ |
| T15 | `feat(ui): add rule management pages` | frontend/app/rules/ |
| T16 | `feat(ansible): add playbook generator from DB rules` | backend/ansible/generator.py |
| T17 | `feat(tasks): add Celery infrastructure + ansible-runner wrapper` | backend/tasks/ |
| T18 | `feat(sync): add plan/diff engine` | backend/sync/diff.py |
| T19 | `feat(hosts): add initial state import flow` | backend/api/hosts.py, frontend/app/hosts/ |
| T20 | `feat(sync): add full sync execution flow` | backend/tasks/sync.py |
| T21 | `feat(ui): add sync UI with plan diff + status polling` | frontend/app/sync/ |
| T22 | `feat(drift): add drift detection engine` | backend/drift/ |
| T23 | `feat(drift): add Celery beat scheduling + manual trigger` | backend/tasks/drift.py |
| T24 | `feat(audit): add full audit logging system` | backend/audit/ |
| T25 | `feat(ui): add drift status dashboard` | frontend/app/drift/ |
| T26 | `feat(ui): add audit log viewer` | frontend/app/audit/ |
| T27 | `test(backend): add pytest suite for all endpoints` | backend/tests/ |
| T28 | `test(frontend): add Playwright E2E tests` | frontend/e2e/ |
| T29 | `test(e2e): add full integration test workflow` | tests/integration/ |

---

## Success Criteria

### Verification Commands
```bash
# Backend tests
cd barricade/backend && pytest -v  # Expected: all pass

# Frontend build
cd barricade/frontend && npm run build  # Expected: no errors

# Docker stack
docker-compose up -d  # Expected: all containers healthy

# API health
curl http://localhost:8000/health  # Expected: {"status": "ok"}

# Auth flow
curl -X POST http://localhost:8000/auth/register -d '{"email":"test@test.com","password":"Test1234!"}' # Expected: 201
curl -X POST http://localhost:8000/auth/login -d '{"email":"test@test.com","password":"Test1234!"}' # Expected: 200 + token

# Playwright
cd barricade/frontend && npx playwright test  # Expected: all pass
```

### Final Checklist
- [ ] All "Must Have" items present and functional
- [ ] All "Must NOT Have" items absent from codebase
- [ ] All pytest tests pass
- [ ] All Playwright tests pass
- [ ] Docker Compose deploys cleanly
- [ ] Full workflow works: register → login → create group → add host → add rules → preview diff → sync → drift check → audit log
