# DNS Resolver Configuration Extension — Centralized DNS Settings

## TL;DR

> **Quick Summary**: Add DNS resolver management to Barricade. Define nameservers, search domains, and resolver options at the group level with per-host overrides. Supports three resolver backends: direct `/etc/resolv.conf`, `systemd-resolved`, and `NetworkManager`. Barricade syncs via Ansible templates, detects drift by comparing actual resolver state against desired config.
>
> **Deliverables**:
> - `ResolverConfig` model + Alembic migration
> - Resolver CRUD API (group-level + host-level) with effective-config endpoint
> - Ansible playbook generator with backend-specific template rendering (resolv.conf / systemd-resolved / NetworkManager)
> - Drift detector via SSH (parse resolv.conf or `resolvectl status`)
> - Celery sync task + periodic drift task
> - Frontend: group resolver page + host detail "DNS" tab
> - pytest suite
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T3 → T5 → T7 → T9
>
> **Prerequisite**: Service Management extension (provides `host_module_status` table and `SyncJob.module_type`)

---

## Context

### Architecture Decisions
- **Assignment model**: Group-level defaults + per-host overrides. Unlike rule-based modules (services, packages), `ResolverConfig` is a **singleton per scope** — each group or host has at most ONE resolver config, not a list. The merge key is the module itself.
- **Merge**: If host has a resolver config, use it entirely (full replacement). Otherwise use the highest-priority group's config. No field-level merge — the entire config block replaces.
- **Three resolver backends**: `resolv_conf` (direct file write), `systemd_resolved` (write `/etc/systemd/resolved.conf` + restart service), `networkmanager` (write `/etc/NetworkManager/conf.d/` + restart). Backend is stored on the config, not auto-detected.
- **Independent sync**: Uses `host_module_status` table.

### Key Differences from Rule-Based Modules
This is a **config module**, not a **rules module**:
- ONE config per scope (not a list of rules)
- No `priority` field within the model (not needed — only one config per group)
- No `name`/merge-key — the config IS the entity
- Group priority only matters for choosing WHICH group's config to use
- Effective config = "which single ResolverConfig applies to this host?"

### Safety
- Nameservers validated as valid IPv4/IPv6 addresses
- Search domains validated as valid DNS names
- At least one nameserver required (empty nameservers = broken DNS)
- `resolv.conf` options validated: known keys only (`ndots`, `timeout`, `attempts`, `rotate`, `edns0`)

---

## Work Objectives

### Definition of Done
- [x] Group-level resolver config: CRUD on `/api/groups/{id}/resolver`
- [x] Host-level override: CRUD on `/api/hosts/{id}/resolver`
- [x] Effective config: `GET /api/hosts/{id}/effective-resolver` returns the ONE applicable config
- [x] Preview: `GET /api/hosts/{id}/resolver-preview` returns rendered config file content
- [x] Plan: `POST /api/resolver/hosts/{id}/plan` previews changes
- [x] Sync: `POST /api/resolver/hosts/{id}/sync` applies via Ansible
- [x] Drift: `POST /api/resolver/hosts/{id}/drift-check`
- [x] Frontend: `/groups/{id}/resolver` page + "DNS" tab on host detail
- [x] Tests: 10+ tests (26 passing)

### Must Have
- `ResolverConfig` model: `nameservers` (JSONB array of IP strings, ordered), `search_domains` (JSONB array), `options` (JSONB dict), `resolver_type` (enum: resolv_conf/systemd_resolved/networkmanager), `dns_over_tls` (bool, default False)
- DB CHECK constraint for group_id/host_id exclusivity
- UNIQUE constraint: one config per group, one per host (`group_id` unique where not null, `host_id` unique where not null)
- Nameserver IP validation (IPv4 and IPv6)
- Search domain DNS name validation
- At least 1 nameserver required
- Options allowlist: `ndots`, `timeout`, `attempts`, `rotate`, `edns0`
- Backend-specific Ansible templates:
  - `resolv_conf`: write `/etc/resolv.conf`
  - `systemd_resolved`: write `/etc/systemd/resolved.conf` + restart `systemd-resolved`
  - `networkmanager`: write `/etc/NetworkManager/conf.d/90-barricade-dns.conf` + restart `NetworkManager`
- Drift collector: parse actual resolver state per backend
- Alembic migration (reversible)

### Must NOT Have (Guardrails)
- No DNS server management (this manages the CLIENT resolver, not a DNS server)
- No DNSSEC configuration (complex, out of scope)
- No DNS-over-HTTPS (DoH) — only DNS-over-TLS for systemd-resolved
- No auto-detection of resolver backend (user must specify)
- No modification to existing module code
- No GitOps integration

---

