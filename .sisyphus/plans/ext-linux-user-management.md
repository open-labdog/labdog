# Linux User Management Extension — Remote User & Group Control

## TL;DR

> **Quick Summary**: Add Linux user and group management to Barricade. Define desired users (with SSH authorized_keys, sudo rules, group memberships) at the group level with per-host overrides. Barricade syncs via Ansible `builtin.user` + `posix.authorized_key`, detects drift via `getent passwd` + `authorized_keys` parsing.
>
> **Deliverables**:
> - `LinuxUser` + `LinuxGroup` models + Alembic migration
> - User/group CRUD API (group-level + host-level) with effective-config merge endpoint
> - Ansible playbook generator for `builtin.user` + `posix.authorized_key` + sudoers
> - Drift detector via SSH (`getent passwd`, `authorized_keys`, `groups`)
> - Celery sync task + periodic drift task
> - Frontend: group users page + host detail Users tab
> - pytest suite
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T3 → T5 → T7 → T9
>
> **Prerequisite**: Service Management extension (T1 creates `host_module_status` table and `SyncJob.module_type` column that this plan reuses)

---

## Context

### Original Request
Add Linux user and group management as the second Barricade extension module. Manage users, SSH authorized_keys, sudo rules, and supplementary groups across hosts using the Barricade plan-before-apply pattern.

### Architecture Decisions
- **Assignment model**: Same as Service Management — both group-level defaults AND per-host overrides. `LinuxUser`/`LinuxGroup` have nullable `group_id` and `host_id` with DB CHECK constraint.
- **Merge**: `username`/`groupname` is the merge key. Host override = full record replacement.
- **authorized_keys**: Full replacement when host overrides (not union). Only public keys stored.
- **Sudo**: Via `/etc/sudoers.d/{username}` drop-in files. `sudo_rule` validated against injection.
- **Independent sync**: User module syncs independently. Uses `host_module_status` table from Service Management.
- **Systemd dependency**: None (unlike services). Works on any Linux.

### Metis Review — Key Directives
- Protected user deny-list: `root`, `daemon`, `bin`, `sys`, `nobody`, `sshd`, `www-data`
- `uid >= 1000` enforced (no root UID creation)
- `sudo_rule` validated: no shell metacharacters, backticks, `$()` substitution
- DB CHECK constraint for group_id/host_id exclusivity
- Empty `authorized_keys` array `[]` = remove all keys (with validation warning)
- User deletion: Ansible `state=absent` with `remove=yes` (removes home dir)
- New top-level package `app/user_mgmt/`

### Security Model
- **Only public keys** in `authorized_keys` JSONB — never private keys
- **sudo_rule is a literal sudoers line** — no templating, no variables
- **uid/gid validation**: Must be ≥ 1000 (or null for auto-assign)
- **Protected users cannot be managed**: deny-list enforced at schema level

---

## Work Objectives

### Core Objective
Enable centralized Linux user/group management with authorized_keys deployment, sudo configuration, drift detection, and audit logging.

### Definition of Done
- [x] Group-level user/group rules: CRUD on `/api/groups/{id}/linux-users` and `/api/groups/{id}/linux-groups`
- [x] Host-level overrides: CRUD on `/api/hosts/{id}/linux-users` and `/api/hosts/{id}/linux-groups`
- [x] Effective config: `GET /api/hosts/{id}/effective-users` merges group defaults + host overrides
- [x] Plan: `POST /api/linux-users/hosts/{id}/plan` previews changes
- [x] Sync: `POST /api/linux-users/hosts/{id}/sync` applies via Ansible
- [x] Drift: `POST /api/linux-users/hosts/{id}/drift-check` detects mismatches
- [x] Audit: All user/group changes logged
- [x] Frontend: `/groups/{id}/users` page + Users tab on `/hosts/{id}`
- [x] Tests: 15+ tests covering CRUD, merge, drift, deny-list, sudo validation

### Must Have
- `LinuxUser` model: `username`, `uid` (nullable), `shell`, `home_dir`, `state` (present/absent), `sudo_rule` (nullable), `authorized_keys` (JSONB array of public key strings), `groups` (JSONB array of supplementary group names)
- `LinuxGroup` model: `groupname`, `gid` (nullable), `state` (present/absent)
- Both models: DB CHECK constraint for group_id/host_id exclusivity
- Protected user deny-list constant
- `uid >= 1000` validation (or null for auto-assign)
- `sudo_rule` injection validation (reject metacharacters)
- Ansible playbook: `builtin.user` + `posix.authorized_key` + sudoers.d file deploy
- SSH drift collector: `getent passwd`, `cat ~/.ssh/authorized_keys`, `groups {user}`, `cat /etc/sudoers.d/{user}`
- Host override = full record replacement by `username`/`groupname` merge key
- Alembic migration (reversible)
- Superuser-only access on all endpoints

