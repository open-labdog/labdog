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
- **Version pinning**: Optional `version` field. Formats: `"1.24.*"` (glob), `">=1.24"`, `"1.24.1-1ubuntu1"` (exact). Passed directly to package manager — no Barricade-side parsing.
- **Repository management**: Separate model, group-level only. Repos are synced BEFORE packages (dependency ordering in playbook).

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
- `PackageRule` model: `package_name`, `version` (nullable), `state` (present/absent/latest), `package_manager` (enum: apt/dnf/yum/auto, default auto), `priority`, `comment`
- `PackageRepository` model: `name`, `url`, `key_url` (nullable), `repo_type` (enum: apt/yum), `distribution` (nullable — for apt), `components` (nullable — for apt), `state` (present/absent)
- Both models: DB CHECK constraint for group_id/host_id on PackageRule; group_id only on PackageRepository
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
- No modification to existing module code
- No GitOps integration

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── T1: PackageRule + PackageRepository models + Alembic migration [unspecified-high]
├── T2: Package schemas + deny-list + repository schemas [unspecified-high]
└── T3: Package merge engine (group priority + host override) [unspecified-high]

Wave 2 (Backend — 3 parallel):
├── T4: Package CRUD + Repository CRUD + effective-config APIs [unspecified-high]
├── T5: Ansible package playbook generator (repos → packages) [unspecified-high]
└── T6: Package drift collector (dpkg/rpm) + diff [unspecified-high]

