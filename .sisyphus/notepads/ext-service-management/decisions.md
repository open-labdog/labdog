# Decisions — ext-service-management

## Resolved Decisions

### Auth Pattern (C-002)
- **Decision**: `current_active_user` for all service CRUD (not superuser)
- **Rationale**: Matches firewall rules pattern; RBAC was removed

### SyncJob modifications (C-001)
- **Decision**: Allow 3 narrow changes to `backend/app/api/sync.py`:
  1. Add `module_type: str = "firewall"` to `SyncJobResponse`
  2. Add optional `module_type` filter to `list_jobs()`
  3. Scope `trigger_host_sync()` running-sync check to `module_type="firewall"`
- **Rationale**: Required for independent sync and response visibility

### Sync Collision Scope
- **Decision**: Independent per module — firewall sync does NOT block service sync
- **Implementation**: Each module's running-sync check filters by `module_type`

### Job Listing
- **Decision**: Add optional `module_type` query param to `GET /api/sync/jobs`
- **Default**: Returns all jobs (backward-compatible)