### Must NOT Have (Guardrails)
- No password/hash management — authorized_keys only
- No PAM configuration
- No SSH daemon config changes
- No home directory templating (skeleton files)
- No user quota or resource limit management
- No UID global uniqueness across hosts
- No GitOps integration for this module
- No modification to existing firewall or service module code
- No management of system users (root, daemon, etc.)
- No `sudo_rule` templating or variables

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + pytest-asyncio + httpx

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── T1: LinuxUser + LinuxGroup models + Alembic migration [unspecified-high]
├── T2: User/group schemas + deny-list + sudo validator [unspecified-high]
└── T3: User merge engine (group priority + host override) [unspecified-high]

Wave 2 (Backend — 3 parallel):
├── T4: User/group CRUD API + effective-config endpoints [unspecified-high]
├── T5: Ansible user playbook generator (user + authorized_key + sudoers) [unspecified-high]
└── T6: User drift collector (getent + authorized_keys + sudoers SSH) [unspecified-high]

Wave 3 (Sync + Drift — 2 parallel):
├── T7: User sync Celery task + sync API endpoints [unspecified-high]
└── T8: User drift detection task + API endpoint [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group users page + host detail Users tab [visual-engineering]
└── T10: pytest suite for user management [unspecified-high]

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

---

## TODOs

- [x] 1. LinuxUser + LinuxGroup Models + Alembic Migration

  **What to do**:
  - Create `backend/app/user_mgmt/__init__.py` (empty)
  - Create `backend/app/user_mgmt/models.py`:
    - `UserState` enum: `present`, `absent`
    - `LinuxUser` model:
      - `id`, `group_id` (FK nullable), `host_id` (FK nullable)
      - `username` (String(32), NOT NULL), `uid` (Integer, nullable — auto-assign if null)
      - `shell` (String(100), default "/bin/bash"), `home_dir` (String(200), nullable — default /home/{username})
      - `state` (Enum UserState, default present), `comment` (Text, nullable)
      - `sudo_rule` (Text, nullable — literal sudoers line)
      - `authorized_keys` (JSONB, default []), `supplementary_groups` (JSONB, default [])
      - `priority` (Integer, default 0), `created_at`, `updated_at`
      - CHECK: `(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)`
    - `LinuxGroup` model:
      - `id`, `group_id` (FK nullable), `host_id` (FK nullable)
      - `groupname` (String(32), NOT NULL), `gid` (Integer, nullable)
      - `state` (Enum UserState — reuse present/absent), `priority` (Integer, default 0)
      - `created_at`, `updated_at`
      - Same CHECK constraint
  - Create Alembic migration

  **Must NOT do**:
  - Do NOT add password/hash columns
  - Do NOT modify existing models

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 1 (with T2, T3) | Blocks: T3, T4, T7, T8 | Blocked By: None

  **References**:
  - `backend/app/services/models.py` — ServiceRule model pattern (from service management extension)
  - `backend/app/models/firewall_rule.py` — Base model pattern

  **Acceptance Criteria**:
  - [ ] Both tables created with CHECK constraints
  - [ ] `authorized_keys` and `supplementary_groups` are JSONB with default `[]`
  - [ ] Migration reversible

  **Commit**: YES — `feat(models): add LinuxUser and LinuxGroup models`

- [x] 2. User/Group Schemas + Deny-List + Sudo Validator

  **What to do**:
  - Create `backend/app/user_mgmt/constants.py`:
    - `PROTECTED_USERS`: frozenset `{"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody", "sshd", "systemd-network", "systemd-resolve", "messagebus", "polkitd"}`
    - `PROTECTED_GROUPS`: frozenset `{"root", "daemon", "bin", "sys", "adm", "tty", "disk", "lp", "mail", "news", "uucp", "man", "proxy", "kmem", "dialout", "fax", "voice", "cdrom", "floppy", "tape", "sudo", "audio", "dip", "www-data", "backup", "operator", "list", "irc", "src", "gnats", "shadow", "utmp", "video", "sasl", "plugdev", "staff", "games", "users", "nogroup", "wheel", "sshd"}`
    - `SUDO_FORBIDDEN_PATTERNS`: regex patterns for injection: `` r"[`$();|&<>]" ``
  - Create `backend/app/user_mgmt/schemas.py`:
    - `LinuxUserCreate`: `username`, `uid` (int|None), `shell` (default /bin/bash), `home_dir` (str|None), `state` (present/absent), `sudo_rule` (str|None), `authorized_keys` (list[str], default []), `supplementary_groups` (list[str], default []), `priority` (int, default 0), `comment` (str|None)
      - Validators: reject protected usernames, reject uid < 1000 (if provided), validate sudo_rule against injection, validate each authorized_key starts with valid key type (`ssh-rsa`, `ssh-ed25519`, `ecdsa-sha2-nistp*`, `ssh-dss`)
    - `LinuxGroupCreate`: `groupname`, `gid` (int|None), `state` (present/absent), `priority` (int, default 0)
      - Validators: reject protected groupnames, reject gid < 1000 (if provided)
    - Response models for both

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 1 (with T1, T3) | Blocks: T4, T5, T6 | Blocked By: None

  **References**:
  - `backend/app/services/schemas.py` — Schema pattern from service management
  - `backend/app/services/constants.py` — Constants pattern

  **Acceptance Criteria**:
  - [ ] Protected users rejected (root, sshd, etc.)
  - [ ] `uid < 1000` rejected, `uid=None` accepted
  - [ ] `sudo_rule` with backticks/pipes/semicolons rejected
  - [ ] `authorized_keys` entries validated as SSH public key format

  **Commit**: YES — `feat(user_mgmt): add schemas, deny-lists, sudo validator`

- [x] 3. User Merge Engine

  **What to do**:
  - Create `backend/app/user_mgmt/merge.py`:
    - `async def get_effective_users(host_id, db) -> list[EffectiveLinuxUserResponse]`:
      - Same merge pattern as service management: group priority → host override
      - Merge key: `username`
      - Host override = full record replacement
    - `async def get_effective_groups(host_id, db) -> list[EffectiveLinuxGroupResponse]`:
      - Same pattern, merge key: `groupname`

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 1 (with T1, T2) | Blocks: T4, T7 | Blocked By: T1

  **References**:
  - `backend/app/services/merge.py` — Service merge engine (same pattern)

  **Acceptance Criteria**:
  - [ ] Higher-priority group wins on username conflict
  - [ ] Host override replaces entire user record
  - [ ] Source annotation (group/host) correct

  **Commit**: YES — `feat(user_mgmt): add merge engine with host-override support`

- [x] 4. User/Group CRUD API + Effective-Config Endpoints

  **What to do**:
  - Create `backend/app/api/linux_users.py` with router:
    - Group-level CRUD: `GET/POST/PUT/DELETE /api/groups/{group_id}/linux-users`
    - Host-level overrides: `GET/POST/PUT/DELETE /api/hosts/{host_id}/linux-users`
    - Effective config: `GET /api/hosts/{host_id}/effective-users`
  - Create `backend/app/api/linux_groups.py` with router:
    - Same pattern for linux groups
    - Effective config: `GET /api/hosts/{host_id}/effective-groups`
  - Register both routers in `app/main.py`
  - Audit logging on all mutations

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 2 (with T5, T6) | Blocks: T7, T9 | Blocked By: T1, T2, T3

  **References**:
  - `backend/app/api/services.py` — Service CRUD pattern (from service management extension)

  **Acceptance Criteria**:
  - [ ] All CRUD endpoints registered and responding
  - [ ] Protected users/groups rejected with 422
  - [ ] Effective-users merges group + host correctly
  - [ ] Audit log entries created on mutations

  **Commit**: YES — `feat(api): add Linux user/group CRUD + effective-config endpoints`

- [x] 5. Ansible User Playbook Generator

  **What to do**:
  - Create `backend/app/user_mgmt/generator.py`:
    - `def generate_user_playbook(host_ip, users: list, groups: list, ssh_key_path) -> dict`:
      - Step 1: Create/remove Linux groups via `ansible.builtin.group`
      - Step 2: Create/remove users via `ansible.builtin.user` (name, uid, shell, home, groups, state)
      - Step 3: Deploy authorized_keys via `ansible.posix.authorized_key` (user, key, exclusive=True)
      - Step 4: Deploy sudo rules via `ansible.builtin.copy` to `/etc/sudoers.d/{username}` with `validate: 'visudo -cf %s'`
      - Step 5: Remove sudoers file for users with `state=absent` or `sudo_rule=None`
    - Order matters: groups before users (user may reference group)

  **Must NOT do**:
  - Do NOT deploy private keys
  - Do NOT edit `/etc/sudoers` directly (use `/etc/sudoers.d/` drop-ins)
  - Do NOT use `force: yes` on user deletion (let Ansible fail if user has active sessions)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 2 (with T4, T6) | Blocks: T7 | Blocked By: T2

  **References**:
  - `backend/app/services/generator.py` — Service playbook pattern
  - `backend/app/ansible/generator.py` — Firewall playbook pattern (do not modify)

  **Acceptance Criteria**:
  - [ ] Groups created before users in playbook ordering
  - [ ] `authorized_key` uses `exclusive: true` (replaces all keys)
  - [ ] Sudoers file validated with `visudo -cf %s`
  - [ ] Absent users have sudoers file removed

  **Commit**: YES — `feat(ansible): add user/group playbook generator`

- [x] 6. User Drift Collector

  **What to do**:
  - Create `backend/app/user_mgmt/collector.py`:
    - `async def collect_user_states(host_ip, ssh_port, private_key_pem, usernames: list[str]) -> list[dict]`:
      - For each user: `getent passwd {username}` → parse uid, shell, home, groups
      - `cat /home/{user}/.ssh/authorized_keys 2>/dev/null` → parse public keys
      - `cat /etc/sudoers.d/{user} 2>/dev/null` → parse sudo rule
      - `groups {user}` → parse supplementary groups
      - User not found → `state: absent`
    - `async def collect_group_states(host_ip, ssh_port, private_key_pem, groupnames: list[str]) -> list[dict]`:
      - `getent group {groupname}` → parse gid, members
      - Group not found → `state: absent`
  - Create `backend/app/user_mgmt/diff.py`:
    - `UserDiff` dataclass: `users_to_add`, `users_to_remove`, `users_to_update`, `users_in_sync`
    - `GroupDiff` dataclass: similar
    - Compare: match by username/groupname, check all fields

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 2 (with T4, T5) | Blocks: T7, T8 | Blocked By: T2

  **References**:
  - `backend/app/services/collector.py` — Service collector pattern

  **Acceptance Criteria**:
  - [ ] Users parsed correctly from `getent passwd`
  - [ ] Missing user → `state: absent`
  - [ ] Authorized keys parsed as list of strings
  - [ ] Sudo rules read from `/etc/sudoers.d/`

  **Commit**: YES — `feat(user_mgmt): add drift collector + diff engine`

- [x] 7. User Sync Celery Task + Sync API

  **What to do**:
  - Create `backend/app/tasks/user_sync.py`:
    - Celery task: DB lookup → merge effective users/groups → generate playbook → ansible-runner → update SyncJob + host_module_status
    - `module_type="linux_user"` on SyncJob
  - Create `backend/app/api/user_sync.py`:
    - `POST /api/linux-users/hosts/{host_id}/plan` — preview changes
    - `POST /api/linux-users/hosts/{host_id}/sync` — trigger sync
    - `POST /api/linux-users/groups/{group_id}/sync` — sync all hosts in group
    - `GET /api/linux-users/jobs/{job_id}` — job status
  - Register router in `app/main.py`

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 3 (with T8) | Blocks: T9, T10 | Blocked By: T4, T5, T6

  **References**:
  - `backend/app/tasks/service_sync.py` — Service sync pattern (from service management extension)

  **Acceptance Criteria**:
  - [ ] Plan shows user/group diff
  - [ ] SyncJob created with `module_type="linux_user"`
  - [ ] Groups synced before users in playbook

  **Commit**: YES — `feat(tasks): add user sync Celery task + sync API`

- [x] 8. User Drift Detection Task + API

  **What to do**:
  - Create `backend/app/tasks/user_drift.py`:
    - Periodic drift check for hosts with `module_type="linux_user"` drift enabled
    - Updates `host_module_status`
  - Add drift endpoints to `backend/app/api/user_sync.py`:
    - `POST /api/linux-users/hosts/{host_id}/drift-check`
    - `PUT /api/linux-users/hosts/{host_id}/drift-settings`

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 3 (with T7) | Blocks: T10 | Blocked By: T1, T6

  **References**:
  - `backend/app/tasks/service_drift.py` — Service drift pattern

  **Acceptance Criteria**:
  - [ ] Drift check compares user state, authorized_keys, sudo rules, groups
  - [ ] `host_module_status` updated after check

  **Commit**: YES — `feat(tasks): add user drift detection task + API`

- [x] 9. Frontend — Group Users Page + Host Detail Users Tab

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/users/page.tsx`:
    - Table of Linux users/groups for the group (CRUD)
    - User create dialog: username, uid, shell, state, authorized_keys (textarea, one per line), supplementary groups (multi-select or tags), sudo_rule
    - Group create dialog: groupname, gid, state
  - Modify host detail page to add "Users" tab (alongside "Services" tab from service management):
    - Shows effective users (merged group + host)
    - Each row: username, uid, shell, state, key count, sudo (yes/no), source
    - Host override CRUD
  - Add "Users" link on group detail page
  - Add TypeScript interfaces in `frontend/lib/types.ts`

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**: Wave 4 (with T10) | Blocks: F1-F4 | Blocked By: T4, T7

  **References**:
  - `frontend/app/(dashboard)/groups/[id]/services/page.tsx` — Service management page pattern
  - Host detail page with tab system (from service management)

  **Acceptance Criteria**:
  - [ ] Group users page shows CRUD with all user fields
  - [ ] Host detail "Users" tab shows effective users with source
  - [ ] authorized_keys displayed as count badge, expandable
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add Linux user management pages`

- [x] 10. User Management Test Suite

  **What to do**:
  - Create `backend/tests/test_user_mgmt.py`:
    - **TestUserSchemas**: protected users rejected, uid < 1000 rejected, sudo injection rejected, valid authorized_key accepted, invalid key rejected
    - **TestUserMerge**: group priority wins, host override replaces, effective annotated
    - **TestUserAPI**: CRUD endpoints, effective-users, protected rejection, audit logging
    - **TestUserDiff**: user present/absent detection, authorized_keys diff, sudo rule diff
    - **TestLinuxGroupSchemas**: protected groups rejected, gid < 1000 rejected

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 4 (with T9) | Blocks: F1-F4 | Blocked By: T7, T8

  **References**:
  - `backend/tests/test_services.py` — Service test pattern

  **Acceptance Criteria**:
  - [ ] 15+ tests, all passing
  - [ ] Protected users, sudo injection, authorized_keys format all tested
  - [ ] Merge with host overrides tested
  - [ ] Diff detection tested

  **Commit**: YES — `test(user_mgmt): add user management test suite`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Verify all Must Have items. Check deny-lists. Verify sudo validation. Verify authorized_keys format validation. Check CHECK constraints.

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run pytest + ruff. Verify no modifications to firewall/service code. Migration reversible. No secrets in code.

- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill)
  Create Linux user via group page, add authorized_keys, add sudo rule, verify effective config on host detail, trigger sync preview.

