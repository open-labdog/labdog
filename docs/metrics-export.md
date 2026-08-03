# Metrics export (Prometheus)

LabDog can expose its own fleet state and internal health in the Prometheus
text exposition format, so an existing Prometheus + Grafana stack can scrape it
directly — no extra agent, no extra database.

> **Two different features share the word "metrics".**
> This page is about LabDog **exposing** metrics *outward* (Prometheus scrapes
> LabDog).
> For LabDog **reading** per-host CPU / memory / disk *inward* from a Grafana
> Mimir backend, see [Live host metrics](ui/metrics.md).
> They are independent — you can use either, both, or neither.

Ready-made scrape config, alert rules and a Grafana dashboard live in
[`docs/examples/prometheus/`](examples/prometheus/).

---

## Enabling

The endpoint is **disabled by default**. In `/etc/labdog/labdog.toml`:

```toml
[metrics]
enabled = true

# Optional:
# cache_ttl_seconds = 15.0    # reuse a collected snapshot for this long
# action_key_label  = true    # label action metrics by action_key
```

or via environment variable:

```bash
LABDOG_METRICS__ENABLED=true
```

Restart LabDog, then verify:

```bash
curl -i http://labdog.example.com:8000/metrics
```

Expect `HTTP/1.1 200` and
`Content-Type: text/plain; version=0.0.4; charset=utf-8`.

The current status, the exact scrape URL and a copy-paste `prometheus.yml`
snippet are also shown in the UI under **Integrations → Grafana**, in the
**Metrics out — Prometheus scrape** card.

### Why this is a config-file setting and not a UI toggle

Enabling the endpoint publishes fleet-wide information to the network without
authentication. LabDog's in-app settings API is available to *any* signed-in
user, not just superusers — so a UI toggle would let any authenticated session
(or a hijacked one) expose that data with a single request. Requiring a config
file edit and a restart keeps that decision at the same trust level as the
reverse-proxy configuration that protects it.

---

## Security

**When enabled, `/metrics` is unauthenticated**, exactly like `GET /api/version`.
Prometheus cannot use LabDog's cookie session auth, and shipping a
half-authenticated scrape path invites misconfiguration, so the endpoint is
deliberately open-and-opt-in rather than gated.

**Restrict it at your reverse proxy.** See
[Security hardening → Exposing /metrics](security-hardening.md) for nginx and
Caddy recipes.

### What is disclosed

| Exposed | Not exposed |
|---------|-------------|
| Aggregate counts (hosts by status, rules per module, jobs by outcome) | Hostnames, IP addresses, MAC addresses |
| Module names, action keys, group/scan-config counts | Usernames, emails, user IDs |
| CA certificate **names** and truncated fingerprints, with expiry dates | Certificate material, SSH keys, tokens, any secret |
| Timing histograms and staleness ages | Error messages, command text, session transcripts |
| Build version, commit SHA, build date | Any per-host row-level detail |

