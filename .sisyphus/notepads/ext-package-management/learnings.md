# Learnings - ext-package-management

## Task 2: Schemas & Constants

- Package module uses `fnmatch` for protected package matching (unlike services module which uses exact `in` check) — this enables wildcard patterns like `linux-image*`
- `PackageRuleUpdate` intentionally omits `package_name` — names are immutable, use delete+create pattern
- Update schemas use `model_dump(exclude_unset=True)` not `exclude_none` — important distinction for nullable fields
- Followed cron/schemas.py style: `Literal[...]` for enum-like fields, `field_validator` with `@classmethod`, `model_config = {"from_attributes": True}` on Response classes
- `_PKG_NAME_RE` pattern: `^[a-zA-Z0-9][a-zA-Z0-9._+:\-]*$` — allows colons (epoch versions in dpkg) and plus signs (lib packages like `libstdc++`)
- Repository schemas have separate URL validator that accepts both http:// and https://

## Task 6: Collector & Diff Engine

- asyncssh pattern: use `client_keys=[private_key]` (list), not `private_key=` kwarg — matches services/collector.py and cron/collector.py
- Added `asyncio.wait_for(..., timeout=30.0)` wrapping (cron/collector.py pattern) — services/collector.py lacks this, but timeout is essential for SSH ops
- Package collector returns plain dicts `{"name", "state", "version"}` — no dataclass for SSH results (diff engine consumes these directly)
- dpkg status parsing: only `"ii"` = installed; `"rc"` (removed-config) and `"un"` (unknown) both map to absent
- rpm version extraction: strip `name-` prefix then `.arch` suffix via `rsplit(".", 1)[0]`
- PackageDiff uses `has_drift` property (not `has_changes` like ServiceDiff) — semantic difference: packages "drift" from desired state
- `_version_matches` uses `fnmatch.fnmatch` for glob patterns (e.g. `1.24.*`) — no `>=` comparison operators per design
- `desired_state="latest"` means any installed version is in_sync — it's a desired-state concept, never sent to dpkg/rpm

## Task 1: Models & Migration

- When sharing a PostgreSQL ENUM type across multiple tables in a single migration, use `postgresql.ENUM(..., create_type=False)` and call `.create(bind, checkfirst=True)` explicitly. Using `sa.Enum(..., create_type=False)` does NOT prevent the `before_create` event from re-creating the type.
- `app/models/__init__.py` is the model registry — ALL model classes must be imported there for Alembic autogenerate.
- Pre-existing modules (cron, user_mgmt) were missing from `__init__.py`, causing spurious table drops in autogenerate. Fixed.
- Models must be imported via `from app.models import X` (through registry), not `from app.packages.models import X` directly — direct path causes circular imports.
- `alembic/script.py.mako` was missing, had to create from default template.
- DB setup: `norce-base-postgres` only had `postgres` user; created `barricade` user/db manually.
