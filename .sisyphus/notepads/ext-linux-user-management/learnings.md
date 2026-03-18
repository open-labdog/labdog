# Learnings — ext-linux-user-management

## 2026-03-18 Session start

### Confirmed patterns from service management (direct references)
- **Model pattern**: `backend/app/services/models.py` — SAEnum, CheckConstraint, Mapped[], ForeignKey CASCADE
- **Migration pattern**: `backend/alembic/versions/0004_service_management.py` — revision chain is `0005_hosts_entries` → next is `0006`
- **Merge engine**: `backend/app/services/merge.py` — priority DESC group query, host override = full replace, sorted by key
- **Schema pattern**: `backend/app/services/schemas.py` — field_validator, PROTECTED check raises ValueError
- **API CRUD pattern**: `backend/app/api/services.py` — current_active_user auth, log_action on mutations, db.flush() + db.commit() + db.refresh()
- **Latest migration**: `0005_hosts_entries.py` — next migration must be `0006_linux_user_management.py`, down_revision=`0005_hosts_entries`
- **JSONB**: Use `sa.JSON` in Alembic, `from sqlalchemy.dialects.postgresql import JSONB` in model

### Package structure
- New top-level package: `backend/app/user_mgmt/` (with __init__.py)
- Module type string for SyncJob/host_module_status: `"linux_user"`

### Key differences from service management
- JSONB columns: `authorized_keys` (list of strings), `supplementary_groups` (list of strings)
- Two models in one migration: LinuxUser + LinuxGroup (reuse UserState enum for both)
- Superuser-only endpoints (unlike service management which uses current_active_user)
- uid/gid >= 1000 validation (or None for auto-assign)
- Protected user/group deny-lists much larger than PROTECTED_SERVICES