- [x] F4. **Scope Fidelity Check** — `deep`
  Verify no password management. No PAM. No SSH daemon config. No home templating. No modifications to firewall/service files.

---

## Commit Strategy

| Task | Message | Key Files |
|------|---------|-----------|
| T1 | `feat(models): add LinuxUser and LinuxGroup models` | `app/user_mgmt/models.py`, `alembic/versions/` |
| T2 | `feat(user_mgmt): add schemas, deny-lists, sudo validator` | `app/user_mgmt/schemas.py`, `app/user_mgmt/constants.py` |
| T3 | `feat(user_mgmt): add merge engine with host-override support` | `app/user_mgmt/merge.py` |
| T4 | `feat(api): add Linux user/group CRUD + effective-config endpoints` | `app/api/linux_users.py`, `app/api/linux_groups.py` |
| T5 | `feat(ansible): add user/group playbook generator` | `app/user_mgmt/generator.py` |
| T6 | `feat(user_mgmt): add drift collector + diff engine` | `app/user_mgmt/collector.py`, `app/user_mgmt/diff.py` |
| T7 | `feat(tasks): add user sync Celery task + sync API` | `app/tasks/user_sync.py`, `app/api/user_sync.py` |
| T8 | `feat(tasks): add user drift detection task + API` | `app/tasks/user_drift.py` |
| T9 | `feat(ui): add Linux user management pages` | `frontend/groups/[id]/users/`, `frontend/hosts/[id]/` |
| T10 | `test(user_mgmt): add user management test suite` | `backend/tests/test_user_mgmt.py` |

---

## Success Criteria

### Verification Commands
```bash
cd backend && pytest tests/test_user_mgmt.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```

### Final Checklist
- [x] All "Must Have" present
- [x] All "Must NOT Have" absent
- [x] All tests pass
- [x] Frontend builds clean
- [x] Migration reversible
- [x] No password/hash management anywhere
- [x] Sudo rules validated against injection