The export is deliberately aggregate-only. There is no per-host label anywhere
(see [Cardinality](#cardinality)), so `/metrics` cannot be used to enumerate
your fleet.

---

## Scraping

Minimal config (full version in
[`examples/prometheus/prometheus.yml`](examples/prometheus/prometheus.yml)):

```yaml
scrape_configs:
  - job_name: labdog
    scrape_interval: 30s
    metrics_path: /metrics
    static_configs:
      - targets: ["labdog.example.com:8000"]
```

LabDog aggregates from PostgreSQL on each scrape and caches the result for
`cache_ttl_seconds` (default 15s). Scraping faster than the TTL is harmless but
pointless; multiple Prometheus servers scraping the same instance share the
cache.

`/metrics` is exempt from LabDog's global API rate limit. Without that
exemption a scraper arriving through the same reverse proxy as user traffic
would share one rate-limit bucket with every logged-in operator, and monitoring
would break exactly when the UI was busiest.

---

## Metric reference

All metric names are prefixed `labdog_`. Counters carry `_total`, durations are
in seconds, and absolute times are Unix timestamps in seconds.

Label values for `module`, `action_key` and module-level `sync_status` are
emitted **verbatim from the database**. Note LabDog carries two module
spellings: the database uses `firewall`, `package`, `service`, `cron`,
`linux_user`, `hosts_file`, `resolver` (and `bulk` for coalesced syncs), whereas
playbooks use `packages`, `hosts-file`, `linux-users`. Metrics always use the
database spelling.

### Fleet state

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `labdog_build_info` | gauge | `version`, `commit`, `build_date` | Always `1`; build metadata carried on labels |
| `labdog_hosts` | gauge | `sync_status` | Managed hosts by aggregate status (`pending`, `in_sync`, `out_of_sync`, `unknown`, `error`) |
| `labdog_hosts_firewall_backend` | gauge | `backend` | Hosts by detected backend (`nftables`, `iptables`, `unknown`) |
| `labdog_hosts_drift_check_enabled` | gauge | — | Hosts with automatic drift checking enabled |
| `labdog_hosts_never_synced` | gauge | — | Hosts that have never completed a sync |
| `labdog_hosts_never_drift_checked` | gauge | — | Hosts that have never been drift-checked |
| `labdog_host_last_sync_age_seconds_max` | gauge | — | Age of the least-recently-synced host |
| `labdog_host_last_drift_check_age_seconds_max` | gauge | — | Age of the least-recently-checked host |
| `labdog_host_modules` | gauge | `module`, `sync_status` | Per-host per-module state, counted |
| `labdog_module_rules` | gauge | `module` | Desired-state rules defined per module |
| `labdog_groups` | gauge | — | Host groups |
| `labdog_groups_gitops` | gauge | `status` | Groups by GitOps status |
| `labdog_group_gitops_last_import_age_seconds_max` | gauge | — | Stalest GitOps import among enabled groups |
| `labdog_ca_cert_not_after_timestamp_seconds` | gauge | `name`, `fingerprint` | CA certificate expiry, as a Unix timestamp |
| `labdog_scan_pending_hosts` | gauge | — | Discovered hosts awaiting review |
| `labdog_scan_configs` | gauge | `state` | Scan configurations enabled/disabled |
| `labdog_scan_config_last_run` | gauge | `status` | Scan configs by last-run outcome |
| `labdog_users` | gauge | `state` | User accounts active/inactive |
| `labdog_superusers` | gauge | — | Accounts with superuser rights |
| `labdog_git_repositories` | gauge | `auth_type` | Configured Git repositories |
| `labdog_grafana_instances` | gauge | `kind` | Registered Mimir/Loki backends |
| `labdog_vm_mappings` | gauge | — | Hosts mapped to a Proxmox VM (snapshot-capable) |

### Sync and drift

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `labdog_sync_jobs_total` | counter | `status`, `module` | Sync jobs ever recorded |
| `labdog_sync_jobs_inflight` | gauge | `status`, `module` | Sync jobs currently `pending` or `running` |
| `labdog_sync_job_duration_seconds` | histogram | — | Wall time from start to completion |
| `labdog_stale_sync_jobs` | gauge | — | Jobs stuck `running` past the sweeper threshold |
| `labdog_hosts_queue_blocked` | gauge | — | Hosts with a backed-up operation queue |
| `labdog_drift_checks_total` | counter | `module`, `result` | Drift checks ever performed |
| `labdog_drift_changes_total` | counter | `module`, `kind` | Individual drift deltas (`add`, `remove`, `policy_change`) |
| `labdog_drift_check_duration_seconds` | histogram | `module` | Drift-check wall time |

`labdog_drift_checks_total`'s `result` label is the recorded outcome verbatim,
which includes **`error`** — a check that failed to run. An erroring check is
*not* the same as detected drift: the host's true state is unknown. This is
worth alerting on, and it is a signal the in-app drift trend does not surface.

### Actions, scheduling and packs

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `labdog_action_runs_total` | counter | `action_key`, `status` | Action runs ever dispatched |
| `labdog_action_runs_inflight` | gauge | `status` | Action runs queued/pending/running |
| `labdog_action_runs_scheduled_total` | counter | `origin` | Runs dispatched from a schedule, split by what triggered them: `cron` (the scheduler tick) or `manual` (someone pressed *Run now*) |
| `labdog_action_host_runs_total` | counter | `status` | Per-host action executions ever |
| `labdog_action_host_runs_nonzero_exit_total` | counter | — | Per-host executions with a non-zero exit code |
| `labdog_scheduled_actions` | gauge | `target_kind`, `state` | Schedules by target and enabled state |
| `labdog_scheduled_actions_orphaned` | gauge | — | **Enabled schedules whose action has no registry winner** |
| `labdog_scheduled_action_last_dispatch_age_seconds_max` | gauge | — | Stalest enabled schedule's last dispatch |
| `labdog_action_packs` | gauge | `sync_status` | Enabled packs by last sync outcome |
| `labdog_action_pack_last_sync_age_seconds_max` | gauge | — | Stalest enabled pack sync |
| `labdog_action_registry_keys` | gauge | — | Action keys with a resolved winner |
| `labdog_action_registry_computed_timestamp_seconds` | gauge | — | Last registry rebuild |
| `labdog_action_resolutions` | gauge | `origin` | Action-key conflict pins: `user` (an operator picked the winning pack) or `frozen` (auto-pinned when a sync introduced a fresh conflict, so behaviour didn't silently flip — these are the ones nobody has reviewed) |

`labdog_scheduled_actions_orphaned` is the highest-value alert here: an
orphaned schedule fires on time and silently does nothing, because its
`action_key` has no winner in the registry.

### Exporter self-health

| Metric | Type | Description |
|--------|------|-------------|
| `labdog_metrics_scrape_duration_seconds` | gauge | Wall time of the last full collection |
| `labdog_metrics_cache_age_seconds` | gauge | Age of the snapshot served (0 on a fresh collection) |
| `labdog_metrics_scrape_errors_total` | counter | Collections that raised — **process-local**, resets on restart |

---

## Counter semantics

Every counter except `labdog_metrics_scrape_errors_total` is computed as a
`COUNT(*)` or `SUM()` over the whole table with **no time window**. That has
three useful consequences:

- **Restarts don't reset them** — the value comes from PostgreSQL.
- **Multiple workers agree** — every uvicorn worker reads the same database and
  returns identical numbers, so it doesn't matter which one a scrape lands on.
- **Celery workers are irrelevant** to the read path — they write rows; the API
  process reads them.

They can decrease in three situations, which Prometheus treats as a counter
reset (`rate()` handles this correctly; `increase()` across the event
under-reports):

1. **Deleting a host** cascades to its sync jobs, drift samples and per-host
   action runs.
2. **Future history pruning**, if retention is added to those tables.
3. **Restoring the database** from an older backup.

`labdog_metrics_scrape_errors_total` is the deliberate exception: it counts
failures inside the exporter itself, lives in process memory, and resets when
LabDog restarts.

### Why all-time counters still give you recent dashboards

Because Prometheus differentiates them. `rate()` and `increase()` operate on the
*change* in a counter over the query window, so an all-time total is exactly
what they want. The same applies to histograms:

```promql
histogram_quantile(0.95, sum(rate(labdog_sync_job_duration_seconds_bucket[15m])) by (le))
```

gives p95 sync duration **over the last 15 minutes**, even though the underlying
bucket counters are cumulative since the beginning of time.

---

## Histogram caveat

`labdog_drift_check_duration_seconds_count` is **not** the number of drift
checks. Drift-check duration is nullable — checks recorded before timing was
added, or by code paths that don't time themselves, have no duration and are
excluded from the histogram.

Use `labdog_drift_checks_total` to count checks, and
`labdog_drift_check_duration_seconds` only for latency.

Similarly, `labdog_sync_job_duration_seconds` covers only jobs that recorded
both a start and a completion time.

---

## Cardinality

The default export is roughly **250 series**, and there is deliberately **no
per-host label anywhere**. Adding one would multiply out badly: a 10,000-host
fleet with 7 modules and 5 states would produce 350,000 series from a single
metric family. Per-host telemetry has its own home — the Grafana Alloy → Mimir
path documented in [Live host metrics](ui/metrics.md), where each series is
already tagged with `labdog_host_id`.

Free-text values are never used as labels either: error messages, pending
reasons, session IDs, IP addresses, package names and usernames are all
unbounded, and would turn a scrape into a cardinality incident.

The one adjustable dimension is `action_key` on `labdog_action_runs_total`,
which scales with the number of distinct actions you run (typically 10–100).
To trade it away:

```toml
[metrics]
action_key_label = false
```

---

## Grafana dashboard and alerts

- **Dashboard:** [`examples/prometheus/labdog-overview.json`](examples/prometheus/labdog-overview.json)
  — import via **Dashboards → New → Import**, then select your Prometheus
  datasource. Covers fleet state, sync throughput and latency, queue health,
  drift (including the error rate), action failures, scheduling and pack health,
  certificate expiry, and exporter cost.
- **Alerts:** [`examples/prometheus/labdog-alerts.yml`](examples/prometheus/labdog-alerts.yml)
  — 14 rules. Review the thresholds before relying on them; they assume a small
  fleet where persistent drift is unusual.

---

## Troubleshooting

**`/metrics` returns HTML.** The endpoint is disabled, so LabDog returns 404 and
something upstream (or your browser) is showing the SPA fallback. Check
`[metrics] enabled` and that LabDog was restarted after the change.

**`/metrics` returns 404 with an empty body.** Working as designed — the feature
is off. Enable it as above.

**Target is `DOWN` with a 429.** Rate limiting shouldn't apply to `/metrics`, so
this usually means a proxy in front of LabDog is doing its own limiting.

**Collection is slow** (`labdog_metrics_scrape_duration_seconds` climbing).
Collection runs roughly a dozen aggregate queries. Sustained slowness normally
means a large history table would benefit from pruning or an index; raising
`cache_ttl_seconds` reduces how often the work happens.

**Values look stale by a few seconds.** Expected — that's `cache_ttl_seconds`.
`labdog_metrics_cache_age_seconds` tells you the age of the snapshot you got.

---

## Design notes

- **Format is Prometheus text exposition `0.0.4`**, not strict OpenMetrics 1.0.
  0.0.4 is parsed by everything (Prometheus, VictoriaMetrics, Grafana Alloy, the
  OpenTelemetry receiver, Telegraf), while OpenMetrics 1.0 changes counter
  naming rules and requires an `# EOF` terminator — a well-known source of
  "half my metrics disappeared". OpenMetrics can be added later as an additive
  content-negotiated branch.
- **No `prometheus_client` dependency.** That library's value is its in-process
  registry, which doesn't fit here: LabDog derives every value from SQL at
  scrape time, and in-process counters would be *wrong* under multiple uvicorn
  workers (each worker would hold its own partial counts). The renderer is a
  small dependency-free module; the library is used in the test suite only, to
  validate that LabDog's output parses correctly.
- **The snapshot cache is per-process, and that is correct** rather than merely
  tolerable — since every value is read from the database, all workers produce
  identical output regardless of which one serves a given scrape.
- **Celery worker internals are not exported.** Inspecting live workers requires
  a broadcast RPC with a multi-second timeout, which has no place in a scrape
  path. Use [`celery-exporter`](https://github.com/danihodovic/celery-exporter)
  alongside LabDog if you need queue and worker telemetry.
