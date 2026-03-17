# Compromises & Policy Exceptions Registry

> **Purpose**: Track every deviation from stated guardrails, patterns, or policies across all plans.
> Review this file before starting any plan and after completing any plan.
> If a compromise creates downstream debt, note it in the "Watch For" field.

---

## Active Compromises

### C-001: Firewall Code Modification Guardrail Exception

**Plan**: `ext-service-management.md` (T7)
**Date**: 2026-03-17
**Status**: Approved — not yet implemented

**Original Policy**:
> "No modification to existing firewall module code"

**Exception Granted** — 3 narrow changes to `backend/app/api/sync.py`:

| # | Change | Lines Affected | Backward-Compatible |
|---|--------|---------------|---------------------|
| 1 | Add `module_type: str = "firewall"` to `SyncJobResponse` | ~1 line in schema | YES — new field with default |
| 2 | Add optional `module_type` query param to `list_jobs()` | ~3 lines | YES — param is optional, default returns all |
| 3 | Scope `trigger_host_sync()` running-sync check to `module_type="firewall"` | ~1 line (add `.where()` clause) | YES — narrows existing filter |

**Why**: Without these changes, (1) `module_type` would be invisible in API responses even though it exists on the model, (2) job listing would mix firewall and service jobs with no way to filter, (3) a running service sync would block firewall sync and vice versa — contradicting the plan's "independent sync" architecture.

**Risk**: LOW — All changes are additive. Existing API consumers see one new field with a default value and one new optional query param. No existing behavior changes unless the new param is used.

**Watch For**:
- Every future extension module (packages, users, etc/hosts, DNS, cron) will rely on this same `module_type` mechanism. If the pattern proves awkward, the tech debt is in `sync.py` — this is the file to revisit.
- If `SyncJobResponse` grows too many module-specific fields over time, consider splitting into a base response + module-specific subclasses.

---

### C-002: Auth Pattern Decision Override

**Plan**: `ext-service-management.md` (T4, Must Have line 72)
**Date**: 2026-03-17
**Status**: Approved — not yet implemented

**Original Plan Said**:
> "RBAC: group-level endpoints use `require_group_role()`, host overrides require superuser"
> (T4): "superuser-only via `current_superuser`"

**What Was Decided**:
> All service CRUD endpoints use `current_active_user` (any authenticated user)

**Why**: The plan was written before RBAC was removed (via `user-management.md`). After RBAC removal, `require_group_role()` no longer exists. The existing firewall rules API uses `current_active_user` for all CRUD — service management follows the same pattern for consistency. There is no per-group permission system to leverage.

**Risk**: MEDIUM — Any authenticated user can modify service rules for any group. This matches firewall rules behavior, but as Barricade grows in multi-user environments, the lack of per-group permissions may become a pain point.

**Watch For**:
- If a future plan reintroduces per-group permissions (e.g., "team-based access control"), ALL module CRUD endpoints (firewall rules, service rules, and future extensions) will need to be updated simultaneously. This is not extension-specific debt — it's platform-level.
- Monitor user feedback: if users report that "anyone can change any group's services", this is the root cause.

---

### C-003: Celery Task Route Config Drift

**Plan**: `ext-service-management.md` (T7)
**Date**: 2026-03-17
**Status**: Approved — not yet implemented

**Original Pattern**:
> Task routing configured centrally in `app/tasks/__init__.py` via `task_routes` dict. Tasks also declare `queue="long_running"` explicitly in their decorators.

**Compromise**:
> New tasks (`service_sync`, `service_drift`) are added to `task_routes` for consistency, BUT the routing config and the decorator `queue=` param are **redundant**. The decorator takes precedence.

**Why**: Keeping both ensures the routing config stays a complete inventory of task-to-queue mappings, while the decorator acts as a safety net if someone removes a route pattern.

**Risk**: LOW — Redundancy is benign. But it creates a maintenance pattern where future contributors may update one without the other.

**Watch For**:
- When adding future extension tasks (package sync, user sync, etc.), remember to update BOTH `task_routes` in `__init__.py` AND the `queue=` param in the decorator.
- If the routing config grows unwieldy, consider a single wildcard: `"app.tasks.*": {"queue": "long_running"}` — but only if ALL tasks should go to `long_running`.

---

## Resolved / Historical

_None yet. Move entries here when the compromise is eliminated or superseded._

---

## Compromise Severity Guide

| Level | Meaning | Action |
|-------|---------|--------|
| **LOW** | Additive, backward-compatible, minimal maintenance burden | Note and proceed |
| **MEDIUM** | Creates future work or constrains future options | Review before next related plan |
| **HIGH** | Breaks a core invariant or creates significant tech debt | Requires explicit user sign-off and remediation timeline |
| **CRITICAL** | Security, data integrity, or reliability risk | Must resolve before execution |