## Execution Strategy

```
Wave 1 (Foundation — 3 parallel):
├── T1: ResolverConfig model + Alembic migration [quick]
├── T2: Resolver schemas + validators [unspecified-high]
└── T3: Resolver merge engine + config file renderers [unspecified-high]

Wave 2 (Backend — 3 parallel):
├── T4: Resolver CRUD API + effective-config + preview endpoints [unspecified-high]
├── T5: Ansible resolver playbook generator (3 backends) [unspecified-high]
└── T6: Resolver drift collector (resolv.conf parser + resolvectl parser) [unspecified-high]

Wave 3 (Sync + Drift — 2 parallel):
├── T7: Resolver sync Celery task + sync API [unspecified-high]
└── T8: Resolver drift detection task + API [unspecified-high]

Wave 4 (Frontend + Tests — 2 parallel):
├── T9: Frontend — group resolver page + host detail DNS tab [visual-engineering]
└── T10: pytest suite [unspecified-high]

Critical Path: T1 → T3 → T5 → T7 → T9
Max Concurrent: 3
```

---

## TODOs

- [x] 1. ResolverConfig Model + Alembic Migration

  **What to do**:
  - Create `backend/app/resolver/__init__.py` (empty)
  - Create `backend/app/resolver/models.py`:
    - `ResolverType` enum: `resolv_conf`, `systemd_resolved`, `networkmanager`
    - `ResolverConfig` model:
      - `id`, `group_id` (FK nullable), `host_id` (FK nullable)
      - `nameservers` (JSONB, NOT NULL — array of IP strings, ordered)
      - `search_domains` (JSONB, default [] — array of domain strings)
      - `options` (JSONB, default {} — dict of resolver options)
      - `resolver_type` (Enum ResolverType, default resolv_conf)
      - `dns_over_tls` (Boolean, default False — only meaningful for systemd_resolved)
      - `created_at`, `updated_at`
      - CHECK: group_id/host_id exclusivity
      - UNIQUE partial indexes: `group_id` unique where not null, `host_id` unique where not null (singleton per scope)
  - Create Alembic migration

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 1 | Blocks: T3, T4, T7, T8 | Blocked By: None

  **References**:
  - `backend/app/services/models.py` — Model pattern (adapt: no priority, singleton)

  **Acceptance Criteria**:
  - [ ] Table with CHECK + UNIQUE constraints
  - [ ] Only one config per group, one per host enforced at DB level
  - [ ] Migration reversible

  **Commit**: YES — `feat(models): add ResolverConfig model`

- [x] 2. Resolver Schemas + Validators

  **What to do**:
  - Create `backend/app/resolver/schemas.py`:
    - `ResolverConfigCreate`: `nameservers` (list[str], min 1), `search_domains` (list[str], default []), `options` (dict[str, int|str], default {}), `resolver_type` (resolv_conf/systemd_resolved/networkmanager), `dns_over_tls` (bool, default False)
      - Validator `nameservers`: each must be valid IPv4 or IPv6 via `ipaddress.ip_address()`. Min 1, max 3 (resolv.conf limit).
      - Validator `search_domains`: each must be valid DNS name — labels alphanumeric + hyphens, max 253 chars total. Max 6 domains (resolv.conf limit).
      - Validator `options`: keys must be in allowlist `{"ndots", "timeout", "attempts", "rotate", "edns0"}`. Values: `ndots`/`timeout`/`attempts` must be int 0-15.
      - Validator `dns_over_tls`: only valid when `resolver_type=systemd_resolved` (warn/ignore for other backends)
    - `ResolverConfigUpdate`: same, all optional (but if nameservers provided, min 1)
    - `ResolverConfigResponse`: all fields + `id`, `group_id`, `host_id`, `created_at`, `updated_at`
    - `EffectiveResolverResponse`: fields + `source` ("group"|"host"), `source_id`, `source_name`

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 1 | Blocks: T4, T5, T6 | Blocked By: None

  **Acceptance Criteria**:
  - [ ] Empty nameservers rejected
  - [ ] Invalid IPs rejected (e.g., `"not.an.ip"`)
  - [ ] IPv6 nameservers accepted (`"2606:4700:4700::1111"`)
  - [ ] Unknown options keys rejected
  - [ ] `dns_over_tls` ignored when resolver_type != systemd_resolved

  **Commit**: YES — `feat(resolver): add schemas and validators`

