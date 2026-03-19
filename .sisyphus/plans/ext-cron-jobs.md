# Cron Job Management Extension — Centralized Scheduled Task Control

## TL;DR

> **Quick Summary**: Add cron job management to Barricade. Define scheduled tasks (cron expression + command) at the group level with per-host overrides. Barricade syncs via Ansible `builtin.cron`, detects drift by parsing `crontab -l` output, and uses the cron job `name` slug as the merge/identity key.
>
> **Deliverables**:
> - `CronJob` model + Alembic migration
> - Cron job CRUD API (group-level + host-level) with effective-config merge endpoint
> - Ansible playbook generator for `builtin.cron`
> - Drift detector via SSH (`crontab -l -u {user}`)
> - Celery sync task + periodic drift task
> - Frontend: group cron page + host detail "Cron Jobs" tab with human-readable schedule display
> - pytest suite
>
> **Estimated Effort**: Small
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T3 → T5 → T7 → T9
>
> **Prerequisite**: Service Management extension (provides `host_module_status` table and `SyncJob.module_type`)

---

## Context

### Architecture Decisions
- **Assignment model**: Group-level defaults + per-host overrides. `CronJob` has nullable `group_id` and `host_id` with DB CHECK constraint.
- **Merge**: `name` (slug) is the merge key. Host override = full record replacement. Higher-priority group wins on conflict.
- **Identity**: Ansible `builtin.cron` uses `name` as the unique identifier per user. Two jobs with the same `name` for the same `user` are a conflict — Barricade enforces uniqueness within scope (group or host) on `(name, user)`.
- **Cron expression**: Stored as a single `schedule` string (e.g., `"0 2 * * *"`). Validated as 5-field cron format. Parsed into minute/hour/day/month/weekday for Ansible.
- **Environment vars**: Optional JSONB dict of env vars set before the job runs.

### Security
- `command` is intentionally arbitrary (cron jobs run arbitrary commands by design)
- `user` defaults to `root` — validate against protected user deny-list from user management module (if available) or a local deny-list
- `name` must be a valid slug: alphanumeric + hyphens + underscores, max 100 chars (used as Ansible cron `name` identifier)
- No `@reboot` or special schedules — only standard 5-field cron expressions

---

## Work Objectives

### Definition of Done
- [x] Group-level cron jobs: CRUD on `/api/groups/{id}/cron-jobs`
- [x] Host-level overrides: CRUD on `/api/hosts/{id}/cron-jobs`
- [x] Effective config: `GET /api/hosts/{id}/effective-cron-jobs` merges group defaults + host overrides
- [x] Plan: `POST /api/cron/hosts/{id}/plan` previews changes
- [x] Sync: `POST /api/cron/hosts/{id}/sync` applies via Ansible
- [x] Drift: `POST /api/cron/hosts/{id}/drift-check` detects mismatches
- [x] Frontend: `/groups/{id}/cron-jobs` page + "Cron Jobs" tab on host detail
- [x] Tests: 10+ tests

### Must Have
- `CronJob` model: `name` (slug), `user` (default root), `schedule` (5-field cron), `command`, `environment` (JSONB dict), `state` (present/absent), `comment`, `priority`
- DB CHECK constraint for group_id/host_id exclusivity
- Unique constraint on `(name, user)` within same scope (group or host)
- Cron expression validation: 5 fields, valid ranges (0-59, 0-23, 1-31, 1-12, 0-7)
- `name` slug validation: alphanumeric + hyphens + underscores
- Ansible `builtin.cron` with parsed minute/hour/day/month/weekday
- Drift collector: `crontab -l -u {user}` → parse Ansible-managed entries (look for `#Ansible:` marker)
- Alembic migration (reversible)

### Must NOT Have (Guardrails)
- No `@reboot`, `@hourly`, `@daily` special schedules — standard 5-field only
- No cron job output/log management
- No job execution monitoring or success/failure tracking
- No `/etc/cron.d/` file management (use `crontab` per-user only)
- No modification to existing module code
- No GitOps integration

