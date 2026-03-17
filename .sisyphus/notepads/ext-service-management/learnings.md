# Learnings — ext-service-management

## Codebase Conventions

### Models
- Base class: `Base` from `app.models.base` (uses NAMING_CONVENTION for constraints)
- CHECK constraints: use `CheckConstraint("...", name="ck_tablename_constraintname")`
- Enums: defined as Python `enum.Enum` AND registered as PostgreSQL ENUM type
- `created_at`/`updated_at`: `DateTime(timezone=True)` with `default=func.now()`
- FK naming: `fk_tablename_column_reftable` (auto via NAMING_CONVENTION)

### Alembic
- Migration files: `NNNN_description.py` format (e.g., `0004_...`)
- Next migration number: **0004**
- Revision IDs: match the file prefix (e.g., `"0004"`)
- Down revision: `"0003_drop_rbac"`

### Schemas (Pydantic v2)
- Validators: `@field_validator("field")` + `@classmethod`
- ORM mode: `model_config = {"from_attributes": True}` (NOT `class Config`)
- Optional fields: `field: str | None = None`

### API
- Router: `APIRouter(tags=["..."])` — prefix added at `main.py` registration
- DB injection: `db: AsyncSession = Depends(get_db)`
- Auth: `current_active_user` for all service CRUD (same as firewall rules)
- Errors: `HTTPException(status_code=int, detail=str)`
- CRUD pattern: fetch → validate → mutate → commit → refresh → return
- Delete: `status_code=204`, returns None
- Audit: `await log_action(db=db, action="create", entity_type="service_rule", entity_id=..., user_id=user.id, ...)`
  - Call BEFORE final commit; does NOT auto-commit

### Celery Tasks
- Decorator: `@celery_app.task(bind=True, name="app.tasks.X.Y", queue="long_running")`
- DB in tasks: import inside function, use `AsyncSessionLocal()` context manager, wrap in `asyncio.run()`
- SSH keys: decrypt inside task, write to `/dev/shm/barricade-{job_id}.key`, clean in `finally`
- `celery_app` imported from `app.tasks`

### Frontend
- Data fetching: React Query `useQuery` + `queryKey` arrays
- Mutations: `apiFetch(path, { method, body })` + `queryClient.invalidateQueries`
- UI library: shadcn/ui (Button, Dialog, Table, Badge, Input, Label, Card)
- Dark theme: `bg-slate-900`, `border-slate-700`, `text-slate-300`
- No tab navigation exists yet — T9 creates this pattern from scratch
- API base: `NEXT_PUBLIC_API_URL` env var (accessed via `process.env.NEXT_PUBLIC_API_URL`)

### Testing
- Fixtures: `db`, `client`, `superuser_client`, `regular_user_client`
- Factories: `create_group(db)`, `create_host(db)`, `create_ssh_key(db)`, `create_rule(db, group_id)`
- Test DB: testcontainers PostgreSQL, transaction rollback per test
- Structure: class-based `class TestX:` with `async def test_y(self, client, db)`