- [x] 3. Resolver Merge Engine + Config File Renderers

  **What to do**:
  - Create `backend/app/resolver/merge.py`:
    - `async def get_effective_resolver(host_id, db) -> EffectiveResolverResponse | None`:
      - Check host-level config first → if exists, return it (source="host")
      - Get host's groups ordered by priority DESC
      - Return first group that has a ResolverConfig (highest priority wins)
      - Return None if no config found (host has no DNS management)
  - Create `backend/app/resolver/renderer.py`:
    - `def render_resolv_conf(config: ResolverConfig) -> str`:
      ```
      # Managed by Barricade — do not edit manually
      nameserver 10.0.0.53
      nameserver 10.0.0.54
      search internal.example.com
      options ndots:5 timeout:2
      ```
    - `def render_systemd_resolved(config: ResolverConfig) -> str`:
      ```ini
      # Managed by Barricade
      [Resolve]
      DNS=10.0.0.53 10.0.0.54
      Domains=internal.example.com
      DNSOverTLS=yes
      ```
    - `def render_networkmanager_conf(config: ResolverConfig) -> str`:
      ```ini
      # Managed by Barricade
      [global-dns-domain-*]
      servers=10.0.0.53,10.0.0.54
      ```

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 1 | Blocks: T4, T5, T7 | Blocked By: T1

  **Acceptance Criteria**:
  - [ ] resolv.conf format correct (nameserver per line, search line, options line)
  - [ ] systemd-resolved format correct (INI format)
  - [ ] NetworkManager format correct
  - [ ] Merge returns host override if exists, else highest-priority group

  **Commit**: YES — `feat(resolver): add merge engine and config file renderers`

- [x] 4. Resolver CRUD API + Preview

  **What to do**:
  - Create `backend/app/api/resolver.py`:
    - Group config: `GET/PUT/DELETE /api/groups/{group_id}/resolver` (GET returns current or 404, PUT creates or updates — singleton, DELETE removes)
    - Host override: `GET/PUT/DELETE /api/hosts/{host_id}/resolver` (same singleton pattern)
    - Effective: `GET /api/hosts/{host_id}/effective-resolver` → returns the one applicable config or 404
    - Preview: `GET /api/hosts/{host_id}/resolver-preview` → returns rendered config file content as plain text
    - Note: NO POST endpoints — use PUT for create-or-update (singleton pattern)
  - Register in `app/main.py`, audit logging

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2 | Blocks: T7, T9 | Blocked By: T1, T2, T3

  **Acceptance Criteria**:
  - [ ] PUT creates config if none exists, updates if exists (upsert)
  - [ ] DELETE removes config (returns to "unmanaged")
  - [ ] Effective returns single config or 404
  - [ ] Preview returns rendered text for the applicable backend

  **Commit**: YES — `feat(api): add resolver CRUD + preview endpoints`

- [x] 5. Ansible Resolver Playbook Generator

  **What to do**:
  - Create `backend/app/resolver/generator.py`:
    - `def generate_resolver_playbook(host_ip, config, rendered_content: str, ssh_key_path) -> dict`:
      - **resolv_conf**: `ansible.builtin.copy` to `/etc/resolv.conf` with `mode: "0644"`
      - **systemd_resolved**: `ansible.builtin.copy` to `/etc/systemd/resolved.conf` + `ansible.builtin.systemd` to restart `systemd-resolved`
      - **networkmanager**: `ansible.builtin.copy` to `/etc/NetworkManager/conf.d/90-barricade-dns.conf` + `ansible.builtin.systemd` to restart `NetworkManager`
      - All: `become: true`, `gather_facts: false`

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2 | Blocks: T7 | Blocked By: T3

  **Acceptance Criteria**:
  - [ ] Three backend paths supported
  - [ ] systemd-resolved restarts service after config change
  - [ ] NetworkManager restarts after config change
  - [ ] resolv.conf is simple file write (no service restart)

  **Commit**: YES — `feat(ansible): add resolver playbook generator`

- [x] 6. Resolver Drift Collector + Parser

  **What to do**:
  - Create `backend/app/resolver/collector.py`:
    - `async def collect_resolver_state(host_ip, ssh_port, private_key_pem, resolver_type: str) -> dict`:
      - **resolv_conf**: `cat /etc/resolv.conf` → parse `nameserver` lines, `search` line, `options` line
      - **systemd_resolved**: `resolvectl status` → parse `DNS Servers:`, `DNS Domain:` lines. Fallback: `cat /etc/systemd/resolved.conf` if resolvectl unavailable.
      - **networkmanager**: `cat /etc/NetworkManager/conf.d/90-barricade-dns.conf` → parse INI format. Fallback: `nmcli dev show | grep DNS` if file doesn't exist.
      - Return: `{"nameservers": [...], "search_domains": [...], "options": {...}}`
  - Create `backend/app/resolver/diff.py`:
    - `ResolverDiff` dataclass: `nameservers_changed` (bool), `search_domains_changed` (bool), `options_changed` (bool), `current`, `desired`
    - Property: `has_changes`

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2 | Blocks: T7, T8 | Blocked By: T2

  **Acceptance Criteria**:
  - [ ] resolv.conf parsed: nameservers, search, options extracted
  - [ ] resolvectl output parsed for DNS servers and domains
  - [ ] Missing config file → "unmanaged" state (not error)
  - [ ] Diff detects nameserver order changes

  **Commit**: YES — `feat(resolver): add drift collector and parser`