---

## Execution Strategy

```
Wave 1 (Foundation — 3 parallel):
├── T1: CronJob model + Alembic migration [quick]
├── T2: Cron schemas + cron expression validator [unspecified-high]
└── T3: Cron merge engine [unspecified-high]

Wave 2 (Backend — 3 parallel):
├── T4: Cron CRUD API + effective-config endpoint [unspecified-high]
├── T5: Ansible cron playbook generator [quick]
└── T6: Cron drift collector (crontab -l parser) [unspecified-high]

Wave 3 (Sync + Drift — 2 parallel):
├── T7: Cron sync Celery task + sync API [unspecified-high]
└── T8: Cron drift detection task + API [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group cron page + host detail tab [visual-engineering]
└── T10: pytest suite [unspecified-high]

Critical Path: T1 → T4 → T7 → T9
Max Concurrent: 3
```

---

## TODOs

- [x] 1. CronJob Model + Alembic Migration

  **What to do**:
  - Create `backend/app/cron/__init__.py` (empty)
  - Create `backend/app/cron/models.py`:
    - `CronState` enum: `present`, `absent`
    - `CronJob` model:
      - `id`, `group_id` (FK nullable), `host_id` (FK nullable)
      - `name` (String(100), NOT NULL — slug identifier)
      - `user` (String(32), default "root")
      - `schedule` (String(100), NOT NULL — e.g., "0 2 * * *")
      - `command` (Text, NOT NULL)
      - `environment` (JSONB, default {} — env vars dict)
      - `state` (Enum CronState, default present)
      - `priority` (Integer, default 0), `comment` (Text, nullable)
      - `created_at`, `updated_at`
      - CHECK: `(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)`
      - UNIQUE: `(group_id, name, user)` where group_id is not null + `(host_id, name, user)` where host_id is not null (partial unique indexes)
  - Create Alembic migration

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 1 | Blocks: T3, T4, T7, T8 | Blocked By: None

  **References**:
  - `backend/app/services/models.py` — ServiceRule model pattern

  **Acceptance Criteria**:
  - [ ] Table created with CHECK and uniqueness constraints
  - [ ] `environment` is JSONB with default `{}`
  - [ ] Migration reversible

  **Commit**: YES — `feat(models): add CronJob model`

- [x] 2. Cron Schemas + Cron Expression Validator

  **What to do**:
  - Create `backend/app/cron/schemas.py`:
    - `CronJobCreate`: `name` (str), `user` (str, default "root"), `schedule` (str), `command` (str), `environment` (dict[str,str], default {}), `state` (present/absent), `priority`, `comment`
      - Validator `name`: slug format — `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`, max 100 chars
      - Validator `schedule`: must be valid 5-field cron — split by whitespace, verify 5 parts, validate ranges:
        - minute: 0-59 (or `*`, `*/N`, `N-M`, `N,M`)
        - hour: 0-23
        - day of month: 1-31
        - month: 1-12
        - day of week: 0-7 (0 and 7 both = Sunday)
        - Support `*`, `*/N`, `N-M`, `N,M,O` notation
      - Validator `user`: reject system users matching the user_mgmt deny-list if available, or at minimum reject empty string
    - `CronJobResponse`: all fields + `id`, `group_id`, `host_id`, `created_at`, `updated_at`
    - `EffectiveCronJobResponse`: fields + `source`, `source_id`, `source_name`
  - Create `backend/app/cron/validators.py`:
    - `def validate_cron_expression(expr: str) -> tuple[str,str,str,str,str]`: returns (minute, hour, dom, month, dow) or raises ValueError
    - Handles: `*`, `*/N`, `N-M`, `N,M,O`, and plain integers

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 1 | Blocks: T4, T5, T6 | Blocked By: None

  **References**:
  - `backend/app/services/schemas.py` — Schema pattern

  **Acceptance Criteria**:
  - [ ] Valid cron: `"0 2 * * *"`, `"*/5 * * * *"`, `"0 0 1,15 * *"` accepted
  - [ ] Invalid cron: `"60 * * * *"`, `"* * * *"` (4 fields), `"@daily"` rejected
  - [ ] Slug validation: `"daily-backup"` OK, `"my job!"` rejected

  **Commit**: YES — `feat(cron): add schemas and cron expression validator`

