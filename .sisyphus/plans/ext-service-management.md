# Service Management Extension — Systemd Service Control

## TL;DR

> **Quick Summary**: Add systemd service management to Barricade. Define desired service states (running/stopped, enabled/disabled) at the group level with per-host overrides. Barricade syncs desired state to hosts via Ansible `builtin.service`, detects drift via `systemctl`, and tracks per-module sync status independently from firewall.
>
> **Deliverables**:
> - `ServiceRule` model + `host_module_status` table + Alembic migration
> - `module_type` column on `SyncJob` (backward-compatible, shared infrastructure)
> - Service CRUD API (group-level + host-level) with effective-config merge endpoint
> - Ansible playbook generator for `builtin.service`
> - Drift detector via SSH `systemctl` commands
> - Celery sync task + periodic drift task
> - Frontend: group services page + host detail Services tab
> - pytest suite
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T3 → T5 → T7 → T9

---

## Context

### Original Request
Add service management as the first Barricade extension module. Manage systemd services (running/stopped, enabled/disabled) across hosts using the same plan-before-apply pattern as firewall rules.

### Architecture Decisions
- **Assignment model**: Both group-level defaults AND per-host overrides. `ServiceRule` has nullable `group_id` and `host_id` with DB CHECK constraint enforcing exactly one.
- **Merge**: `service_name` is the merge key. Higher-priority group wins on conflict. Host override does full-record replacement.
- **Independent sync**: Service module syncs independently from firewall. No unified multi-module sync.
- **Per-module status**: New `host_module_status` junction table tracks sync/drift status per module per host. Existing `Host.sync_status` stays firewall-only.
- **Systemd-only**: Hard requirement. No SysVinit/OpenRC support.

### Metis Review — Key Directives
- Normalize `restarted`/`reloaded` → `running` for drift comparison (avoids false positives)
- Protected service deny-list as constant: `sshd`, `networking`, `systemd-*`
- DB CHECK constraint for group_id/host_id exclusivity
- `module_type` on SyncJob with `server_default='firewall'`
- New top-level package `app/services/` (not nested under `app/rules/`)
- MUST NOT modify existing firewall module code

---

## Work Objectives

### Core Objective
Enable centralized systemd service management with plan-before-apply diffs, drift detection, and audit logging — following the established Barricade pattern.

### Definition of Done
- [ ] Group-level service rules: CRUD on `/api/groups/{id}/services`
- [ ] Host-level overrides: CRUD on `/api/hosts/{id}/services`
- [ ] Effective config: `GET /api/hosts/{id}/effective-services` merges group defaults + host overrides
- [ ] Plan: `POST /api/services/hosts/{id}/plan` previews changes
- [ ] Sync: `POST /api/services/hosts/{id}/sync` applies via Ansible
- [ ] Drift: `POST /api/services/hosts/{id}/drift-check` detects mismatches
- [ ] Audit: All service changes logged
- [ ] Frontend: `/groups/{id}/services` page + Services tab on `/hosts/{id}`
- [ ] Tests: 10+ tests covering CRUD, merge, drift, deny-list

### Must Have
- `ServiceRule` model with `service_name`, `state` (running/stopped), `enabled` (bool), group_id/host_id
- DB CHECK constraint: exactly one of group_id/host_id set
- `host_module_status` table for per-module sync/drift tracking
- `module_type` column on SyncJob (server_default='firewall')
- Protected service deny-list constant
- Ansible `builtin.service` playbook generator
- SSH-based drift collector (`systemctl is-active/is-enabled`)
- Drift normalization: desired `restarted`/`reloaded` = actual `running`
- Host override: full record replacement by `service_name` merge key
- Alembic migration (reversible)
- RBAC: group-level endpoints use `require_group_role()`, host overrides require superuser

### Must NOT Have (Guardrails)
- No modification to existing firewall module code
- No systemd unit file management (services must pre-exist)
- No service health checks / HTTP probes
- No service dependency ordering
- No Docker/Podman container management
- No SysVinit/OpenRC support
- No GitOps integration for this module
- No password-based service account management
- No cross-module sync orchestration

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest + testcontainers)
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + pytest-asyncio + httpx

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── T1: ServiceRule model + host_module_status + SyncJob module_type + Alembic migration [unspecified-high]
├── T2: Service schemas + deny-list constant [quick]
└── T3: Service merge engine (group priority + host override) [unspecified-high]

