# Package Management Extension — System Package Control

## TL;DR

> **Quick Summary**: Add package management to Barricade. Define desired package states (installed/absent/latest with optional version pinning) at the group level with per-host overrides. Barricade syncs via Ansible `builtin.package` (auto-detects apt/dnf/yum), detects drift via `dpkg -l`/`rpm -q`, and supports custom repository management.
>
> **Deliverables**:
> - `PackageRule` + `PackageRepository` models + Alembic migration
> - Package CRUD API (group-level + host-level) with effective-config merge endpoint
> - Repository CRUD API (group-level)
> - Ansible playbook generator for `builtin.package` + `builtin.apt_repository` / `builtin.yum_repository`
> - Drift detector via SSH (`dpkg -l` / `rpm -q`)
> - Celery sync task + periodic drift task
> - Frontend: group packages page + host detail "Packages" tab
> - pytest suite
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T4 → T7 → T9
>
> **Prerequisite**: Service Management extension (provides `host_module_status` table and `SyncJob.module_type`)

---

## Context

### Architecture Decisions
- **Assignment model**: Group-level defaults + per-host overrides for `PackageRule`. `PackageRepository` is group-level only (no per-host repos — repos affect all packages on the system).
- **Merge**: `package_name` is the merge key. Host override = full record replacement.
- **Package manager detection**: Use `ansible.builtin.package` (auto-detects). Also store `package_manager` hint on host model or let Ansible handle it.
- **Version pinning**: Optional `version` field. Formats: `"1.24.*"` (glob via fnmatch), `"1.24.1-1ubuntu1"` (exact). NO `>=` syntax — use `state: latest` for "newest." Passed directly to package manager for install; fnmatch used for drift comparison only.
- **Repository management**: Separate model, group-level only. Repos are synced BEFORE packages (dependency ordering in playbook).
- **Auth level**: `current_superuser` for all package endpoints (following cron pattern — system-impacting).
- **Update semantics**: Use `exclude_unset=True` (NOT `exclude_none`) so `version=null` (any version) can be set explicitly.
- **Repo deduplication**: Key is `(url, repo_type, distribution)` — same URL with different distros are different repos.
- **Sync+Drift API**: Bundle into one file (`api/package_sync.py`) following the cron pattern (most recent).

### Security
- Protected package deny-list: packages that could break SSH access or the system (`openssh-server`, `systemd`, `linux-image-*`, `kernel`, `glibc`, `libc6`)
- No `purge` state — only `present`/`absent`/`latest` (purge removes config, too dangerous)
- Repository URLs validated as HTTPS (no HTTP mirrors in production)

---

## Work Objectives

### Definition of Done
- [ ] Group-level package rules: CRUD on `/api/groups/{id}/packages`
- [ ] Host-level overrides: CRUD on `/api/hosts/{id}/packages`
- [ ] Repository management: CRUD on `/api/groups/{id}/package-repos`
- [ ] Effective config: `GET /api/hosts/{id}/effective-packages` merges group defaults + host overrides
- [ ] Plan: `POST /api/packages/hosts/{id}/plan` previews changes
- [ ] Sync: `POST /api/packages/hosts/{id}/sync` applies via Ansible (repos first, then packages)
- [ ] Drift: `POST /api/packages/hosts/{id}/drift-check` detects version/state mismatches
- [ ] Frontend: `/groups/{id}/packages` page + "Packages" tab on host detail
- [ ] Tests: 12+ tests

### Must Have
- `PackageRule` model: `package_name`, `version` (nullable), `state` (present/absent/latest), `package_manager` (enum: apt/dnf/yum/auto, default auto), `priority`, `comment` + UniqueConstraint on `(group_id, package_name)` and `(host_id, package_name)`
- `PackageRepository` model: `name`, `url`, `key_url` (nullable), `repo_type` (enum: apt/yum), `distribution` (nullable — for apt), `components` (nullable — for apt), `state` (present/absent)
- Both models: DB CHECK constraint for group_id/host_id on PackageRule; group_id only on PackageRepository. UniqueConstraint on `(group_id, name)` for PackageRepository.
- Protected package deny-list constant
- Ansible playbook ordering: repos → packages
- Multi-distro drift collector: `dpkg -l` for Debian/Ubuntu, `rpm -q` for RHEL/Fedora
- Alembic migration (reversible)

