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
- All service CRUD endpoints use `current_active_user` dependency (matches firewall rules pattern)

### Must NOT Have (Guardrails)
- No modification to existing firewall module code **except** these narrow, backward-compatible changes to `backend/app/api/sync.py`: (1) add `module_type: str = "firewall"` field to `SyncJobResponse`, (2) add optional `module_type` query param to `list_jobs()`, (3) scope `trigger_host_sync()` running-sync check to `module_type="firewall"`
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

- [x] 1. ServiceRule Model + host_module_status Table + SyncJob module_type + Alembic Migration

  **What to do**:
  - Create `backend/app/services/__init__.py` (empty)
  - Create `backend/app/services/models.py`:
    - `ServiceState` enum: `running`, `stopped`
    - `ServiceRule` SQLAlchemy model:
      - `id` (Integer, PK), `group_id` (FK to host_groups, nullable), `host_id` (FK to hosts, nullable)
      - `service_name` (String(100), NOT NULL), `state` (Enum ServiceState, default running)
      - `enabled` (Boolean, default True — systemd enable/disable)
      - `priority` (Integer, default 0), `comment` (Text, nullable)
      - `created_at`, `updated_at` (DateTime with timezone)
      - CHECK constraint: `(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)`
  - Create `backend/app/models/host_module_status.py`:
    - `HostModuleStatus` model: `id`, `host_id` (FK), `module_type` (String(50)), `sync_status` (String(20), default "unknown"), `drift_check_enabled` (Boolean, default False), `last_sync_at` (DateTime nullable), `last_drift_check_at` (DateTime nullable)
    - Unique constraint on `(host_id, module_type)`
  - Add `module_type` column to `SyncJob`: `String(50)`, `server_default='firewall'`, nullable=False
  - Create Alembic migration for all three changes
  - Register models in `backend/app/models/__init__.py` if needed

  **Must NOT do**:
  - Do NOT modify existing `Host.sync_status` or `Host.drift_check_enabled` fields
  - Do NOT modify `FirewallRule` model
  - Do NOT change SyncJob behavior for existing firewall jobs

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with T2, T3)
  - **Blocks**: T3, T4, T7, T8
  - **Blocked By**: None

  **References**:
  - `backend/app/models/firewall_rule.py` — Model pattern to follow
  - `backend/app/models/sync_job.py` — SyncJob to extend
  - `backend/alembic/versions/` — Migration numbering pattern

  **Acceptance Criteria**:
  - [ ] `ServiceRule` table created with CHECK constraint
  - [ ] `host_module_status` table created with unique constraint
  - [ ] `SyncJob.module_type` column exists with default 'firewall'
  - [ ] Existing firewall SyncJob queries still work (backward-compat)
  - [ ] Migration reversible: `alembic downgrade -1` succeeds

  **Commit**: YES — `feat(models): add ServiceRule, host_module_status, SyncJob module_type`

- [x] 2. Service Schemas + Deny-List Constants

  **What to do**:
  - Create `backend/app/services/constants.py`:
    - `PROTECTED_SERVICES`: frozenset of service names that cannot be managed: `{"sshd", "ssh", "networking", "NetworkManager", "systemd-journald", "systemd-logind", "systemd-udevd", "systemd-resolved", "dbus"}`
  - Create `backend/app/services/schemas.py`:
    - `ServiceRuleCreate(BaseModel)`: `service_name` (str), `state` (Literal["running","stopped"]), `enabled` (bool, default True), `priority` (int, default 0), `comment` (str|None)
      - Validator: reject `service_name` in `PROTECTED_SERVICES`
      - Validator: strip `.service` suffix from service_name (normalize)
    - `ServiceRuleUpdate(BaseModel)`: same fields, all optional
    - `ServiceRuleResponse(BaseModel)`: all fields + `id`, `group_id`, `host_id`, `created_at`, `updated_at`, `model_config = ConfigDict(from_attributes=True)`
    - `EffectiveServiceResponse(BaseModel)`: `service_name`, `state`, `enabled`, `source` (Literal["group","host"]), `source_id` (int), `source_name` (str)

  **Must NOT do**:
  - Do NOT allow `restarted`/`reloaded` as valid states for DB storage (those are sync-time actions only)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with T1, T3)
  - **Blocks**: T4, T5, T6
  - **Blocked By**: None

  **References**:
  - `backend/app/schemas/rules.py` — Schema pattern to follow
  - `backend/app/services/constants.py` — Deny-list location

  **Acceptance Criteria**:
  - [ ] `PROTECTED_SERVICES` contains sshd and systemd-* services
  - [ ] `ServiceRuleCreate` rejects protected service names with 422
  - [ ] `service_name` normalized: `"nginx.service"` → `"nginx"`

  **Commit**: YES — `feat(services): add schemas and deny-list constants`