- [x] 7. Resolver Sync Celery Task + Sync API

  **What to do**:
  - Create `backend/app/tasks/resolver_sync.py`:
    - Celery task: get effective resolver → render config → generate playbook → ansible-runner → update SyncJob + host_module_status
    - `module_type="resolver"` on SyncJob
  - Create `backend/app/api/resolver_sync.py`:
    - `POST /api/resolver/hosts/{host_id}/plan`
    - `POST /api/resolver/hosts/{host_id}/sync`
    - `POST /api/resolver/groups/{group_id}/sync`
    - `GET /api/resolver/jobs/{job_id}`
  - Register router

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T4, T5, T6

  **Acceptance Criteria**:
  - [ ] Plan shows nameserver/domain/options changes
  - [ ] SyncJob with `module_type="resolver"`
  - [ ] Backend-specific playbook generated correctly

  **Commit**: YES — `feat(tasks): add resolver sync Celery task + sync API`

- [x] 8. Resolver Drift Detection Task + API

  **What to do**:
  - Create `backend/app/tasks/resolver_drift.py`
  - Add drift endpoints:
    - `POST /api/resolver/hosts/{host_id}/drift-check`
    - `PUT /api/resolver/hosts/{host_id}/drift-settings`

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 3 | Blocks: T10 | Blocked By: T1, T6

  **Acceptance Criteria**:
  - [ ] Nameserver order change detected as drift
  - [ ] Missing search domain detected
  - [ ] `host_module_status` updated

  **Commit**: YES — `feat(tasks): add resolver drift detection + API`

- [x] 9. Frontend — Group Resolver Page + Host Detail DNS Tab

  **What to do**:
  - Create `frontend/app/(dashboard)/groups/[id]/resolver/page.tsx`:
    - **Form-based UI** (not table — singleton config):
      - Resolver type selector (resolv.conf / systemd-resolved / NetworkManager)
      - Nameservers: ordered list with add/remove/reorder. IP input with validation.
      - Search domains: list with add/remove
      - Options: key-value editor (dropdown for key, input for value)
      - DNS-over-TLS toggle (shown only for systemd-resolved)
      - Save/Delete buttons
    - "Preview" button → modal showing rendered config file
  - Add "DNS" tab on host detail page:
    - Shows effective resolver config (source annotation)
    - Override form for host-level config
    - If no config: "DNS is not managed for this host"
  - Add "DNS Resolver" link on group detail page
  - TypeScript interfaces

  **Recommended Agent Profile**: `visual-engineering` + `frontend-ui-ux`
  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T4, T7

  **Acceptance Criteria**:
  - [ ] Form-based UI (not table) since it's a singleton config
  - [ ] Nameserver list reorderable
  - [ ] DNS-over-TLS toggle only shown for systemd-resolved
  - [ ] Preview modal shows rendered config
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add DNS resolver management pages`

- [x] 10. pytest Suite

  **What to do**:
  - Create `backend/tests/test_resolver.py`:
    - **TestResolverSchemas**: IP validation, empty nameservers rejected, unknown options rejected, dns_over_tls only for systemd_resolved
    - **TestResolverRenderer**: resolv.conf format, systemd-resolved INI format, NetworkManager format
    - **TestResolverMerge**: singleton merge — host override wins, highest-priority group used
    - **TestResolverAPI**: PUT creates, PUT updates, DELETE removes, effective returns single config, preview returns text
    - **TestResolverCollector**: resolv.conf parsing, resolvectl parsing
    - **TestResolverDiff**: nameserver change detected, search domain change, options change

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T7, T8

  **Acceptance Criteria**:
  - [ ] 12+ tests, all passing
  - [ ] All 3 renderer formats tested
  - [ ] Singleton semantics tested (only one config per scope)
  - [ ] IP validation tested (IPv4 + IPv6)

  **Commit**: YES — `test(resolver): add DNS resolver test suite`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
- [x] F2. **Code Quality Review** — `unspecified-high`
- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright`)
- [x] F4. **Scope Fidelity Check** — `deep` — Verify: no DNS server management, no DNSSEC, no DoH, no auto-detection.

---

## Success Criteria

```bash
cd backend && pytest tests/test_resolver.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```