### Must NOT Have (Guardrails)
- No `purge` state (too dangerous — removes config files)
- No automatic repository GPG key import without explicit `key_url`
- No kernel/bootloader package management
- No package compilation from source
- No pip/npm/gem packages — system packages only
- No modification to existing module code (except `main.py` router registration, `tasks/__init__.py` routing, `frontend/lib/types.ts` types, and navigation links on group/host detail pages)
- No GitOps integration
- No `>=` version constraint syntax (only exact versions and shell globs)
- No `exclude_none` on Update schema dumps (use `exclude_unset` so `version=null` works)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── T1: PackageRule + PackageRepository models + Alembic migration [unspecified-high]
├── T2: Package schemas + deny-list + Update schemas + repository schemas [unspecified-high]
└── T6: Package drift collector (dpkg/rpm) + diff [unspecified-high] (pure SSH, no model imports)

Wave 2 (Core Logic — 2 parallel):
├── T3: Package merge engine (depends: T1, T2) [unspecified-high]
└── T5: Ansible package playbook generator (depends: T2) [unspecified-high]

Wave 3 (Backend APIs — 2 parallel):
├── T4: Package CRUD + Repository CRUD + effective-config APIs (depends: T1, T2, T3) [unspecified-high]
└── T7: Package sync + drift Celery tasks + sync/drift API + Celery routing (depends: T3, T5, T6) [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group packages page + host detail tab (depends: T4, T7) [visual-engineering]
└── T10: pytest suite (depends: T4, T7) [unspecified-high]

Critical Path: T1 → T3 → T4 → T9
Max Concurrent: 3
```

**NOTE**: T7 now bundles sync + drift (T8 merged in) following the cron pattern. T6 moved to Wave 1 since it's pure SSH logic with no model/schema imports. Former T8 drift tasks are absorbed into T7.

---

## TODOs

- [x] 1. PackageRule + PackageRepository Models + Alembic Migration

  **What to do**:
  - Create `backend/app/packages/__init__.py` (empty)
  - Create `backend/app/packages/models.py`:
    - `PackageState` enum (`str, enum.Enum`): `present`, `absent`, `latest`
    - `PackageManager` enum: `apt`, `dnf`, `yum`, `auto`
    - `RepoType` enum: `apt`, `yum`
    - `PackageRule` model:
      - `id`, `group_id` (FK nullable, ondelete CASCADE), `host_id` (FK nullable, ondelete CASCADE)
      - `package_name` (String(200)), `version` (String(100), nullable)
      - `state` (Enum PackageState, default present), `package_manager` (Enum PackageManager, default auto)
      - `priority` (Integer, default 0), `comment` (Text, nullable)
      - `created_at`, `updated_at` (DateTime timezone=True, server_default=func.now())
      - `CheckConstraint`: group_id/host_id exclusivity — `ck_package_rules_scope`
      - `UniqueConstraint("group_id", "package_name", name="uq_package_rules_group_pkg")` (conditional — when group_id NOT NULL)
      - `UniqueConstraint("host_id", "package_name", name="uq_package_rules_host_pkg")` (conditional — when host_id NOT NULL)
    - `PackageRepository` model:
      - `id`, `group_id` (FK, NOT NULL, ondelete CASCADE — repos are group-only)
      - `name` (String(100)), `url` (String(500)), `key_url` (String(500), nullable)
      - `repo_type` (SAEnum RepoType, with explicit `name=`), `distribution` (String(100), nullable), `components` (String(200), nullable)
      - `state` (Enum — present/absent), `created_at`, `updated_at`
      - `UniqueConstraint("group_id", "name", name="uq_package_repos_group_name")`
  - Create reversible Alembic migration: `alembic revision --autogenerate -m "add package tables"`

  **Must NOT do**:
  - Do NOT add a `purge` value to PackageState
  - Do NOT use `Enum` without explicit `name=` parameter in SAEnum (breaks Alembic)

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T3, T4, T7 | Blocked By: None

  **References**:
  - `backend/app/services/models.py:1-57` — ServiceRule model pattern (enums, CheckConstraint, ForeignKey with ondelete CASCADE, DateTime with timezone, server_default=func.now())
  - `backend/app/cron/models.py:1-62` — CronJob model pattern (str enum base, mapped_column, CHECK constraint naming)
  - `backend/app/models/base.py` — Import `Base` from here
  - `backend/app/models/host.py` — `hosts.id` FK target
  - `backend/app/models/host_group.py` — `host_groups.id` FK target

  **QA Scenarios**:
  ```
  Scenario: Migration applies and reverses cleanly
    Tool: Bash
    Steps:
      1. cd backend && alembic upgrade head
      2. alembic downgrade -1
      3. alembic upgrade head
    Expected: All 3 commands exit 0
    Evidence: .sisyphus/evidence/task-1-migration.txt

  Scenario: Models import without error
    Tool: Bash
    Steps:
      1. cd backend && python -c "from app.packages.models import PackageRule, PackageRepository, PackageState, PackageManager, RepoType; print('OK')"
    Expected: Prints "OK"
    Evidence: .sisyphus/evidence/task-1-import.txt
  ```

  **Commit**: YES — `feat(models): add PackageRule and PackageRepository models`

- [x] 2. Package Schemas + Deny-List + Update Schemas + Repository Schemas

  **What to do**:
  - Create `backend/app/packages/constants.py`:
    - `PROTECTED_PACKAGES`: frozenset `{"openssh-server", "openssh-client", "sshd", "systemd", "linux-image*", "linux-headers*", "kernel", "kernel-core", "glibc", "libc6", "libc-bin", "coreutils", "bash", "init", "grub", "grub2"}`
    - Use `fnmatch` for wildcard matching (`linux-image*`)
    - Helper: `def is_protected(name: str) -> bool` using fnmatch
  - Create `backend/app/packages/schemas.py`:
    - `PackageRuleCreate`: `package_name`, `version` (str|None), `state` (Literal["present","absent","latest"]), `package_manager` (Literal["auto","apt","dnf","yum"], default "auto"), `priority` (int, default 0), `comment` (str|None)
      - `field_validator("package_name")`: reject protected packages via `is_protected()`, validate regex `^[a-zA-Z0-9][a-zA-Z0-9._+:-]*$`
    - `PackageRuleUpdate`: ALL fields Optional. Used with `model_dump(exclude_unset=True)` so `version=None` (any version) can be set explicitly.
    - `PackageRuleResponse`: with `model_config = {"from_attributes": True}`
    - `EffectivePackageResponse`: includes `source` field (group name or "host override")
    - `PackageRepositoryCreate`: `name`, `url`, `key_url` (str|None), `repo_type` (Literal["apt","yum"]), `distribution` (str|None, required if apt), `components` (str|None), `state` (Literal["present","absent"])
      - `field_validator("url")`: must start with `https://` or `http://`
    - `PackageRepositoryUpdate`: ALL fields Optional, `exclude_unset=True`
    - `PackageRepositoryResponse`: with `from_attributes`

  **Must NOT do**:
  - Do NOT support `>=` version constraint syntax
  - Do NOT use `exclude_none` — must use `exclude_unset` on Update schemas

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T3, T4, T5, T6 | Blocked By: None

  **References**:
  - `backend/app/services/schemas.py:1-75` — Schema pattern (Create/Update/Response trio, field_validator, model_config)
  - `backend/app/services/schemas.py:30-51` — `ServiceRuleUpdate` with Optional fields
  - `backend/app/cron/schemas.py:1-100` — Cron schema pattern (Literal types, field_validator with @classmethod)
  - `backend/app/cron/schemas.py:70-80` — CronJobUpdate using `exclude_unset=True`

  **QA Scenarios**:
  ```
  Scenario: Protected packages rejected
    Tool: Bash
    Steps:
      1. cd backend && python -c "
         from app.packages.schemas import PackageRuleCreate
         try: PackageRuleCreate(package_name='openssh-server', state='present')
         except: print('REJECTED')
         try: PackageRuleCreate(package_name='linux-image-5.15.0-generic', state='present')
         except: print('REJECTED-WILDCARD')
         PackageRuleCreate(package_name='nginx', state='present')
         print('ACCEPTED-nginx')
         "
    Expected: REJECTED, REJECTED-WILDCARD, ACCEPTED-nginx
    Evidence: .sisyphus/evidence/task-2-schemas.txt
  ```

  **Commit**: YES — `feat(packages): add schemas, deny-list, repo schemas`

- [x] 3. Package Merge Engine

  **What to do**:
  - Create `backend/app/packages/merge.py`:
    - `async def get_effective_packages(host_id, db) -> list[EffectivePackageResponse]`:
      - Group priority merge (key = `package_name`) + host override
    - `async def get_effective_repos(host_id, db) -> list[PackageRepositoryResponse]`:
      - Collect repos from all groups the host belongs to (no merge conflicts — repos are additive, deduplicated by `url`)

  **Recommended Agent Profile**: `unspecified-high`

  **Must NOT do**:
  - Do NOT deduplicate repos by URL alone — key is `(url, repo_type, distribution)`

  **Parallelization**: Wave 2 | Blocks: T4, T7 | Blocked By: T1, T2

  **References**:
  - `backend/app/services/merge.py:1-72` — Service merge pattern (imports HostGroupMembership, HostGroup, orders by priority.desc())
  - `backend/app/models/host.py` — `HostGroupMembership` association table
  - `backend/app/models/host_group.py` — `HostGroup.priority` field

  **QA Scenarios**:
  ```
  Scenario: Merge priority + host override
    Tool: Bash (pytest)
    Steps:
      1. cd backend && pytest tests/test_packages.py -k TestPackageMerge -v
    Expected: All merge tests pass
    Evidence: .sisyphus/evidence/task-3-merge.txt
  ```

  **Commit**: YES — `feat(packages): add merge engine`

- [x] 4. Package + Repository CRUD API

  **What to do**:
  - Create `backend/app/api/packages.py`:
    - Group-level package CRUD: `GET/POST/PUT/DELETE /api/groups/{group_id}/packages`
    - Host-level overrides: `GET/POST/PUT/DELETE /api/hosts/{host_id}/packages`
    - Effective packages: `GET /api/hosts/{host_id}/effective-packages`
    - Repository CRUD (group-only): `GET/POST/PUT/DELETE /api/groups/{group_id}/package-repos`
    - Effective repos: `GET /api/hosts/{host_id}/effective-repos`
  - Register router in `app/main.py` (import + `app.include_router(packages_router, prefix="/api")`)
  - Use `current_superuser` for all endpoints (system-impacting operations)
  - Catch `IntegrityError` on create/update → return 409 Conflict
  - Audit logging via `log_action` on all mutations
  - DELETE endpoints return `Response(status_code=204)`
  - Use `body.model_dump(exclude_unset=True)` for Update operations

  **Must NOT do**:
  - Do NOT use `current_active_user` (package ops are superuser-only)
  - Do NOT use `exclude_none` for Update dumps

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T1, T2, T3

  **References**:
  - `backend/app/api/cron_jobs.py:1-335` — Most recent CRUD pattern (superuser auth, IntegrityError handling, audit logging, 204 DELETE)
  - `backend/app/api/cron_jobs.py:65-72` — IntegrityError catch → 409
  - `backend/app/api/cron_jobs.py:110` — `exclude_unset=True` on Update
  - `backend/app/main.py:80-85` — Router registration pattern
  - `backend/app/audit/logger.py` — `log_action` import

  **QA Scenarios**:
  ```
  Scenario: CRUD operations work
    Tool: Bash (pytest)
    Steps:
      1. cd backend && pytest tests/test_packages.py -k TestPackageAPI -v
    Expected: All API tests pass (CRUD, protected rejection, 409 on duplicate)
    Evidence: .sisyphus/evidence/task-4-api.txt
  ```

  **Commit**: YES — `feat(api): add package + repository CRUD endpoints`

- [x] 5. Ansible Package Playbook Generator

  **What to do**:
  - Create `backend/app/packages/generator.py`:
    - `def generate_package_playbook(host_ip, packages: list, repos: list, ssh_key_path) -> dict`:
      - **Step 1** — Repos: For each repo with `state=present`:
        - apt repos: `ansible.builtin.apt_repository` (repo URL, key from key_url)
        - yum repos: `ansible.builtin.yum_repository` (baseurl, gpgkey)
      - **Step 2** — Packages: For each package:
        - `ansible.builtin.package` with `name: {package_name}={version}` (if version), `state: {state}`
        - For `latest`: `state: latest` without version
        - For `absent`: `state: absent`
      - Playbook: `become: true`, `gather_facts: true` (needed for `ansible_os_family` in repo tasks)

  **Must NOT do**:
  - Do NOT use `purge` parameter on package removal
  - Do NOT modify existing generators

  **Must NOT do**:
  - Do NOT use `purge` parameter on removal tasks
  - Do NOT modify existing generators

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 2 | Blocks: T7 | Blocked By: T2

  **References**:
  - `backend/app/cron/generator.py:1-89` — Most recent generator pattern (returns dict, used in sync task)
  - `backend/app/services/generator.py:1-48` — Service generator pattern (returns tuple of yaml+json)
  - `backend/app/ansible/inventory.py` — `generate_inventory` helper for consistent inventory format

  **QA Scenarios**:
  ```
  Scenario: Generator produces valid playbook
    Tool: Bash (pytest)
    Steps:
      1. cd backend && pytest tests/test_packages.py -k TestPackageGenerator -v
    Expected: Repos before packages, apt/yum format correct, gather_facts true
    Evidence: .sisyphus/evidence/task-5-generator.txt
  ```

  **Commit**: YES — `feat(ansible): add package playbook generator`

- [x] 6. Package Drift Collector + Diff

  **What to do**:
  - Create `backend/app/packages/collector.py`:
    - `async def collect_package_states(host_ip, ssh_port, private_key_pem, package_names: list[str]) -> list[dict]`:
      - Detect package manager: run `which dpkg` and `which rpm`
      - For dpkg (Debian/Ubuntu): `dpkg -l {pkg} 2>/dev/null` → parse status + version
      - For rpm (RHEL/Fedora): `rpm -q {pkg} 2>/dev/null` → parse version
      - Package not found → `state: absent`
      - Return: `[{"name": "nginx", "state": "present"|"absent", "version": "1.24.0-1"}]`
  - Create `backend/app/packages/diff.py`:
    - `PackageDiff` dataclass: `to_install`, `to_remove`, `to_upgrade`, `in_sync`
    - Compare by `package_name`: check state and version match
    - Version comparison: `latest` desired vs any installed = in_sync (if present). Version glob matching via `fnmatch`.

  **Must NOT do**:
  - Do NOT support `>=` version comparison — only exact and fnmatch glob
  - Do NOT import models or schemas (pure SSH logic + data structures)

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T7 | Blocked By: None (pure SSH logic, no model/schema imports)

  **References**:
  - `backend/app/services/collector.py:1-94` — Service collector pattern (asyncssh SSH commands, parse output)
  - `backend/app/services/diff.py:1-92` — Service diff pattern (dataclass, compare by name)
  - `backend/app/cron/collector.py:1-70` — Cron collector (SSH command, parse output)

  **QA Scenarios**:
  ```
  Scenario: Drift diff correctly identifies mismatches
    Tool: Bash (pytest)
    Steps:
      1. cd backend && pytest tests/test_packages.py -k TestPackageDiff -v
    Expected: present/absent detection, version mismatch, latest=any OK, glob matching
    Evidence: .sisyphus/evidence/task-6-diff.txt
  ```

  **Commit**: YES — `feat(packages): add drift collector (dpkg/rpm) + diff`

- [x] 7. Package Sync + Drift Celery Tasks + Combined API + Celery Routing

  **What to do**:
  - Create `backend/app/tasks/package_sync.py`:
    - `@celery_app.task(bind=True, name="app.tasks.package_sync.run_package_sync", queue="long_running")`
    - Merge effective packages + repos → generate playbook → ansible-runner → update SyncJob + host_module_status
    - `module_type="package"` on SyncJob and HostModuleStatus
    - SSH key to `/dev/shm/barricade-{job_id}.key`, cleanup in `finally:`
    - Use `asyncio.run()` for async operations
  - Create `backend/app/tasks/package_drift.py`:
    - `check_all_package_drift` periodic task
    - RedBeat schedule registration at module import (with try/except for test env)
    - Uses `settings.DRIFT_CHECK_INTERVAL_MINUTES`
  - Create `backend/app/api/package_sync.py` (BUNDLES sync + drift following cron pattern):
    - `router = APIRouter(prefix="/packages", tags=["package-sync"])`
    - `POST /api/packages/hosts/{host_id}/plan` — preview changes
    - `POST /api/packages/hosts/{host_id}/sync` — apply via Ansible
    - `POST /api/packages/groups/{group_id}/sync` — group-wide sync
    - `GET /api/packages/jobs/{job_id}` — job status
    - `POST /api/packages/hosts/{host_id}/drift-check` — trigger drift check
    - `PUT /api/packages/hosts/{host_id}/drift-settings` — enable/disable
    - Import `SyncJobResponse` from `app.api.sync`
  - Register router in `app/main.py`
  - Add Celery task routing in `tasks/__init__.py`:
    ```python
    "app.tasks.package_sync.*": {"queue": "long_running"},
    "app.tasks.package_drift.*": {"queue": "long_running"},
    ```

  **Must NOT do**:
  - Do NOT create separate drift API file (bundle with sync, following cron pattern)
  - Do NOT use `purge` in playbook tasks

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T3, T5, T6

  **References**:
  - `backend/app/tasks/cron_sync.py:1-197` — Most recent sync task pattern (Celery bind, asyncio.run, SSH key /dev/shm, finally cleanup)
  - `backend/app/tasks/cron_drift.py:1-110` — Drift task pattern (RedBeat schedule, check_all loop, HostModuleStatus query)
  - `backend/app/api/cron_sync.py:1-343` — Combined sync+drift API pattern (plan, sync, group sync, drift-check, drift-settings, job status)
  - `backend/app/api/sync.py` — `SyncJobResponse` import
  - `backend/app/tasks/__init__.py:26-27` — Celery task_routes dict (add package entries)

  **QA Scenarios**:
  ```
  Scenario: Sync task creates SyncJob with correct module_type
    Tool: Bash (pytest)
    Steps:
      1. cd backend && pytest tests/test_packages.py -k TestPackageSync -v
    Expected: SyncJob.module_type == "package", repos before packages in playbook
    Evidence: .sisyphus/evidence/task-7-sync.txt

  Scenario: Celery routing configured
    Tool: Bash
    Steps:
      1. cd backend && python -c "from app.tasks import celery_app; print(celery_app.conf.task_routes)" | grep package
    Expected: package_sync and package_drift routes present
    Evidence: .sisyphus/evidence/task-7-routing.txt
  ```

  **Commit**: YES — `feat(tasks): add package sync + drift tasks and combined API`

- [x] 9. Frontend — Group Packages Page + Host Detail Tab

  **What to do**:
  - Add TypeScript interfaces to `frontend/lib/types.ts`:
    - `PackageRule`, `PackageRepository`, `EffectivePackage`
  - Create `frontend/app/(dashboard)/groups/[id]/packages/page.tsx`:
    - Two sections: "Packages" table (CRUD) + "Repositories" table (CRUD)
    - Package create dialog: package_name, version (optional), state dropdown (present/absent/latest), package_manager dropdown
    - Repository create dialog: name, url, key_url, repo_type (apt/yum), distribution (shown when apt), components (shown when apt)
  - Add "Packages" tab to host detail page (`frontend/app/(dashboard)/hosts/[id]/page.tsx`):
    - Effective packages table (merged group + host) using `/api/hosts/{id}/effective-packages`
    - Host override CRUD using `/api/hosts/{id}/packages`
    - No repo management on host level (group-only)
  - Add "Manage Packages" navigation link to group detail page (`frontend/app/(dashboard)/groups/[id]/page.tsx`)

  **Must NOT do**:
  - Do NOT add repo management on host detail page (repos are group-only)
  - Do NOT add drag-and-drop reordering (no priority UI needed, unlike rules)

  **Recommended Agent Profile**: `visual-engineering`, **Skills**: [`frontend-ui-ux`]

  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T4, T7

  **References**:
  - `frontend/lib/types.ts:1-246` — Add new interfaces after existing types
  - `frontend/app/(dashboard)/groups/[id]/page.tsx:177-183` — Navigation links section (add "Manage Packages" link after cron link)
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx:81,804-812` — Tab buttons section (add "Packages" tab)
  - `frontend/app/(dashboard)/groups/[id]/rules/page.tsx` — Reference for group-level CRUD table pattern
  - `frontend/app/(dashboard)/groups/[id]/cron-jobs/page.tsx` — Reference for similar group module page

  **QA Scenarios**:
  ```
  Scenario: Frontend builds without errors
    Tool: Bash
    Steps:
      1. cd frontend && npm run build
    Expected: Exit code 0, no TypeScript errors
    Evidence: .sisyphus/evidence/task-9-build.txt

  Scenario: Group packages page accessible
    Tool: Playwright
    Steps:
      1. Navigate to /groups/1/packages
      2. Verify page loads with "Packages" and "Repositories" sections
      3. Screenshot page
    Expected: Both sections visible with empty tables
    Evidence: .sisyphus/evidence/task-9-group-packages.png
  ```

  **Commit**: YES — `feat(ui): add package management pages`

- [x] 10. pytest Suite

  **What to do**:
  - Create `backend/tests/test_packages.py`:
    - **TestPackageSchemas**: protected packages rejected (openssh-server, linux-image-5.x), valid names accepted, version format accepted
    - **TestPackageMerge**: group priority, host override, repo deduplication by URL
    - **TestPackageAPI**: CRUD, effective-packages, protected rejection, repo CRUD
    - **TestPackageDiff**: present/absent detection, version mismatch, latest=any version OK, glob matching
    - **TestPackageGenerator**: repos before packages in playbook, apt vs yum format

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T4, T7

  **References**:
  - `backend/tests/test_cron.py:1-301` — Most recent test pattern (class-based, @pytest.mark.asyncio, fixtures)
  - `backend/tests/conftest.py` — `superuser_client`, `db`, `create_group`, `create_host`, `create_ssh_key` fixtures

  **QA Scenarios**:
  ```
  Scenario: All tests pass
    Tool: Bash
    Steps:
      1. cd backend && pytest tests/test_packages.py -v
    Expected: 12+ tests, all passing
    Evidence: .sisyphus/evidence/task-10-tests.txt
  ```

  **Acceptance Criteria**:
  - [ ] 12+ tests, all passing
  - [ ] Protected package deny-list with wildcards tested
  - [ ] Multi-distro diff tested (dpkg + rpm)
  - [ ] Repo deduplication by (url, repo_type, distribution) tested

  **Commit**: YES — `test(packages): add package management test suite`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
- [x] F2. **Code Quality Review** — `unspecified-high`
- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright`)
- [x] F4. **Scope Fidelity Check** — `deep` — Verify: no purge, no kernel packages, no pip/npm, no source compilation.

