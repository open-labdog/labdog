# Draft: Service Management + User Management Extensions

## Requirements (confirmed)
- Follow the "Barricade pattern": DB model → Ansible renderer → drift detector → sync engine → audit log → React UI
- Service Management = Module 1 (low complexity)
- User Management = Module 2 (medium complexity)
- Each gets its own plan file in `.sisyphus/plans/`
- Main `barricade-extensions.md` updated to reference individual plans

## Decisions Made

### Multi-module sync
- **Start simple**: Each module syncs independently (like firewall today)
- Multi-module unified sync deferred to a future plan

### Assignment model
- **Both group-based AND per-host**: Group rules are defaults, host-specific rules override
- ServiceRule / LinuxUser have both `group_id` (nullable) and `host_id` (nullable), exactly one set
- Merge: For each item (service_name / username), host-specific config wins over group config
- This is like CSS specificity — group = default, host = override

### UI placement
- **Tabs on host detail page**: `/hosts/{id}` gets "Services" and "Users" tabs
- Shows EFFECTIVE config (merged group defaults + host overrides)
- Host-level overrides managed inline on the same page
- Group-level defaults can be set via `/groups/{id}/services` and `/groups/{id}/users` pages

### Test strategy
- **Tests after implementation**: Build first, then add test module with 3+ tests per area

### Architecture (refined after Metis review)
- Each module gets its own: model, API router, Ansible renderer, drift detector, sync endpoints, Celery task
- Reuse existing SyncJob model by adding `module_type` column (`server_default='firewall'`, backward-compatible)
- NEW: `host_module_status` junction table `(host_id, module_type) → (sync_status, drift_enabled, last_sync_at, last_drift_check_at)` — replaces per-module column explosion on Host
- Reuse existing audit log (log_action with entity_type="service_rule" / "linux_user")
- Each module has its own Alembic migration
- Each module has its own state collector (SSH + parse)
- New top-level packages: `app/services/`, `app/user_mgmt/` (NOT nested under `app/rules/`)
- MUST NOT modify existing firewall module code

### Merge semantics (from Metis)
- Merge key: `service_name` for services, `username` for users
- Host override = FULL RECORD REPLACEMENT (not field-level merge)
- `authorized_keys`: Full replacement when host overrides (not union)
- Priority-based merge for group conflicts (like firewall)

### Drift detection (from Metis)
- `restarted`/`reloaded` normalized to `running` for drift comparison (avoids false positives)
- Service not installed on host → `error` status
- User not found on host → `out_of_sync` with add diff
- Systemd-only: HARD REQUIREMENT

### Security (from Metis)
- Protected service deny-list: `sshd`, `networking`, `systemd-*` (constant, not scattered)
- Protected user deny-list: `root`, `daemon`, `bin`, `sys`, `nobody`, `sshd` (constant)
- `uid >= 1000` enforced (no root UID creation)
- `sudo_rule` validated against injection (no shell metacharacters, backticks, $() substitution)
- Only public keys in authorized_keys (never private keys)
- Sudo via `/etc/sudoers.d/{username}` drop-in files (not editing /etc/sudoers)
- DB CHECK constraint: `(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)`

## Scope Boundaries
- INCLUDE: DB model, Alembic migration, API CRUD + sync/drift endpoints, Ansible renderer, state collector + parser, Celery sync task, drift detection, frontend tabs on host detail, group-level management pages, tests
- EXCLUDE: Multi-module unified sync, module enable/disable per group, GitOps integration for non-firewall modules, Playwright E2E for new pages
