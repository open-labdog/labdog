# DNS Resolver Extension — Learnings

## Key Architecture Facts
- **Singleton config** per scope: one ResolverConfig per group OR per host (not a list)
- **Full replacement merge**: host override replaces entire group config (no field merge)
- **3 resolver backends**: resolv_conf, systemd_resolved, networkmanager
- No auto-detection — user specifies backend

## Project Conventions
- Models in `app/{module}/models.py`, imported in `app/models/__init__.py`
- Alembic current head: `263ff4c0e96c` — new migration `down_revision = '263ff4c0e96c'`
- ENUM types need `create_type=False` + `.create(op.get_bind(), checkfirst=True)` in migration
- Routers registered in `app/main.py` with `app.include_router(router, prefix="/api")`
- Celery tasks use `@celery_app.task(bind=True, queue="long_running")`
- SSH key decrypted to `/dev/shm/barricade-{job_id}.key`, cleaned in `finally`
- `host_module_status` table tracks per-host per-module sync/drift status
- Async SQLAlchemy: use `AsyncSession`, import `AsyncSessionLocal` in tasks

## Models Pattern (from services/models.py)
- `CheckConstraint`: `"(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)"`
- Timestamps: `server_default=func.now()`, `onupdate=func.now()`
- Enums: `SAEnum(EnumClass, name="enumname")`

## JSONB for nameservers/search_domains/options (PostgreSQL)
- Import: `from sqlalchemy.dialects.postgresql import JSONB`
- Usage: `mapped_column(JSONB, nullable=False)`

## Unique Partial Indexes for Singleton Constraint
- One config per group: `UniqueConstraint("group_id")` only where group_id is not null
- SQLAlchemy partial index: `Index("ix_resolver_group_unique", "group_id", unique=True, postgresql_where=column("group_id") != None)`

## Wave 1 Started (2026-03-19)
T1 (model), T2 (schemas), T3 (merge+renderer)
## ResolverConfig Model Implementation

### Key Learnings

1. **Enum Creation in Migrations**: When models are imported in alembic/env.py, SQLAlchemy automatically creates enums defined in models. To avoid duplicate enum creation errors:
   - Use `postgresql.ENUM(..., create_type=False)` in migration column definitions
   - Use a DO block with exception handling for idempotent enum creation
   - Example: `DO $$ BEGIN CREATE TYPE ... EXCEPTION WHEN duplicate_object THEN null; END $$;`

2. **Partial Unique Indexes**: PostgreSQL supports partial unique indexes with `postgresql_where` clause:
   - Allows multiple NULL values while enforcing uniqueness on non-NULL values
   - Perfect for group_id/host_id pattern where exactly one must be set
   - Syntax: `Index(..., unique=True, postgresql_where=text("group_id IS NOT NULL"))`

3. **Check Constraints**: Enforce business logic at database level:
   - Pattern: `(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)`
   - Ensures exactly one of two foreign keys is set

4. **JSONB Defaults**: Use `server_default` with string literals for JSONB columns:
   - `server_default="[]"` for empty arrays
   - `server_default="{}"` for empty objects
   - NOT `Boolean()` or Python defaults

5. **Migration Reversibility**: Always test `upgrade -> downgrade -> upgrade` cycle:
   - Ensures migrations are truly reversible
   - Catches issues with enum/type cleanup

### Files Created
- `backend/app/resolver/__init__.py` - Module marker
- `backend/app/resolver/models.py` - ResolverConfig + ResolverType
- `backend/alembic/versions/0008_resolver_config.py` - Migration

### Files Modified
- `backend/app/models/__init__.py` - Added imports

### Testing
All migration cycles passed:
- ✓ upgrade head
- ✓ downgrade -1
- ✓ upgrade head
