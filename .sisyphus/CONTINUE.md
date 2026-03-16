# Barricade — Project Status

## What Is Barricade

Centralized Linux firewall management via Ansible. Define firewall rules in a web UI, preview changes before applying, and sync to hosts running nftables, firewalld, or ufw. Extended with GitOps mode (Git-as-truth) and network host discovery.

---

## What's Built (Complete)

### Core Platform (v1) — 29/29 tasks

| Area | What | Key Files |
|------|------|-----------|
| **Backend scaffold** | FastAPI + SQLAlchemy async + Alembic migrations | `backend/app/main.py`, `backend/alembic/` |
| **Frontend scaffold** | Next.js 16 + shadcn/ui + TanStack Query + dark theme | `frontend/app/`, `frontend/components/` |
| **Infrastructure** | Docker Compose (7 services: postgres, redis, backend, frontend, celery worker/beat, migrate) | `docker-compose.yml` |
| **Database** | 9 SQLAlchemy models + Alembic migrations | `backend/app/models/` |
| **Auth** | JWT via fastapi-users, httpOnly cookie (`barricade_auth`), register/login/logout | `backend/app/auth/users.py` |
| **RBAC** | Per-host-group roles (admin/editor/viewer), superuser bypass | `backend/app/auth/rbac.py` |
| **SSH key encryption** | AES-256-GCM, master key from env, passphrase rejection | `backend/app/crypto/` |
| **Host management** | CRUD + SSH key upload + firewall auto-detect + group assignment | `backend/app/api/hosts.py` |
| **Group management** | CRUD with priority ordering | `backend/app/api/groups.py` |
| **Rule engine** | CRUD, validation (CIDR/port/protocol/ICMP), priority merge, SSH lockout prevention | `backend/app/rules/`, `backend/app/api/rules.py` |
| **Renderers** | nftables (template), firewalld (rich rules), ufw (iptables-save) | `backend/app/rules/renderers/` |
| **Ansible integration** | Playbook generator + inventory builder + ansible-runner wrapper | `backend/app/ansible/`, `backend/app/tasks/sync.py` |
| **Plan/diff engine** | Compare current vs desired rules, preview before apply | `backend/app/sync/diff.py` |
| **State collection** | SSH into hosts via asyncssh, run backend-specific commands, parse output | `backend/app/sync/collector.py`, `backend/app/sync/parsers/` |
| **Sync execution** | Celery task, concurrent rejection (409), empty-group guard | `backend/app/tasks/sync.py`, `backend/app/api/sync.py` |
| **Drift detection** | Periodic (RedBeat) + manual trigger, per-host enable/disable | `backend/app/drift/`, `backend/app/tasks/drift.py` |
| **Audit logging** | Append-only, before/after state, cursor pagination | `backend/app/audit/`, `backend/app/api/audit.py` |
| **Frontend pages** | Auth, Dashboard, Groups, Hosts, SSH Keys, Rules, Sync, Drift, Audit | `frontend/app/(dashboard)/`, `frontend/app/(auth)/` |

### GitOps Extension (backend only)

| Area | What | Key Files |
|------|------|-----------|
| **GitRepository model** | CRUD + Alembic migration + HostGroup gitops fields | `backend/app/api/git_repos.py` |
| **Git service** | Clone/pull with SSH + HTTPS token auth | `backend/app/gitops/git_service.py` |
| **YAML serializer** | Multi-module format (extensible), firewall module | `backend/app/gitops/serializer.py` |
| **Webhooks** | GitHub, GitLab, Gitea with HMAC/token verification | `backend/app/api/webhooks.py` |
| **Import/reconcile** | YAML → validate → diff → update DB | `backend/app/gitops/importer.py` |
| **Auto-sync pipeline** | Webhook → import → audit log → sync to hosts | `backend/app/gitops/pipeline.py` |
| **Rule lockdown** | 403 on rule mutation for GitOps groups + UI indicators | `backend/app/api/rules.py` |

### Host Discovery (backend only)

| Area | What | Key Files |
|------|------|-----------|
| **Network scanner** | Async TCP port 22 scan on CIDR ranges | `backend/app/discovery/scanner.py` |
| **Discovery API** | POST scan, GET status/results, POST bulk-add | `backend/app/api/discovery.py` |

### Test Suite

| Suite | Count | What |
|-------|-------|------|
| **Unit tests** | 54 | Crypto, diff, renderers, rules, parsers (nftables/firewalld/ufw) |
| **API tests** | ~60 | Auth, RBAC, groups, hosts, sync, audit, drift, merge, gitops (need Docker) |
| **Playwright E2E** | 55+ | Auth, groups, hosts, rules, sync, audit, dashboard (need running stack) |
| **Integration** | 1 | Full workflow test with testcontainers (14 steps: register → audit) |

---

## What's Planned (Future)

### 1. GitOps Frontend UI — `gitops-frontend.md`
**Status**: Plan exists, backend ready, NO frontend pages built.

Missing:
- `/git-repos` page — CRUD for git repositories
- GitOps settings section on group detail page (enable/disable, repo + file path selection, webhook URL)
- GitOps status column on groups list
- Sidebar "Git Repos" navigation item
- TypeScript interfaces for GitRepository

### 2. Host Discovery Frontend — `host-discovery.md`
**Status**: Plan exists, backend ready, NO frontend page built.

Missing:
- `/discovery` page — Scan form (CIDR input), live results table, SSH key selector, group assignment, bulk add

### 3. Platform Extensions — `barricade-extensions.md`
**Status**: Design document only, nothing built. Roadmap for extending Barricade beyond firewalls.

Proposed modules:
- **Service management** — Manage systemd services (start/stop/enable/disable)
- **User management** — Manage Linux users/groups/SSH authorized_keys
- **Certificate management** — TLS certificate deployment + renewal
- **DNS resolver** — Manage /etc/resolv.conf and /etc/hosts
- **Package management** — Ensure packages installed/removed/pinned

Each module follows the same "Barricade pattern": DB model → Ansible renderer → drift detector → sync engine → audit log → React UI.

---

## Environment

| Component | Technology | Port |
|-----------|-----------|------|
| Frontend | Next.js 16 + shadcn/ui + TanStack Query | 3000 |
| Backend | FastAPI + SQLAlchemy (async) + asyncpg | 8000 |
| Database | PostgreSQL 16 | 5432 |
| Task queue | Celery + Redis + RedBeat scheduler | — |
| Config mgmt | Ansible (ansible-runner) | — |
| State collection | asyncssh | — |

## Quick Start

```bash
cp .env.example .env
# Generate ENCRYPTION_KEY + SECRET_KEY (see README.md)
docker compose up -d
# Frontend: http://localhost:3000
# API: http://localhost:8000
```

## Running Tests

```bash
# Unit tests (no Docker needed)
cd backend && pytest tests/test_crypto.py tests/test_diff.py tests/test_renderers.py tests/test_rules.py tests/test_parsers.py -v

# All tests (needs Docker for testcontainers)
cd backend && pytest tests/ --ignore=tests/integration -v

# Integration test (needs Docker)
cd backend && pytest tests/integration/ -v -m integration

# Playwright E2E (needs running stack)
cd frontend && npx playwright test
```