---

## Commit Strategy

| Task | Message | Key Files |
|------|---------|-----------|
| T1 | `feat(models): add PackageRule and PackageRepository models` | `app/packages/models.py`, `alembic/versions/` |
| T2 | `feat(packages): add schemas, deny-list, repo schemas` | `app/packages/schemas.py`, `app/packages/constants.py` |
| T3 | `feat(packages): add merge engine` | `app/packages/merge.py` |
| T4 | `feat(api): add package + repository CRUD endpoints` | `app/api/packages.py`, `app/main.py` |
| T5 | `feat(ansible): add package playbook generator` | `app/packages/generator.py` |
| T6 | `feat(packages): add drift collector (dpkg/rpm) + diff` | `app/packages/collector.py`, `app/packages/diff.py` |
| T7 | `feat(tasks): add package sync + drift tasks and API` | `app/tasks/package_sync.py`, `app/tasks/package_drift.py`, `app/api/package_sync.py`, `app/tasks/__init__.py`, `app/main.py` |
| T9 | `feat(ui): add package management pages` | `frontend/groups/[id]/packages/`, `frontend/hosts/[id]/`, `frontend/lib/types.ts` |
| T10 | `test(packages): add test suite` | `backend/tests/test_packages.py` |

---

## Success Criteria

```bash
cd backend && pytest tests/test_packages.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```