- [x] 3. Cron Merge Engine

  **What to do**:
  - Create `backend/app/cron/merge.py`:
    - `async def get_effective_cron_jobs(host_id, db) -> list[EffectiveCronJobResponse]`:
      - Group priority merge (key = `(name, user)` tuple). Host override = full record replacement.

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 1 | Blocks: T4, T7 | Blocked By: T1

  **References**:
  - `backend/app/services/merge.py` — Service merge pattern

  **Acceptance Criteria**:
  - [ ] Merge key is `(name, user)` composite — same name different user = different jobs
  - [ ] Host override replaces group entry
  - [ ] Source annotation correct

  **Commit**: YES — `feat(cron): add merge engine`

- [x] 4. Cron CRUD API + Effective-Config

  **What to do**:
  - Create `backend/app/api/cron_jobs.py`:
    - Group CRUD: `GET/POST/PUT/DELETE /api/groups/{group_id}/cron-jobs`
    - Host overrides: `GET/POST/PUT/DELETE /api/hosts/{host_id}/cron-jobs`
    - Effective: `GET /api/hosts/{host_id}/effective-cron-jobs`
  - Register in `app/main.py`, audit logging on mutations

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2 | Blocks: T7, T9 | Blocked By: T1, T2, T3

  **Acceptance Criteria**:
  - [ ] All endpoints registered
  - [ ] Duplicate `(name, user)` in same scope rejected
  - [ ] Invalid cron expression rejected with 422
  - [ ] Audit entries created

  **Commit**: YES — `feat(api): add cron job CRUD + effective-config endpoints`

- [x] 5. Ansible Cron Playbook Generator

  **What to do**:
  - Create `backend/app/cron/generator.py`:
    - `def generate_cron_playbook(host_ip, cron_jobs: list, ssh_key_path) -> dict`:
      - Parse each job's `schedule` into minute/hour/day/month/weekday via validator
      - For each job with `state=present`:
        - `ansible.builtin.cron`: `name`, `user`, `minute`, `hour`, `day`, `month`, `weekday`, `job` (command)
        - If `environment` dict non-empty: add `env: yes` tasks for each var before the job
      - For `state=absent`: `ansible.builtin.cron` with `state: absent`, `name`, `user`
      - Playbook: `become: true`, `gather_facts: false`

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 2 | Blocks: T7 | Blocked By: T2

  **References**:
  - `backend/app/services/generator.py` — Service playbook pattern

  **Acceptance Criteria**:
  - [ ] Cron expression parsed into 5 fields for Ansible
  - [ ] Environment vars set via `ansible.builtin.cron` `env` parameter
  - [ ] Absent jobs removed by name+user

  **Commit**: YES — `feat(ansible): add cron job playbook generator`

- [x] 6. Cron Drift Collector + Parser

  **What to do**:
  - Create `backend/app/cron/collector.py`:
    - `async def collect_cron_jobs(host_ip, ssh_port, private_key_pem, users: list[str]) -> list[dict]`:
      - For each user: `crontab -l -u {user} 2>/dev/null`
      - Parse output: Ansible-managed cron entries have a `#Ansible: {name}` comment marker on the line above the job
      - Extract: name (from marker), schedule (5 fields), command (rest of line), user
      - Skip non-Ansible-managed entries (no marker)
      - Empty crontab or user without crontab → empty list for that user
  - Create `backend/app/cron/diff.py`:
    - `CronDiff` dataclass: `jobs_to_add`, `jobs_to_remove`, `jobs_to_update`, `jobs_in_sync`
    - Compare by `(name, user)` key: check schedule + command match

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2 | Blocks: T7, T8 | Blocked By: T2

  **Acceptance Criteria**:
  - [ ] `#Ansible:` marker lines parsed correctly
  - [ ] Non-managed entries skipped
  - [ ] Empty crontab handled gracefully
  - [ ] Schedule + command compared for drift

  **Commit**: YES — `feat(cron): add drift collector and crontab parser`