- [x] 3. Service Merge Engine

  **What to do**:
  - Create `backend/app/services/merge.py`:
    - `async def get_effective_services(host_id: int, db: AsyncSession) -> list[EffectiveServiceResponse]`:
      1. Get host's group memberships (ordered by group priority DESC)
      2. For each group, get `ServiceRule` where `group_id=gid` ordered by priority
      3. Merge: higher-priority group wins (key = `service_name`)
      4. Get host-level overrides: `ServiceRule` where `host_id=host_id`
      5. Host overrides replace group entries (full record, keyed by `service_name`)
      6. Return list with `source="group"|"host"` annotation

  **Must NOT do**:
  - Do NOT do field-level merge — host override replaces entire record
  - Do NOT modify `app/rules/merge.py` (firewall merge)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with T1, T2) but depends on T1 model
  - **Blocks**: T4, T7
  - **Blocked By**: T1

  **References**:
  - `backend/app/rules/merge.py` — Firewall merge pattern (adapt, don't modify)
  - `backend/app/api/drift.py:34-48` — `_get_desired_rules_for_host` pattern

  **Acceptance Criteria**:
  - [ ] Higher-priority group rule wins on `service_name` conflict
  - [ ] Host override replaces group default entirely
  - [ ] Source annotation shows "group" or "host" for each entry

  **Commit**: YES — `feat(services): add merge engine with host-override support`

- [x] 4. Service CRUD API

  **What to do**:
  - Create `backend/app/api/services.py` with router prefix `/api`:
    - **Group-level CRUD**:
      - `GET /api/groups/{group_id}/services` — list rules for group
      - `POST /api/groups/{group_id}/services` — create rule (superuser only)
      - `PUT /api/groups/{group_id}/services/{rule_id}` — update rule
      - `DELETE /api/groups/{group_id}/services/{rule_id}` — delete rule
    - **Host-level overrides**:
      - `GET /api/hosts/{host_id}/services` — list host-specific overrides
      - `POST /api/hosts/{host_id}/services` — create host override (superuser only)
      - `PUT /api/hosts/{host_id}/services/{rule_id}` — update override
      - `DELETE /api/hosts/{host_id}/services/{rule_id}` — delete override
    - **Effective config**:
      - `GET /api/hosts/{host_id}/effective-services` — merged group + host overrides
  - Register router in `backend/app/main.py`
  - Call `log_action()` on all mutations with `entity_type="service_rule"`

  **Auth**: Use `current_active_user` for all service endpoints (consistent with firewall rules API pattern). Do NOT use `current_superuser`.

  **Must NOT do**:
  - Do NOT use `current_superuser` — service rule CRUD follows the same auth pattern as firewall rule CRUD
  - Do NOT add any role-based access checks

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2 (with T5, T6)
  - **Blocks**: T7, T9
  - **Blocked By**: T1, T2, T3

  **References**:
  - `backend/app/api/rules.py` — CRUD pattern to follow
  - `backend/app/main.py` — Router registration
  - `backend/app/audit/logger.py` — `log_action()` usage

  **Acceptance Criteria**:
  - [ ] All 9 endpoints registered and responding
  - [ ] Protected services rejected with 422
  - [ ] Effective-services merges group + host correctly
  - [ ] Audit log entries created on mutations

  **Commit**: YES — `feat(api): add service management CRUD + effective-config endpoints`

- [x] 5. Ansible Service Playbook Generator

  **What to do**:
  - Create `backend/app/services/generator.py`:
    - `def generate_service_playbook(host_ip: str, services: list, ssh_key_path: str) -> dict`:
      - Generates Ansible playbook with `ansible.builtin.service` tasks
      - Map `state`: `running` → `started`, `stopped` → `stopped`
      - Map `enabled`: bool directly
      - Returns YAML-serializable playbook dict
      - Inventory generation reuses `backend/app/ansible/inventory.py`

  **Must NOT do**:
  - Do NOT use `state: restarted` or `state: reloaded` in generated playbook (those are transient)
  - Do NOT modify `backend/app/ansible/generator.py` (firewall-specific)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2 (with T4, T6)
  - **Blocks**: T7
  - **Blocked By**: T2

  **References**:
  - `backend/app/ansible/generator.py` — Firewall playbook pattern (do not modify)
  - `backend/app/ansible/inventory.py` — Reuse for inventory generation

  **Acceptance Criteria**:
  - [ ] Generated playbook uses `ansible.builtin.service` module
  - [ ] `state: running` maps to `state: started` in Ansible
  - [ ] Playbook includes `become: true` and `gather_facts: false`

  **Commit**: YES — `feat(ansible): add service playbook generator`

- [x] 6. Service Drift Collector + Diff

  **What to do**:
  - Create `backend/app/services/collector.py`:
    - `async def collect_service_states(host_ip, ssh_port, private_key_pem, service_names: list[str]) -> list[dict]`:
      - SSH into host via asyncssh
      - For each service: run `systemctl is-active {name}` and `systemctl is-enabled {name}`
      - Parse output: `active` → running, `inactive`/`failed` → stopped, `enabled` → True, `disabled` → False
      - Handle service not found: `systemctl is-active unknown` returns exit code 4 → mark as `error`
  - Create `backend/app/services/diff.py`:
    - `ServiceDiff` dataclass: `services_to_update`, `services_in_sync`, `services_with_errors`
    - `def compute_service_diff(current: list, desired: list) -> ServiceDiff`
    - **CRITICAL**: Normalize desired `restarted`/`reloaded` to `running` for comparison

  **Must NOT do**:
  - Do NOT modify `backend/app/sync/collector.py` or `backend/app/sync/diff.py`
  - Do NOT check service health — only state (active/inactive) and enabled status

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2 (with T4, T5)
  - **Blocks**: T7, T8
  - **Blocked By**: T2

  **References**:
  - `backend/app/sync/collector.py` — SSH collection pattern (do not modify)
  - `backend/app/sync/diff.py` — Diff pattern (do not modify)

  **Acceptance Criteria**:
  - [ ] Collector SSHes in and runs `systemctl` commands
  - [ ] Service not found returns error state (not crash)
  - [ ] Diff normalizes `restarted`/`reloaded` to `running`

  **Commit**: YES — `feat(services): add drift collector via systemctl SSH`

- [x] 7. Service Sync Celery Task + Sync API

  **What to do**:
  - Create `backend/app/tasks/service_sync.py`:
    - `@celery_app.task(bind=True, name="app.tasks.service_sync.run_service_sync", queue="long_running")`
    - Same pattern as `tasks/sync.py`: DB lookup → merge effective services → generate playbook → ansible-runner → update SyncJob + host_module_status
    - Set `module_type="service"` on SyncJob
  - Create `backend/app/api/service_sync.py` with router:
    - `POST /api/services/hosts/{host_id}/plan` — preview changes (diff current vs desired)
    - `POST /api/services/hosts/{host_id}/sync` — trigger sync (creates SyncJob, dispatches task)
      - Running-sync check MUST scope to `module_type="service"`: `SyncJob.where(host_id == x, module_type == "service", status.in_(["pending", "running"]))`
    - `POST /api/services/groups/{group_id}/sync` — sync all hosts in group
    - `GET /api/services/jobs/{job_id}` — get job status
  - Register router in `app/main.py`
  - **Modify existing `backend/app/api/sync.py`** (narrow, backward-compatible changes):
    1. Add `module_type: str = "firewall"` field to `SyncJobResponse` schema
    2. Add optional `module_type: str | None = None` query param to `list_jobs()` endpoint with filter: `if module_type: q = q.where(SyncJob.module_type == module_type)`
    3. Scope `trigger_host_sync()` running-sync check to firewall: add `.where(SyncJob.module_type == "firewall")` to the existing pending/running query
  - **Update `backend/app/tasks/__init__.py`** — add Celery task routing:
    ```python
    "app.tasks.service_sync.*": {"queue": "long_running"},
    "app.tasks.service_drift.*": {"queue": "long_running"},
    ```

  **Must NOT do**:
  - Do NOT modify `backend/app/tasks/sync.py` (Celery task logic)
  - Do NOT change existing sync behavior — only add `module_type` awareness to `sync.py` API as described above

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 3 (with T8)
  - **Blocks**: T9, T10
  - **Blocked By**: T4, T5, T6

  **References**:
  - `backend/app/tasks/sync.py` — Celery sync task pattern (read only, do not modify)
  - `backend/app/api/sync.py` — Sync API pattern + narrow modifications target
  - `backend/app/tasks/__init__.py` — Celery config and task routing

  **Acceptance Criteria**:
  - [ ] Plan endpoint returns diff of current vs desired service states
  - [ ] Sync creates SyncJob with `module_type="service"`
  - [ ] Job status queryable via GET endpoint
  - [ ] Running firewall sync does NOT block service sync (and vice versa)
  - [ ] `GET /api/sync/jobs?module_type=service` returns only service jobs
  - [ ] `GET /api/sync/jobs` (no filter) returns all jobs (backward-compatible)
  - [ ] `SyncJobResponse` includes `module_type` field in all responses
  - [ ] Celery task routes updated for service_sync and service_drift

  **Commit**: YES — `feat(tasks): add service sync Celery task + sync API with module-scoped sync`

- [x] 8. Service Drift Detection Task + API

  **What to do**:
  - Create `backend/app/tasks/service_drift.py`:
    - Celery task for periodic service drift checks
    - Checks all hosts with `host_module_status.module_type="service"` and `drift_check_enabled=True`
    - Uses collector + diff to determine drift status
    - Updates `host_module_status` record
  - Add drift API endpoints to `backend/app/api/service_sync.py` (or separate file):
    - `POST /api/services/hosts/{host_id}/drift-check` — manual drift check
    - `PUT /api/services/hosts/{host_id}/drift-settings` — enable/disable periodic drift

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 3 (with T7)
  - **Blocks**: T10
  - **Blocked By**: T1, T6

  **References**:
  - `backend/app/drift/detector.py` — Drift pattern
  - `backend/app/tasks/drift.py` — Periodic drift task pattern

  **Acceptance Criteria**:
  - [ ] Manual drift check returns current vs desired comparison
  - [ ] Drift settings toggleable per host
  - [ ] `host_module_status` updated after each check

  **Commit**: YES — `feat(tasks): add service drift detection task + API`

- [x] 9. Frontend — Group Services Page + Host Detail Services Tab

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/services/page.tsx`:
    - Table of service rules for the group (CRUD)
    - "Add Service" button → dialog with service_name, state (running/stopped), enabled toggle
    - Edit/delete per row
  - Modify `frontend/app/(dashboard)/hosts/[id]/page.tsx`:
    - Add tab navigation: "Overview" | "Services" (future: "Users")
    - "Services" tab shows effective services (merged group + host overrides)
    - Each row shows: service_name, state, enabled, source (group name or "Host Override")
    - "Add Override" button for host-level overrides
    - Edit/delete for host overrides only (group rules shown read-only)
  - Add "Services" link on group detail page (alongside existing "Rules" and "Sync" links)
  - Add TypeScript interfaces for ServiceRule in `frontend/lib/types.ts`

  **Must NOT do**:
  - Do NOT add services to sidebar navigation (accessed via group/host detail pages)
  - Do NOT add service monitoring or live status polling

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 4 (with T10)
  - **Blocks**: F1-F4
  - **Blocked By**: T4, T7

  **References**:
  - `frontend/app/(dashboard)/groups/[id]/rules/page.tsx` — Group rules page pattern
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Host detail page to modify
  - `frontend/lib/api.ts` — `apiFetch` helper
  - `frontend/lib/types.ts` — Type definitions

  **Acceptance Criteria**:
  - [ ] Group services page shows CRUD for service rules
  - [ ] Host detail page has tab navigation with "Services" tab
  - [ ] Effective services show source annotation (group vs host)
  - [ ] Host overrides editable; group rules read-only on host page
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add service management pages`

- [x] 10. Service Management Test Suite

  **What to do**:
  - Create `backend/tests/test_services.py`:
    - **TestServiceSchemas** (pure, no DB):
      - `test_protected_service_rejected` — sshd in PROTECTED_SERVICES
      - `test_service_name_normalized` — `"nginx.service"` → `"nginx"`
      - `test_valid_service_accepted` — `"nginx"` passes
    - **TestServiceMerge** (needs DB):
      - `test_group_priority_wins` — higher-priority group's service rule wins
      - `test_host_override_replaces_group` — host override replaces group default
      - `test_effective_services_annotated` — source="group"|"host" correct
    - **TestServiceAPI** (needs DB):
      - `test_create_group_service` — POST /api/groups/{id}/services → 201
      - `test_create_host_override` — POST /api/hosts/{id}/services → 201
      - `test_effective_services_endpoint` — GET /api/hosts/{id}/effective-services correct
      - `test_protected_service_rejected_by_api` — POST with sshd → 422
    - **TestServiceDiff** (pure):
      - `test_in_sync_detected` — desired=running, actual=running → in_sync
      - `test_drift_detected` — desired=running, actual=stopped → out_of_sync
      - `test_restarted_normalized` — desired=restarted treated as running for comparison
  - Add factory helper `create_service_rule()` to conftest.py (or local fixture)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 4 (with T9)
  - **Blocks**: F1-F4
  - **Blocked By**: T7, T8

  **References**:
  - `backend/tests/conftest.py` — Fixtures and factory pattern
  - `backend/tests/test_rules.py` — Test structure to follow

  **Acceptance Criteria**:
  - [ ] 12+ tests, all passing
  - [ ] Schema, merge, API, and diff all covered
  - [ ] Protected service deny-list tested
  - [ ] Drift normalization tested

  **Commit**: YES — `test(services): add service management test suite`

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
