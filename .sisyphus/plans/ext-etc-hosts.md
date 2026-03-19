# /etc/hosts Management Extension — Centralized Host File Control

## TL;DR

> **Quick Summary**: Add `/etc/hosts` entry management to Barricade. Define hostname-to-IP mappings at the group level with per-host overrides. Barricade renders the full `/etc/hosts` file via Ansible template, detects drift by parsing the remote file, and preserves system entries (`127.0.0.1 localhost`, `::1 localhost`).
>
> **Deliverables**:
> - `HostsEntry` model + Alembic migration
> - Hosts entry CRUD API (group-level + host-level) with effective-config + rendered preview endpoints
> - Ansible playbook generator using `ansible.builtin.template` for atomic `/etc/hosts` writes
> - Drift detector via SSH (`cat /etc/hosts` → parse → diff)
> - Celery sync task + periodic drift task
> - Frontend: group hosts-entries page + host detail "Hosts File" tab
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
- **Assignment model**: Group-level defaults + per-host overrides. `HostsEntry` has nullable `group_id` and `host_id` with DB CHECK constraint.
- **Merge**: `ip_address` is the merge key. Host override = full record replacement. Higher-priority group wins on IP conflict.
- **Full-file management**: Render complete `/etc/hosts` via Ansible template (not lineinfile — avoids partial state). Validate with `grep -q '127.0.0.1 localhost'`.
- **System entries**: Always inject `127.0.0.1 localhost` and `::1 localhost ip6-localhost` as non-deletable system entries.
- **Independent sync**: Uses `host_module_status` table from Service Management.

### Safety
- System entries (`127.0.0.1 localhost`, `::1 localhost`) are always preserved — auto-injected, non-deletable
- Rendered file validated before deployment: must contain `127.0.0.1 localhost`
- IP address validated as valid IPv4 or IPv6
- Hostname validated: alphanumeric + hyphens + dots, max 253 chars

---

## Work Objectives

### Definition of Done
- [x] Group-level entries: CRUD on `/api/groups/{id}/hosts-entries`
- [x] Host-level overrides: CRUD on `/api/hosts/{id}/hosts-entries`
- [x] Effective config: `GET /api/hosts/{id}/effective-hosts-entries` merges group defaults + host overrides
- [x] Preview: `GET /api/hosts/{id}/hosts-file-preview` returns rendered `/etc/hosts` content
- [x] Plan: `POST /api/hosts-mgmt/hosts/{id}/plan` previews changes
- [x] Sync: `POST /api/hosts-mgmt/hosts/{id}/sync` applies via Ansible
- [x] Drift: `POST /api/hosts-mgmt/hosts/{id}/drift-check` detects mismatches
- [x] Frontend: `/groups/{id}/hosts-entries` page + "Hosts File" tab on host detail
- [x] Tests: 10+ tests

### Must Have
- `HostsEntry` model: `ip_address`, `hostname`, `aliases` (JSONB array), `comment`, `priority`, `is_system` (bool)
- DB CHECK constraint for group_id/host_id exclusivity
- IP validation (IPv4 and IPv6)
- Hostname RFC 952/1123 validation
- System entry auto-injection (localhost entries)
- System entries non-deletable (`is_system=True`)
- Full-file template rendering with validation
- Alembic migration (reversible)

### Must NOT Have (Guardrails)
- No `lineinfile` approach (full template only — atomic replacement)
- No DNS resolution or lookup functionality
- No modification to existing module code
- No management of `/etc/hostname` (only `/etc/hosts`)
- No GitOps integration

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── T1: HostsEntry model + Alembic migration [quick]
├── T2: Hosts entry schemas + validators [quick]
└── T3: Hosts entry merge engine + file renderer [unspecified-high]

Wave 2 (Backend — 3 parallel):
├── T4: Hosts entry CRUD API + effective-config + preview endpoints [unspecified-high]
├── T5: Ansible /etc/hosts template playbook generator [quick]
└── T6: Hosts file drift collector + parser [unspecified-high]

