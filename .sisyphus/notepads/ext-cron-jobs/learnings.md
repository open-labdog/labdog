# Learnings — ext-cron-jobs

## 2026-03-18 Session start

### Confirmed patterns (from user-mgmt, just completed)
- Migration chain: 0006 is latest → new is `0007_cron_jobs.py`, down_revision="0006"
- Model pattern: `backend/app/user_mgmt/models.py` — SAEnum, CheckConstraint, Mapped[], JSONB
- Migration pattern: `backend/alembic/versions/0006_linux_user_management.py`
- Merge engine: `backend/app/user_mgmt/merge.py` — priority DESC, host override = full replace
- Schema pattern: `backend/app/user_mgmt/schemas.py` — field_validator, @classmethod
- API CRUD: `backend/app/api/linux_users.py` — current_superuser, log_action, flush+commit+refresh
- Sync task: `backend/app/tasks/user_sync.py` — Celery, module_type, AsyncSessionLocal
- Drift task: `backend/app/tasks/user_drift.py` — periodic, drift_check_enabled

### Cron-specific design decisions
- Merge key: `(name, user)` COMPOSITE — two jobs with same name but different user = different jobs
- `environment` is JSONB dict (not list) — default `{}`
- Migration: `cronstate` enum (present/absent) — check if userstate enum can be reused or create new
- `module_type = "cron"` for SyncJob + host_module_status
- Ansible marker for drift: `#Ansible: {name}` comment above the crontab line
- No @reboot, @daily etc. — 5-field only
- UNIQUE partial indexes: `(group_id, name, user)` and `(host_id, name, user)`

## T1 Complete: CronJob Model & Migration

### Files created
- `backend/app/cron/__init__.py` — empty module init
- `backend/app/cron/models.py` — CronState enum + CronJob model (62 lines)
- `backend/alembic/versions/0007_cron_jobs.py` — migration with partial unique indexes (92 lines)

### Key implementation details
- **CronState enum**: `present` / `absent` (fresh enum, not reusing userstate)
- **CronJob model**: Follows user_mgmt pattern exactly
  - Scope constraint: `(group_id XOR host_id)` — one must be set, not both
  - JSONB `environment` field with default `dict` (empty dict)
  - Composite merge key: `(name, user)` — two jobs with same name but different user are distinct
  - Timestamps: `created_at`, `updated_at` with server defaults
- **Migration 0007**: 
  - Enum created implicitly by `create_table` (no explicit `.create()` call)
  - Partial unique indexes enforce uniqueness only when group_id/host_id is NOT NULL:
    - `uq_cron_jobs_group_name_user` on `(group_id, name, user)` where `group_id IS NOT NULL`
    - `uq_cron_jobs_host_name_user` on `(host_id, name, user)` where `host_id IS NOT NULL`
  - Downgrade: drops indexes, table, then enum

### Verification
- ✓ Import test: `from app.cron.models import CronJob` → OK
- ✓ Commit: `feat(models): add CronJob model`
- ✓ Migration chain: 0006 → 0007 (down_revision="0006")

### Next tasks
- T2: Schemas (CronJobCreate, CronJobUpdate, CronJobRead)
- T3: Merge engine (priority-based merge, host override)

## T3 Complete: Merge Engine

### Files created
- `backend/app/cron/merge.py` — get_effective_cron_jobs() (72 lines)

### Key implementation details
- **Composite merge key**: `(name, user)` tuple — dict key is `tuple[str, str]`
- **Pattern**: Exact replica of `user_mgmt/merge.py` adapted for CronJob fields
- **Host override**: Replaces group entry entirely (same key overwrites in merged dict)
- **Sort**: `lambda j: (j.name, j.user)` for deterministic ordering
- **Safe defaults**: `rule.environment or {}` for JSONB, `rule.state.value` for enum serialization
- **Fields populated**: name, user, schedule, command, environment, state, priority, comment, source, source_id, source_name

### Verification
- ✓ Import test: `from app.cron.merge import get_effective_cron_jobs` → OK
- ✓ Commit: `feat(cron): add merge engine`