Wave 2 (Backend — 3 parallel):
├── T4: Service CRUD API (group-level + host-level + effective-config) [unspecified-high]
├── T5: Ansible service playbook generator [quick]
└── T6: Service drift collector + parser (systemctl SSH) [unspecified-high]

Wave 3 (Sync + Drift — 2 parallel):
├── T7: Service sync Celery task + API endpoints [unspecified-high]
└── T8: Service drift detection task + API endpoint [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group services page + host detail Services tab [visual-engineering]
└── T10: pytest suite for service management [unspecified-high]

Wave FINAL (Review — 4 parallel):
├── F1: Plan compliance audit [oracle]
├── F2: Code quality review [unspecified-high]
├── F3: Real manual QA [unspecified-high]
└── F4: Scope fidelity check [deep]

Critical Path: T1 → T4 → T7 → T9 → F1-F4
Max Concurrent: 3 (Waves 1-2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T3, T4, T7, T8 | 1 |
| T2 | — | T4, T5, T6 | 1 |
| T3 | T1 | T4, T7 | 1 |
| T4 | T1, T2, T3 | T7, T9 | 2 |
| T5 | T2 | T7 | 2 |
| T6 | T2 | T7, T8 | 2 |
| T7 | T4, T5, T6 | T9, T10 | 3 |
| T8 | T1, T6 | T10 | 3 |
| T9 | T4, T7 | F1-F4 | 4 |
| T10 | T7, T8 | F1-F4 | 4 |

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|------------|
| 1 | 3 | T1→`unspecified-high`, T2→`quick`, T3→`unspecified-high` |
| 2 | 3 | T4→`unspecified-high`, T5→`quick`, T6→`unspecified-high` |
| 3 | 2 | T7→`unspecified-high`, T8→`unspecified-high` |
| 4 | 2 | T9→`visual-engineering`, T10→`unspecified-high` |
| FINAL | 4 | F1→`oracle`, F2-F4→`unspecified-high`/`deep` |

---

## TODOs

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Verify all Must Have items. Check deny-list enforced. Verify CHECK constraint. Verify host_module_status table. Verify SyncJob module_type backward-compat.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run pytest + ruff. Check no modifications to existing firewall code. Verify Alembic migration is reversible. No `as any`/`type: ignore`.

- [ ] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill)
  Navigate to group services page, create service rule, navigate to host detail Services tab, verify effective config, trigger sync preview.

- [ ] F4. **Scope Fidelity Check** — `deep`
  Verify no systemd unit file management. No service dependencies. No health checks. No Docker. No modifications to firewall files.

---

## Commit Strategy

| Task | Message | Key Files |
|------|---------|-----------|
| T1 | `feat(models): add ServiceRule, host_module_status, SyncJob module_type` | `app/services/models.py`, `app/models/host_module_status.py`, `alembic/versions/` |
| T2 | `feat(services): add schemas and deny-list constants` | `app/services/schemas.py`, `app/services/constants.py` |
| T3 | `feat(services): add merge engine with host-override support` | `app/services/merge.py` |
| T4 | `feat(api): add service management CRUD + effective-config endpoints` | `app/api/services.py`, `app/main.py` |
| T5 | `feat(ansible): add service playbook generator` | `app/services/generator.py` |
| T6 | `feat(services): add drift collector via systemctl SSH` | `app/services/collector.py`, `app/services/diff.py` |
| T7 | `feat(tasks): add service sync Celery task + sync API` | `app/tasks/service_sync.py`, `app/api/service_sync.py` |
| T8 | `feat(tasks): add service drift detection task + API` | `app/tasks/service_drift.py`, `app/api/service_drift.py` |
| T9 | `feat(ui): add service management pages` | `frontend/app/(dashboard)/groups/[id]/services/`, `frontend/app/(dashboard)/hosts/[id]/` |
| T10 | `test(services): add service management test suite` | `backend/tests/test_services.py` |

---

## Success Criteria

### Verification Commands
```bash
# Backend tests
cd backend && pytest tests/test_services.py -v

# Frontend build
cd frontend && npm run build

# Alembic migration reversible
cd backend && alembic upgrade head && alembic downgrade -1
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Frontend builds clean
- [ ] Migration reversible
- [ ] SyncJob backward-compatible (existing firewall jobs unaffected)
