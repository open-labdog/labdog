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
