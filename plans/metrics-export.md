# plan: exportable metrics (OpenMetrics)

Branch-scoped scratchpad (delete before PR, per CONTRIBUTING.md).

## Goal
A Prometheus-scrapable `/metrics` endpoint so existing Prometheus + Grafana
stacks can consume LabDog directly. Covers both **fleet state** (hosts by
status, drift, desired-state sizes) and **LabDog self-health** (sync/action
outcomes, durations, queue depth, scheduler + pack health).

## Confirmed decisions
- **Unauthenticated, opt-in.** Disabled by default via `[metrics] enabled`.
  Open when enabled, like `GET /api/version`. Protection = reverse proxy.
  Deliberately NOT a DB-backed `AppSetting`: `/api/settings` gates on
  `current_active_user` (not superuser), so a DB toggle would let any
  authenticated session expose fleet state with one PATCH.
- **UI on the existing Grafana page** (`/grafana`), with explicit
  "Metrics in" / "Metrics out" headings so the two directions don't blur.
- One PR: backend + frontend + docs + examples + tests.

## Key design points
- **`app/metrics/service.py` is NOT reusable.** It returns `date_trunc` time
  series; Prometheus needs point-in-time values (the TSDB does its own
  bucketing, and back-dated samples are rejected as out-of-order on the second
  scrape). New `aggregates.py` holds point-in-time SQL. Five docstrings that
  claimed otherwise are corrected in this PR.
- **Two module vocabularies coexist.** DB columns use
  `firewall, package, service, cron, linux_user, hosts_file, resolver` (+`bulk`);
  `ansible_runtime/composer.py` `CANONICAL_ORDER` uses different spellings.
  Emit DB values verbatim; never import `CANONICAL_ORDER`.
- **Hand-rolled exposition renderer, no runtime dep.** `prometheus_client`'s
  value is its registry, which we can't use (sync `collect()` vs async
  aggregation), and its in-process counters are wrong under multiple uvicorn
  workers. Added to dev extras only, to validate our output in a test.
- **Prometheus text 0.0.4**, not OpenMetrics 1.0 (universally parsed; 1.0's
  `# EOF` + counter-naming differences are a common footgun). Additive later.
- **All-time counters, no time predicate** → monotonic across restarts and
  identical across workers. Recent-window dashboards still work because
  `histogram_quantile(0.95, sum(rate(..._bucket[15m])) by (le))` differentiates
  the cumulative buckets.
- **Sequential queries** (~13/scrape) + 15s TTL cache with single-flight.
  Never `asyncio.gather` on one asyncpg session.
- **Route registered unconditionally**, branching on `enabled` inside → 404 when
  disabled. Conditional registration would fall through to the SPA catch-all and
  return `index.html` with HTTP 200.

## Cardinality
No `host_id`/`hostname` labels ever (10k hosts × 7 modules × 5 statuses = 350k
series). Per-host telemetry already lives in the Alloy→Mimir path. No free-text
labels (`error_message`, `pending_reason`, `session_id`, IPs, usernames).
~250 series baseline; `action_key` label is togglable via
`[metrics] action_key_label`.

## Follow-ups (→ TODO.md, not this PR)
1. Redis broker queue depth + `labdog_broker_reachable` (200ms timeout).
2. `drift_samples` retention + `drift_sample_rollup` — naive retention breaks
   counter monotonicity; rollup must be incremented in the same transaction as
   the delete. Write `aggregates.py` so this is a one-line UNION ALL change.
3. `ix_sync_jobs_created_at` — for `/api/dashboard/*` (which filters on
   `created_at`), NOT the exporter (all-time counters use no time predicate).
4. Unify `HostModuleStatus.sync_status` `drifted` vs `out_of_sync`.
5. Rename `docs/ui/metrics.md` → `docs/ui/host-metrics.md`.
6. True OpenMetrics 1.0 via `Accept` content negotiation.
