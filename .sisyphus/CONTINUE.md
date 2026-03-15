# Barricade — Continuation Guide

## Status: 27/29 Implementation Tasks Complete

**Last session**: 2026-03-14
**Git commits**: 27 (from `f753972` initial to `2b6b21a` test suite)
**Branch**: `main`

## What's Done (Waves 1-7)

All core features are implemented and committed:

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | T1-T4 | Backend scaffold, Frontend scaffold, Docker Compose, DB models (9 tables + Alembic) |
| 2 | T5-T7 | AES-256-GCM SSH key encryption, JWT auth (fastapi-users 15.x, httpOnly cookies), RBAC middleware |
| 3 | T8-T11 | Group/Host/SSH key CRUD APIs, Frontend auth pages + host/group management UI |
| 4 | T12-T15 | Rule model + validation + priority merge, Rule CRUD API, 3 firewall renderers (nftables/firewalld/ufw), Rule management UI |
| 5 | T16-T19 | Ansible playbook generator, Celery + ansible-runner wrapper, Plan/diff engine, Import flow |
| 6 | T20-T23 | Sync execution (concurrency control), Sync/Drift/Audit frontend pages, Drift detection + Celery beat scheduling |
| 7 | T24-T27 | Audit logging (append-only), Drift dashboard, Audit viewer, **36 pytest tests (all pass)** |

### Backend: 23 API Routes
```
/health
/auth/jwt/login, /auth/jwt/logout, /auth/register
/users/me, /users/{id}
/api/groups (CRUD), /api/groups/{id}/permissions, /api/groups/{id}/rules (CRUD + reorder)
/api/hosts (CRUD), /api/hosts/{id}/detect-firewall, /api/hosts/{id}/effective-rules
/api/hosts/{id}/current-rules, /api/hosts/{id}/import-rules
/api/ssh-keys (CRUD)
/api/sync/hosts/{id}/plan, /api/sync/groups/{id}/plan
/api/sync/hosts/{id}/sync, /api/sync/groups/{id}/sync
/api/sync/jobs, /api/sync/jobs/{id}
/api/drift/hosts/{id}/check, /api/drift/groups/{id}/check, /api/drift/hosts/{id}/settings
/api/audit-log
```

### Frontend: 13+ Pages
```
/login, /register
/dashboard (drift status overview)
/groups, /groups/new, /groups/[id], /groups/[id]/rules, /groups/[id]/sync
/hosts, /hosts/new, /hosts/[id]
/ssh-keys
/audit
```

### Tests: 36/36 passing
```bash
cd backend && source venv/bin/activate && pytest tests/ -v  # 36 passed in 0.22s
```

## What Remains (3 Tasks)

### T28: Frontend Playwright E2E Tests
- **Requires**: Running full stack (Docker Compose)
- **What**: Create `frontend/e2e/` with Playwright specs for auth, groups, hosts, rules, sync, audit
- **Plan details**: See `.sisyphus/plans/barricade.md` Task 28

### T29: End-to-End Integration Test
- **Requires**: Full Docker Compose stack + SSH-accessible target host
- **What**: Create `tests/integration/test_full_workflow.py` — register, create group, add host, add rules, preview, sync, drift check, audit log
- **Plan details**: See `.sisyphus/plans/barricade.md` Task 29

### F1-F4: Final Verification Wave (4 parallel review agents)
- **F1**: Plan compliance audit — verify all Must Have/Must NOT Have
- **F2**: Code quality review — linting, type checking, test pass
- **F3**: Real QA via Playwright — full workflow end-to-end
- **F4**: Scope fidelity check — no creep, all spec implemented

## How to Continue

### Option A: Resume with `/start-work`
```
/start-work barricade
```
The plan file at `.sisyphus/plans/barricade.md` has checkboxes showing progress. The orchestrator will pick up from T28.

### Option B: Manual execution

**1. Start the stack:**
```bash
cd barricade
cp .env.example .env
# Edit .env with real values (generate ENCRYPTION_KEY with: cd backend && python -m app.crypto.key_management)
docker compose up -d
docker compose exec backend alembic upgrade head
```

**2. Run T28 — Playwright tests:**
```bash
cd frontend
npm install @playwright/test
npx playwright install chromium
# Create e2e/ test files per plan spec
npx playwright test
```

**3. Run T29 — Integration test:**
```bash
cd backend
source venv/bin/activate
pytest tests/integration/test_full_workflow.py -v
```

## Architecture Quick Reference

```
barricade/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app factory + route registration
│   │   ├── config.py        # pydantic-settings (env vars)
│   │   ├── db.py            # Async SQLAlchemy engine + session
│   │   ├── auth/            # fastapi-users, RBAC, superuser CLI
│   │   ├── api/             # REST endpoints (groups, hosts, ssh_keys, rules, sync, drift, audit, permissions)
│   │   ├── models/          # SQLAlchemy models (9 tables)
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── rules/           # Rule model, validation, merge, renderers (nftables/firewalld/ufw)
│   │   ├── ansible/         # Playbook generator + inventory
│   │   ├── tasks/           # Celery app + sync/drift tasks
│   │   ├── sync/            # Diff engine
│   │   ├── drift/           # Drift detection
│   │   ├── crypto/          # AES-256-GCM encryption
│   │   └── audit/           # Audit logging
│   ├── tests/               # 36 pytest tests
│   ├── alembic/             # DB migrations
│   └── pyproject.toml
├── frontend/
│   ├── app/                 # Next.js 15 App Router pages
│   ├── components/          # shadcn/ui + custom (sidebar, status-badge, rule-dialog)
│   ├── lib/                 # API client, types, auth context, utils
│   └── middleware.ts        # Cookie-based route protection
├── docker-compose.yml       # 7 services (postgres, redis, backend, celery-worker, celery-beat, frontend, migrate)
└── .env.example
```

## Key Design Decisions
- **Source of truth**: DB rules → Ansible pushes to hosts (never edit on host)
- **Auth**: JWT in httpOnly cookies (not localStorage) via fastapi-users 15.x
- **RBAC**: Per-host-group roles (viewer/editor/admin), superuser bypasses all
- **SSH keys**: AES-256-GCM encrypted in DB, decrypted only inside Celery tasks, written to /dev/shm/ (tmpfs)
- **Rule merge**: Priority-based (higher group priority wins on conflict)
- **SSH lockout prevention**: Auto-injected non-deletable allow rule for Barricade server SSH
- **Sync safety**: Concurrent sync rejection (409), empty group sync rejection (400), plan-before-apply diff
- **Drift detection**: Periodic (Celery beat) + manual, per-host enable/disable
- **Audit**: Append-only JSONB log, cursor-based pagination, no delete/update endpoints
- **Polling**: 3-5s for job status, 10s for dashboard (WebSocket deferred to v2)