Wave 3 (Sync + Drift — 2 parallel):
├── T7: Hosts file sync Celery task + sync API endpoints [unspecified-high]
└── T8: Hosts file drift detection task + API endpoint [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group hosts-entries page + host detail tab [visual-engineering]
└── T10: pytest suite [unspecified-high]

Critical Path: T1 → T3 → T5 → T7 → T9
Max Concurrent: 3
```

---

## TODOs

- [x] 1. HostsEntry Model + Alembic Migration

  **What to do**:
  - Create `backend/app/hosts_mgmt/__init__.py` (empty)
  - Create `backend/app/hosts_mgmt/models.py`:
    - `HostsEntry` model:
      - `id`, `group_id` (FK nullable), `host_id` (FK nullable)
      - `ip_address` (String(45) — fits IPv6), `hostname` (String(253))
      - `aliases` (JSONB, default []), `comment` (Text, nullable)
      - `priority` (Integer, default 0), `is_system` (Boolean, default False)
      - `created_at`, `updated_at`
      - CHECK: `(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)`
  - Create Alembic migration

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 1 | Blocks: T3, T4, T7, T8 | Blocked By: None

  **References**:
  - `backend/app/services/models.py` — ServiceRule model pattern

  **Acceptance Criteria**:
  - [ ] Table created with CHECK constraint
  - [ ] `aliases` is JSONB with default `[]`
  - [ ] Migration reversible

  **Commit**: YES — `feat(models): add HostsEntry model for /etc/hosts management`

- [x] 2. Hosts Entry Schemas + Validators

  **What to do**:
  - Create `backend/app/hosts_mgmt/schemas.py`:
    - `HostsEntryCreate`: `ip_address` (str), `hostname` (str), `aliases` (list[str], default []), `comment` (str|None), `priority` (int, default 0)
      - Validator: `ip_address` must be valid IPv4 or IPv6 (use `ipaddress.ip_address()`)
      - Validator: `hostname` must match RFC 952/1123 pattern (alphanumeric + hyphens + dots, max 253 chars, labels max 63 chars)
      - Validator: each alias follows same hostname rules
    - `HostsEntryUpdate`: same fields, all optional
    - `HostsEntryResponse`: all fields + `id`, `group_id`, `host_id`, `is_system`, `created_at`, `updated_at`
    - `EffectiveHostsEntryResponse`: fields + `source` ("group"|"host"), `source_id`, `source_name`

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 1 | Blocks: T4, T5, T6 | Blocked By: None

  **References**:
  - `backend/app/services/schemas.py` — Schema pattern

  **Acceptance Criteria**:
  - [ ] Invalid IP rejected (e.g., `"not-an-ip"`)
  - [ ] IPv6 accepted (e.g., `"::1"`, `"fe80::1"`)
  - [ ] Hostname > 253 chars rejected
  - [ ] Hostname with spaces/special chars rejected

  **Commit**: YES — `feat(hosts_mgmt): add schemas and validators`

- [x] 3. Hosts Entry Merge Engine + File Renderer

  **What to do**:
  - Create `backend/app/hosts_mgmt/merge.py`:
    - `async def get_effective_hosts_entries(host_id, db) -> list[EffectiveHostsEntryResponse]`:
      - Same merge pattern: group priority → host override, key = `ip_address`
    - `def render_hosts_file(entries: list) -> str`:
      - Always start with system entries:
        ```
        # Managed by Barricade — do not edit manually
        127.0.0.1 localhost
        ::1 localhost ip6-localhost ip6-loopback
        ```
      - Then render each entry: `{ip} {hostname} {aliases joined by space}  # {comment}`
      - Sort entries: system first, then by IP

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 1 | Blocks: T4, T5, T7 | Blocked By: T1

  **References**:
  - `backend/app/services/merge.py` — Service merge pattern

  **Acceptance Criteria**:
  - [ ] System entries always present in rendered output
  - [ ] Host override replaces group entry for same IP
  - [ ] Rendered file is valid `/etc/hosts` format

  **Commit**: YES — `feat(hosts_mgmt): add merge engine and file renderer`

- [x] 4. Hosts Entry CRUD API + Preview Endpoint

  **What to do**:
  - Create `backend/app/api/hosts_entries.py`:
    - Group-level CRUD: `GET/POST/PUT/DELETE /api/groups/{group_id}/hosts-entries`
    - Host-level overrides: `GET/POST/PUT/DELETE /api/hosts/{host_id}/hosts-entries`
    - Effective config: `GET /api/hosts/{host_id}/effective-hosts-entries`
    - File preview: `GET /api/hosts/{host_id}/hosts-file-preview` → returns rendered `/etc/hosts` as plain text
    - Block deletion of `is_system=True` entries
  - Register router in `app/main.py`
  - Audit logging on all mutations

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 2 | Blocks: T7, T9 | Blocked By: T1, T2, T3

  **References**:
  - `backend/app/api/services.py` — Service CRUD pattern

  **Acceptance Criteria**:
  - [ ] All CRUD endpoints working
  - [ ] System entries cannot be deleted (403)
  - [ ] Preview returns valid `/etc/hosts` content
  - [ ] Effective-hosts-entries merges correctly

  **Commit**: YES — `feat(api): add hosts entry CRUD + preview endpoints`

- [x] 5. Ansible /etc/hosts Template Playbook Generator

  **What to do**:
  - Create `backend/app/hosts_mgmt/generator.py`:
    - `def generate_hosts_file_playbook(host_ip, rendered_content: str, ssh_key_path) -> dict`:
      - Task 1: `ansible.builtin.copy` with `content: {rendered_content}`, `dest: /etc/hosts`, `owner: root`, `group: root`, `mode: "0644"`, `validate: 'grep -q "127.0.0.1 localhost" %s'`
      - Playbook: `become: true`, `gather_facts: false`

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 2 | Blocks: T7 | Blocked By: T3

  **References**:
  - `backend/app/services/generator.py` — Service playbook pattern

  **Acceptance Criteria**:
  - [ ] Generated playbook uses `ansible.builtin.copy` with content
  - [ ] Validate command checks for localhost entry
  - [ ] `mode: "0644"`, `owner: root`

  **Commit**: YES — `feat(ansible): add /etc/hosts playbook generator`

- [x] 6. Hosts File Drift Collector + Parser

  **What to do**:
  - Create `backend/app/hosts_mgmt/collector.py`:
    - `async def collect_hosts_file(host_ip, ssh_port, private_key_pem) -> list[dict]`:
      - SSH into host, run `cat /etc/hosts`
      - Parse each non-comment, non-empty line: split into `ip`, `hostname`, `aliases`
      - Skip lines starting with `#`
      - Return list of parsed entries
  - Create `backend/app/hosts_mgmt/diff.py`:
    - `HostsFileDiff` dataclass: `entries_to_add`, `entries_to_remove`, `entries_to_update`, `entries_in_sync`
    - Compare by `ip_address` key: check hostname + aliases match

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 2 | Blocks: T7, T8 | Blocked By: T2

  **References**:
  - `backend/app/services/collector.py` — Service collector pattern

  **Acceptance Criteria**:
  - [ ] Parser handles comments, blank lines, tabs vs spaces
  - [ ] IPv6 entries parsed correctly
  - [ ] Multiple aliases per line parsed as array

  **Commit**: YES — `feat(hosts_mgmt): add drift collector and parser`

- [x] 7. Hosts File Sync Celery Task + Sync API

  **What to do**:
  - Create `backend/app/tasks/hosts_sync.py`:
    - Celery task: merge effective entries → render file → generate playbook → ansible-runner → update SyncJob + host_module_status
    - `module_type="hosts_file"` on SyncJob
  - Create `backend/app/api/hosts_sync.py`:
    - `POST /api/hosts-mgmt/hosts/{host_id}/plan` — preview diff
    - `POST /api/hosts-mgmt/hosts/{host_id}/sync` — trigger sync
    - `POST /api/hosts-mgmt/groups/{group_id}/sync` — sync all hosts in group
    - `GET /api/hosts-mgmt/jobs/{job_id}` — job status
  - Register router

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T4, T5, T6

  **References**:
  - `backend/app/tasks/service_sync.py` — Service sync pattern

  **Acceptance Criteria**:
  - [ ] Plan shows entries to add/remove/update
  - [ ] SyncJob created with `module_type="hosts_file"`
  - [ ] Rendered file always includes localhost entries

  **Commit**: YES — `feat(tasks): add hosts file sync Celery task + sync API`

- [x] 8. Hosts File Drift Detection Task + API

  **What to do**:
  - Create `backend/app/tasks/hosts_drift.py`:
    - Periodic drift check for hosts with `module_type="hosts_file"` drift enabled
  - Add drift endpoints:
    - `POST /api/hosts-mgmt/hosts/{host_id}/drift-check`
    - `PUT /api/hosts-mgmt/hosts/{host_id}/drift-settings`

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 3 | Blocks: T10 | Blocked By: T1, T6

  **References**:
  - `backend/app/tasks/service_drift.py` — Service drift pattern

  **Acceptance Criteria**:
  - [ ] Drift check compares rendered file vs actual file
  - [ ] Missing entries detected as drift
  - [ ] Extra (unmanaged) entries reported but not auto-removed

  **Commit**: YES — `feat(tasks): add hosts file drift detection task + API`

- [x] 9. Frontend — Group Hosts-Entries Page + Host Detail Tab

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/hosts-entries/page.tsx`:
    - Table: IP | Hostname | Aliases | Comment | Actions
    - Add/edit dialog, delete button (blocked for system entries)
  - Add "Hosts File" tab on host detail page:
    - Effective entries table (merged group + host)
    - "Preview File" button → modal showing rendered `/etc/hosts` content
    - Host override CRUD
  - Add "Hosts File" link on group detail page
  - TypeScript interfaces in `lib/types.ts`

  **Recommended Agent Profile**: `visual-engineering` + `frontend-ui-ux`

  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T4, T7

  **References**:
  - `frontend/app/(dashboard)/groups/[id]/services/page.tsx` — Service page pattern

  **Acceptance Criteria**:
  - [ ] Group page shows hosts entries CRUD
  - [ ] Host detail has "Hosts File" tab with effective entries
  - [ ] Preview modal shows rendered file content
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add /etc/hosts management pages`

- [x] 10. pytest Suite

  **What to do**:
  - Create `backend/tests/test_hosts_mgmt.py`:
    - **TestHostsSchemas**: IP validation (valid IPv4, valid IPv6, invalid rejected), hostname validation
    - **TestHostsRenderer**: system entries always present, entries sorted, aliases rendered
    - **TestHostsMerge**: group priority wins, host override replaces, effective annotated
    - **TestHostsAPI**: CRUD endpoints, system entry deletion blocked, preview returns valid content
    - **TestHostsDiff**: entries parsed from /etc/hosts format, missing entries detected

  **Recommended Agent Profile**: `unspecified-high`

  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T7, T8

  **Acceptance Criteria**:
  - [ ] 12+ tests, all passing
  - [ ] IP/hostname validation tested
  - [ ] System entry protection tested
  - [ ] File rendering tested

  **Commit**: YES — `test(hosts_mgmt): add /etc/hosts management test suite`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
- [x] F2. **Code Quality Review** — `unspecified-high`
- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright`)
- [x] F4. **Scope Fidelity Check** — `deep`

---

## Commit Strategy

| Task | Message | Key Files |
|------|---------|-----------|
| T1 | `feat(models): add HostsEntry model` | `app/hosts_mgmt/models.py`, `alembic/versions/` |
| T2 | `feat(hosts_mgmt): add schemas and validators` | `app/hosts_mgmt/schemas.py` |
| T3 | `feat(hosts_mgmt): add merge engine and file renderer` | `app/hosts_mgmt/merge.py` |
| T4 | `feat(api): add hosts entry CRUD + preview endpoints` | `app/api/hosts_entries.py` |
| T5 | `feat(ansible): add /etc/hosts playbook generator` | `app/hosts_mgmt/generator.py` |
| T6 | `feat(hosts_mgmt): add drift collector and parser` | `app/hosts_mgmt/collector.py`, `app/hosts_mgmt/diff.py` |
| T7 | `feat(tasks): add hosts file sync + API` | `app/tasks/hosts_sync.py`, `app/api/hosts_sync.py` |
| T8 | `feat(tasks): add hosts file drift detection + API` | `app/tasks/hosts_drift.py` |
| T9 | `feat(ui): add /etc/hosts management pages` | `frontend/groups/[id]/hosts-entries/`, `frontend/hosts/[id]/` |
| T10 | `test(hosts_mgmt): add test suite` | `backend/tests/test_hosts_mgmt.py` |

---

## Success Criteria

```bash
cd backend && pytest tests/test_hosts_mgmt.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```
