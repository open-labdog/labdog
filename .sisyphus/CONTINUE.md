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
| **Auth** | JWT via fastapi-users, httpOnly cookie (`barricade_auth`), first-user auto-promotes to superuser, registration gated (closed after first user) | `backend/app/auth/users.py`, `backend/app/api/auth_setup.py` |
| **User management** | Admin CRUD API with last-superuser guard, superuser-only `/users` page, sidebar logout + password change | `backend/app/api/admin_users.py`, `frontend/app/(dashboard)/users/page.tsx` |
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
| **Frontend pages** | Auth, Dashboard, Groups, Hosts, SSH Keys, Rules, Sync, Drift, Audit, Users, Git Repos, Discovery | `frontend/app/(dashboard)/`, `frontend/app/(auth)/` |

### GitOps Extension (full stack) ✅

| Area | What | Key Files |
|------|------|-----------|
| **GitRepository model** | CRUD + Alembic migration + HostGroup gitops fields | `backend/app/api/git_repos.py` |
| **Git service** | Clone/pull with SSH + HTTPS token auth | `backend/app/gitops/git_service.py` |
| **YAML serializer** | Multi-module format (extensible), firewall module | `backend/app/gitops/serializer.py` |
| **Webhooks** | GitHub, GitLab, Gitea with HMAC/token verification | `backend/app/api/webhooks.py` |
| **Import/reconcile** | YAML → validate → diff → update DB | `backend/app/gitops/importer.py` |
| **Auto-sync pipeline** | Webhook → import → audit log → sync to hosts | `backend/app/gitops/pipeline.py` |
| **Rule lockdown** | 403 on rule mutation for GitOps groups + UI indicators | `backend/app/api/rules.py` |
| **Frontend — /git-repos page** | Full CRUD with webhook URL display + copy buttons | `frontend/app/(dashboard)/git-repos/page.tsx` |
| **Frontend — Group GitOps settings** | Enable/disable dialog, status card on group detail | `frontend/app/(dashboard)/groups/[id]/page.tsx` |
| **Frontend — Groups list GitOps column** | Status badge per group | `frontend/app/(dashboard)/groups/page.tsx` |

### Host Discovery (full stack) ✅

| Area | What | Key Files |
|------|------|-----------|
| **Network scanner** | Async TCP port 22 scan on CIDR ranges | `backend/app/discovery/scanner.py` |
| **Discovery API** | POST scan, GET status/results, POST bulk-add | `backend/app/api/discovery.py` |
| **Frontend — /hosts/discover page** | Scan form, live results, bulk-add | `frontend/app/(dashboard)/hosts/discover/page.tsx` |

### User Management & Auth Improvements ✅

| Area | What | Key Files |
|------|------|-----------|
| **First-user bootstrap** | First registered user auto-promoted to superuser via `on_after_register` | `backend/app/auth/users.py` |
| **Registration gating** | `/auth/register` blocked when users exist (403), `GET /auth/setup-status` public endpoint | `backend/app/api/auth_setup.py` |
| **User admin API** | Full CRUD at `/api/admin/users` with last-superuser guard + self-delete guard | `backend/app/api/admin_users.py` |
| **RBAC removed** | Per-group roles (admin/editor/viewer) removed entirely. Only superuser vs regular user. All authenticated users see everything. | Deleted: `auth/rbac.py`, `api/permissions.py`, `models/user_group_permission.py` |
| **Alembic migration** | Drop `user_group_permissions` table and `grouprole` enum | `backend/alembic/versions/0003_drop_rbac.py` |
| **Frontend — /users page** | Superuser-only CRUD: create, edit, reset password, delete users | `frontend/app/(dashboard)/users/page.tsx` |
| **Sidebar user menu** | User email + logout + password change at sidebar bottom | `frontend/components/sidebar.tsx` |
| **Conditional nav** | "Users" nav item visible only to superusers | `frontend/components/sidebar.tsx` |
| **Registration flow** | Login page hides register link; register page shows "closed" when users exist | `frontend/app/(auth)/login/page.tsx`, `frontend/app/(auth)/register/page.tsx` |

### Test Suite

| Suite | Count | What |
|-------|-------|------|
| **Unit tests** | 54 | Crypto, diff, renderers, rules, parsers (nftables/firewalld/ufw) |
| **API tests** | ~60 | Auth, groups, hosts, sync, audit, drift, merge, gitops (need Docker) |
| **Playwright E2E** | 55+ | Auth, groups, hosts, rules, sync, audit, dashboard (need running stack) |
| **Integration** | 1 | Full workflow test with testcontainers (14 steps: register → audit) |

---

## What's Planned (Future)

### 1. Platform Extensions — `ext-service-management.md`
**Status**: Plan exists, nothing built.

First extension module: systemd service management (start/stop/enable/disable services across hosts).

### 2. Additional Extension Modules — `barricade-extensions.md`
**Status**: Design document only, nothing built. Roadmap for extending Barricade beyond firewalls.

Proposed modules:
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
# First user to register automatically becomes superuser
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