- [x] 7. Cron Sync Celery Task + Sync API

  **What to do**:
  - Create `backend/app/tasks/cron_sync.py`:
    - Celery task: merge effective cron jobs → generate playbook → ansible-runner → update SyncJob + host_module_status
    - `module_type="cron"` on SyncJob
  - Create `backend/app/api/cron_sync.py`:
    - `POST /api/cron/hosts/{host_id}/plan`
    - `POST /api/cron/hosts/{host_id}/sync`
    - `POST /api/cron/groups/{group_id}/sync`
    - `GET /api/cron/jobs/{job_id}`
  - Register router

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T4, T5, T6

  **Acceptance Criteria**:
  - [ ] Plan shows jobs to add/remove/update
  - [ ] SyncJob with `module_type="cron"`
  - [ ] Environment vars included in playbook

  **Commit**: YES — `feat(tasks): add cron sync Celery task + sync API`

- [x] 8. Cron Drift Detection Task + API

  **What to do**:
  - Create `backend/app/tasks/cron_drift.py`
  - Add drift endpoints:
    - `POST /api/cron/hosts/{host_id}/drift-check`
    - `PUT /api/cron/hosts/{host_id}/drift-settings`

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 3 | Blocks: T10 | Blocked By: T1, T6

  **Acceptance Criteria**:
  - [ ] Missing jobs detected as drift
  - [ ] Changed schedule or command detected
  - [ ] `host_module_status` updated

  **Commit**: YES — `feat(tasks): add cron drift detection + API`

- [x] 9. Frontend — Group Cron Page + Host Detail Tab

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/cron-jobs/page.tsx`:
    - Table: Name | User | Schedule | Command | State | Actions
    - Create/edit dialog: name (slug), user, schedule (with human-readable preview: "Every day at 2:00 AM"), command (textarea), environment (key-value pairs editor), state
    - Human-readable schedule display: parse cron to text (use a library like `cronstrue` or inline helper)
  - Add "Cron Jobs" tab on host detail page:
    - Effective cron jobs (merged)
    - Host override CRUD
  - Add "Cron Jobs" link on group detail page
  - TypeScript interfaces in `lib/types.ts`

  **Recommended Agent Profile**: `visual-engineering` + `frontend-ui-ux`
  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T4, T7

  **Acceptance Criteria**:
  - [ ] Schedule shown in human-readable format ("Every day at 2:00 AM")
  - [ ] Environment vars editable as key-value pairs
  - [ ] Host detail has "Cron Jobs" tab
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add cron job management pages`

- [x] 10. pytest Suite

  **What to do**:
  - Create `backend/tests/test_cron.py`:
    - **TestCronValidator**: valid crons accepted, invalid rejected, ranges validated, `*/N` notation
    - **TestCronSchemas**: slug validation, protected fields, state enum
    - **TestCronMerge**: composite key `(name, user)`, group priority, host override
    - **TestCronAPI**: CRUD, effective, duplicate rejection, invalid cron 422
    - **TestCronCollector**: `#Ansible:` marker parsing, non-managed entries skipped
    - **TestCronDiff**: schedule change detected, command change, absent detection

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T7, T8

  **Acceptance Criteria**:
  - [ ] 12+ tests, all passing
  - [ ] Cron expression validation thoroughly tested
  - [ ] Crontab parser tested with real `crontab -l` output

  **Commit**: YES — `test(cron): add cron job management test suite`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
- [x] F2. **Code Quality Review** — `unspecified-high`
- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright`)
- [x] F4. **Scope Fidelity Check** — `deep` — Verify: no @reboot, no /etc/cron.d/, no job monitoring, no output management.

---

## Success Criteria

```bash
cd backend && pytest tests/test_cron.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```
