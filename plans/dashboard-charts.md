# plan: dashboard charts & activity feed

Branch-scoped scratchpad (delete before PR, per CONTRIBUTING.md).

## Goal
Add to the Fleet Overview dashboard: (a) sync success-rate over time chart,
(b) drift trend over the past week chart, (c) compact recent-activity feed
(last ~10 audit events) with a "View all" link to `/audit`.

## Core architecture — hybrid metrics substrate
- **Sync success-rate:** aggregate existing `sync_jobs` in place (complete,
  never-pruned history). Do not copy it.
- **Drift trend:** no history exists → new narrow, typed, append-only
  `drift_samples` fact table, written forward-only from the drift-check path.
  Chart renders an empty-state until data accrues.
- Both behind one `app/metrics/` service so the future OpenMetrics `/metrics`
  endpoint (next roadmap item) reuses the same aggregations. Same underlying
  numbers as the in-UI charts.

## Backend
- `backend/app/models/drift_sample.py` (register in `models/__init__.py`):
  id, host_id (FK→hosts CASCADE), module_type str(50), status str(20),
  add_count/remove_count/policy_change_count (int default 0),
  duration_ms (int nullable), checked_at (tz-aware default now).
  Indexes: (checked_at), (module_type, checked_at), (host_id).
- Migration `0013_drift_samples`, down_revision `0012_grafana_instances_kind_url`.
  Raw-SQL `op.execute` style. No seeding/backfill (forward-only).
- `backend/app/metrics/`: `recorder.py` (record_drift_sample — adds to caller
  session, caller commits; NOT log_action), `service.py` (date_trunc + COUNT
  FILTER aggregations), `schemas.py` (SyncRate*/DriftTrend* Pydantic, shared).
- Instrument drift-check writes (same session, before existing commit):
  `app/tasks/drift.py` `_check_drift_for_one_host`, `app/api/drift.py`
  check_host_drift/check_group_drift, per-module drift tasks
  (service/package/user/resolver/cron/hosts). Timer → duration_ms.
- New router `backend/app/api/dashboard.py` (prefix `/dashboard`; register in
  `main.py`). Reserve `/metrics` for later. Endpoints:
  - `GET /api/dashboard/sync-success-rate?days=7&granularity=day&module=`
  - `GET /api/dashboard/drift-trend?days=7&granularity=day&module=`
  Query params: days int ge=1 le=90, granularity Literal[day,hour], module opt.
- Activity feed: reuse `GET /api/audit-log?limit=10` as-is. No new endpoint.

## Frontend
- Add `recharts`. Hand-port trimmed base-ui-compatible `components/ui/chart.tsx`
  (ChartContainer/ChartTooltip/ChartTooltipContent/ChartConfig) — do NOT
  `npx shadcn add chart`. All chart files `"use client"`.
- `components/dashboard/`: sync-success-chart.tsx (green), drift-trend-chart.tsx
  (amber, owns empty-state), activity-feed-panel.tsx (reuse SyncStatusBadge +
  formatRelativeTime, "View all" → /audit).
- Slot into `app/(dashboard)/dashboard/page.tsx` between summary grids and the
  triage table. Reuse Card/useDelayedLoading/apiFetch/useQuery (refetch 30s).
- Types in `lib/types.ts` mirroring Pydantic. Keys ["dashboard","sync-rate",days]
  / ["dashboard","drift-trend",days] / ["audit","recent"].
- Dark-only, semantic palette (green=success, amber=drift, red=error, blue=pending).

## Tests
- Backend (pytest + testcontainers, real PG18): test_metrics_service,
  test_dashboard_api (schema/empty/validation), test_drift_recorder (one row
  per check, atomic).
- Frontend Playwright: charts render, drift empty-state, feed + View all nav.

## Phasing
Day 1: sync-rate + feed real data; drift-trend empty→self-fills as checks accrue.
Deferred (substrate ready): `/metrics` OpenMetrics endpoint, drift_samples retention.