Wave 3 (Sync + Drift — 2 parallel):
├── T7: Package sync Celery task + sync API [unspecified-high]
└── T8: Package drift detection task + API [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group packages page + host detail tab [visual-engineering]
└── T10: pytest suite [unspecified-high]

Critical Path: T1 → T4 → T7 → T9
Max Concurrent: 3
```

---

## TODOs

- [ ] 1. PackageRule + PackageRepository Models + Alembic Migration

  **What to do**:
  - Create `backend/app/packages/__init__.py` (empty)
  - Create `backend/app/packages/models.py`:
    - `PackageState` enum: `present`, `absent`, `latest`
    - `PackageManager` enum: `apt`, `dnf`, `yum`, `auto`
    - `RepoType` enum: `apt`, `yum`
    - `PackageRule` model:
      - `id`, `group_id` (FK nullable), `host_id` (FK nullable)
      - `package_name` (String(200)), `version` (String(100), nullable)
      - `state` (Enum PackageState, default present), `package_manager` (Enum PackageManager, default auto)
      - `priority` (Integer, default 0), `comment` (Text, nullable)
      - `created_at`, `updated_at`
      - CHECK: group_id/host_id exclusivity
    - `PackageRepository` model:
      - `id`, `group_id` (FK, NOT NULL — repos are group-only, no host override)
      - `name` (String(100)), `url` (String(500)), `key_url` (String(500), nullable)
      - `repo_type` (Enum RepoType), `distribution` (String(100), nullable), `components` (String(200), nullable)
      - `state` (Enum — reuse present/absent), `created_at`, `updated_at`
  - Create Alembic migration

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T3, T4, T7, T8 | Blocked By: None

  **References**:
  - `backend/app/services/models.py` — ServiceRule model pattern

  **Acceptance Criteria**:
  - [ ] PackageRule table with CHECK constraint
  - [ ] PackageRepository table with non-nullable group_id (no host override)
  - [ ] Migration reversible

  **Commit**: YES — `feat(models): add PackageRule and PackageRepository models`

- [ ] 2. Package Schemas + Deny-List + Repository Schemas

  **What to do**:
  - Create `backend/app/packages/constants.py`:
    - `PROTECTED_PACKAGES`: frozenset `{"openssh-server", "openssh-client", "sshd", "systemd", "linux-image*", "linux-headers*", "kernel", "kernel-core", "glibc", "libc6", "libc-bin", "coreutils", "bash", "init", "grub", "grub2"}`
    - Note: use `fnmatch` for wildcard matching (`linux-image*`)
  - Create `backend/app/packages/schemas.py`:
    - `PackageRuleCreate`: `package_name`, `version` (str|None), `state` (present/absent/latest), `package_manager` (auto/apt/dnf/yum, default auto), `priority`, `comment`
      - Validator: reject protected packages (with fnmatch for wildcards)
      - Validator: `package_name` alphanumeric + hyphens + dots + underscores + colons (for arch like `nginx:amd64`)
    - `PackageRepositoryCreate`: `name`, `url`, `key_url` (str|None), `repo_type` (apt/yum), `distribution` (str|None, required if apt), `components` (str|None), `state`
      - Validator: `url` must start with `https://` or `http://` (warn on http)
    - Response models for both

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T4, T5, T6 | Blocked By: None

  **References**:
  - `backend/app/services/schemas.py` — Schema pattern

  **Acceptance Criteria**:
  - [ ] Protected packages rejected (openssh-server, systemd, linux-image-5.15)
  - [ ] Wildcard matching works (`linux-image-5.15.0-generic` matches `linux-image*`)
  - [ ] Valid package names accepted (nginx, libssl-dev, python3.12)

  **Commit**: YES — `feat(packages): add schemas, deny-list, repo schemas`

- [ ] 3. Package Merge Engine

  **What to do**:
  - Create `backend/app/packages/merge.py`:
    - `async def get_effective_packages(host_id, db) -> list[EffectivePackageResponse]`:
      - Group priority merge (key = `package_name`) + host override
    - `async def get_effective_repos(host_id, db) -> list[PackageRepositoryResponse]`:
      - Collect repos from all groups the host belongs to (no merge conflicts — repos are additive, deduplicated by `url`)

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T4, T7 | Blocked By: T1

  **References**:
  - `backend/app/services/merge.py` — Service merge pattern

  **Acceptance Criteria**:
  - [ ] Package merge: higher-priority group wins
  - [ ] Host override replaces group entry
  - [ ] Repos: deduplicated by URL across groups (additive)

  **Commit**: YES — `feat(packages): add merge engine`

- [ ] 4. Package + Repository CRUD API

  **What to do**:
  - Create `backend/app/api/packages.py`:
    - Group-level package CRUD: `GET/POST/PUT/DELETE /api/groups/{group_id}/packages`
    - Host-level overrides: `GET/POST/PUT/DELETE /api/hosts/{host_id}/packages`
    - Effective packages: `GET /api/hosts/{host_id}/effective-packages`
    - Repository CRUD (group-only): `GET/POST/PUT/DELETE /api/groups/{group_id}/package-repos`
    - Effective repos: `GET /api/hosts/{host_id}/effective-repos`
  - Register router in `app/main.py`
  - Audit logging on mutations

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 2 | Blocks: T7, T9 | Blocked By: T1, T2, T3

  **References**:
  - `backend/app/api/services.py` — Service CRUD pattern

  **Acceptance Criteria**:
  - [ ] Package CRUD + effective endpoint working
  - [ ] Repository CRUD (group-only) working
  - [ ] Protected packages rejected with 422
  - [ ] Audit entries created

  **Commit**: YES — `feat(api): add package + repository CRUD endpoints`

- [ ] 5. Ansible Package Playbook Generator

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

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 2 | Blocks: T7 | Blocked By: T2

  **References**:
  - `backend/app/services/generator.py` — Service playbook pattern

  **Acceptance Criteria**:
  - [ ] Repos added before packages in playbook ordering
  - [ ] apt and yum repos handled correctly
  - [ ] Version pinning: `nginx=1.24.*` format for apt, `nginx-1.24.*` for yum
  - [ ] `gather_facts: true` (needed for OS family detection)

  **Commit**: YES — `feat(ansible): add package playbook generator`

- [ ] 6. Package Drift Collector + Diff

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

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 2 | Blocks: T7, T8 | Blocked By: T2

  **References**:
  - `backend/app/services/collector.py` — Service collector pattern

  **Acceptance Criteria**:
  - [ ] dpkg output parsed correctly (status + version)
  - [ ] rpm output parsed correctly
  - [ ] Missing package → `state: absent`
  - [ ] Version glob matching: `"1.24.*"` matches `"1.24.0-1ubuntu1"`

  **Commit**: YES — `feat(packages): add drift collector (dpkg/rpm) + diff`

- [ ] 7. Package Sync Celery Task + Sync API

  **What to do**:
  - Create `backend/app/tasks/package_sync.py`:
    - Celery task: merge effective packages + repos → generate playbook → ansible-runner → update SyncJob + host_module_status
    - `module_type="package"` on SyncJob
  - Create `backend/app/api/package_sync.py`:
    - `POST /api/packages/hosts/{host_id}/plan`
    - `POST /api/packages/hosts/{host_id}/sync`
    - `POST /api/packages/groups/{group_id}/sync`
    - `GET /api/packages/jobs/{job_id}`
  - Register router

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T4, T5, T6

  **Acceptance Criteria**:
  - [ ] Plan shows packages to install/remove/upgrade
  - [ ] SyncJob created with `module_type="package"`
  - [ ] Repos synced before packages

  **Commit**: YES — `feat(tasks): add package sync Celery task + sync API`

- [ ] 8. Package Drift Detection Task + API

  **What to do**:
  - Create `backend/app/tasks/package_drift.py`
  - Add drift endpoints:
    - `POST /api/packages/hosts/{host_id}/drift-check`
    - `PUT /api/packages/hosts/{host_id}/drift-settings`

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 3 | Blocks: T10 | Blocked By: T1, T6

  **Acceptance Criteria**:
  - [ ] Missing packages detected as drift
  - [ ] Wrong version detected (when pinned)
  - [ ] `latest` desired + any version installed = in_sync

  **Commit**: YES — `feat(tasks): add package drift detection + API`

- [ ] 9. Frontend — Group Packages Page + Host Detail Tab

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/packages/page.tsx`:
    - Two sections: "Packages" table (CRUD) + "Repositories" table (CRUD)
    - Package create dialog: package_name, version (optional), state (present/absent/latest), package_manager
    - Repository create dialog: name, url, key_url, repo_type (apt/yum), distribution, components
  - Add "Packages" tab on host detail page:
    - Effective packages table (merged group + host)
    - Host override CRUD
    - No repo management on host level (group-only)
  - Add "Packages" link on group detail page
  - TypeScript interfaces

  **Recommended Agent Profile**: `visual-engineering` + `frontend-ui-ux`

  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T4, T7

  **Acceptance Criteria**:
  - [ ] Group page has packages + repos tables
  - [ ] Host detail has "Packages" tab
  - [ ] Version field optional with placeholder "latest"
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add package management pages`

- [ ] 10. pytest Suite

  **What to do**:
  - Create `backend/tests/test_packages.py`:
    - **TestPackageSchemas**: protected packages rejected (openssh-server, linux-image-5.x), valid names accepted, version format accepted
    - **TestPackageMerge**: group priority, host override, repo deduplication by URL
    - **TestPackageAPI**: CRUD, effective-packages, protected rejection, repo CRUD
    - **TestPackageDiff**: present/absent detection, version mismatch, latest=any version OK, glob matching
    - **TestPackageGenerator**: repos before packages in playbook, apt vs yum format

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T7, T8

  **Acceptance Criteria**:
  - [ ] 12+ tests, all passing
  - [ ] Protected package deny-list with wildcards tested
  - [ ] Multi-distro diff tested (dpkg + rpm)
  - [ ] Repo deduplication tested

  **Commit**: YES — `test(packages): add package management test suite`

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`
- [ ] F2. **Code Quality Review** — `unspecified-high`
- [ ] F3. **Real Manual QA** — `unspecified-high` (+ `playwright`)
- [ ] F4. **Scope Fidelity Check** — `deep` — Verify: no purge, no kernel packages, no pip/npm, no source compilation.

---

## Commit Strategy

| Task | Message | Key Files |
|------|---------|-----------|
| T1 | `feat(models): add PackageRule and PackageRepository models` | `app/packages/models.py`, `alembic/versions/` |
| T2 | `feat(packages): add schemas, deny-list, repo schemas` | `app/packages/schemas.py`, `app/packages/constants.py` |
| T3 | `feat(packages): add merge engine` | `app/packages/merge.py` |
| T4 | `feat(api): add package + repository CRUD endpoints` | `app/api/packages.py` |
| T5 | `feat(ansible): add package playbook generator` | `app/packages/generator.py` |
| T6 | `feat(packages): add drift collector (dpkg/rpm) + diff` | `app/packages/collector.py`, `app/packages/diff.py` |
| T7 | `feat(tasks): add package sync + API` | `app/tasks/package_sync.py`, `app/api/package_sync.py` |
| T8 | `feat(tasks): add package drift detection + API` | `app/tasks/package_drift.py` |
| T9 | `feat(ui): add package management pages` | `frontend/groups/[id]/packages/`, `frontend/hosts/[id]/` |
| T10 | `test(packages): add test suite` | `backend/tests/test_packages.py` |

---

## Success Criteria

```bash
cd backend && pytest tests/test_packages.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```
