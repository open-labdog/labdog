# 0.4.0 — Grafana Mimir/Loki integration (instant host metrics)

Branch-scoped scratchpad. **Delete before opening the PR.**

## Goal
Instant CPU / memory / disk usage on the host page, fetched from a
user-registered Grafana Mimir (Prometheus-compatible) endpoint. No
graphs — single instant values. Closes the loop with the bundled
`alloy-install` action pack: register a Mimir/Loki instance → run
alloy-install → metrics flow back automatically.

## Synthesis of UI + UX agent proposals
Both agents independently landed on: clone the **Proxmox integration
pattern** (model + CRUD + encrypted secret + audit + Settings page +
Test), one combined Grafana integration holding Mimir+Loki URLs, three
instant tiles on the host overview tab with green/amber/red thresholds
and explicit empty/error/stale states, 15s auto-refresh while visible,
and contextual CTAs as the discovery path. Full proposals are in the
agent transcripts; key load-bearing decisions distilled below.

## Decisions (MVP for 0.4.0)

### Identity label (the linchpin)
Alloy stamps **two external labels** at `remote_write` so every series
is queryable back by LabDog:
- `labdog_host_id` = LabDog DB host id — **the query key** (stable,
  unique, rename-proof; the host page is already keyed on it).
- `labdog_hostname` = hostname at install time — human-readable only.

LabDog queries `...{labdog_host_id="<id>"}`. These are **injected by
LabDog at dispatch**, never operator-facing.

### Grafana integration (new `grafana_instances` table)
Shaped 1:1 on `ProxmoxNode`. Fields:
- `name` (unique)
- `prometheus_query_url` — what **LabDog queries** (e.g.
  `http://mimir:9009/prometheus`). Required.
- `prometheus_push_url` — what **Alloy remote-writes** to (e.g.
  `http://mimir:9009/api/v1/push`). Required.
- `loki_push_url` — Alloy logs target. Optional (metrics-only allowed).
- `org_id` — `X-Scope-OrgID` tenant. Optional.
- `encrypted_token` — optional bearer token, AES-256-GCM (Proxmox
  pattern; never returned, audit-logged without the secret).
- `verify_ssl` (bool) + `ca_cert_pem` (plaintext, public).
- `is_default` (bool) — exactly one default.

### Host ↔ backend mapping — MVP simplification
**MVP queries the default (or sole) Grafana instance filtered by
`labdog_host_id`.** Because Alloy stamps `labdog_host_id`, the default
instance already returns the right host's series — no per-host FK or
post-run hook needed. (Multi-backend per-host routing via a
`host.metrics_instance_id` FK set post-run is the UX agent's richer
design; deferred — noted in TODO.)

### URL + label injection into the alloy run
- The executor **always** injects `labdog_host_id` / `labdog_hostname`
  extra-vars for per-host action runs (cheap, generally useful).
- A manifest opts in to URL injection via a new optional field that maps
  LabDog's default Grafana instance URLs onto the pack's var names, so
  LabDog core stays generic (no hardcoded `alloy_*`):
  ```yaml
  metrics_backend:
    prometheus_push_var: alloy_prometheus_url
    loki_push_var: alloy_loki_url
    org_id_var: alloy_mimir_org_id
  ```
  Dispatch fills those vars from the default instance unless the operator
  supplied them explicitly.

### PromQL (node_exporter via `prometheus.exporter.unix`)
- CPU %: `100 - avg(rate(node_cpu_seconds_total{labdog_host_id="$id",mode="idle"}[2m]))*100`
- Mem %: `100*(1 - node_memory_MemAvailable_bytes{labdog_host_id="$id"}/node_memory_MemTotal_bytes{...})`
- Disk %: root fs — `100*(1 - node_filesystem_avail_bytes{labdog_host_id="$id",mountpoint="/",fstype!~"tmpfs|overlay"}/node_filesystem_size_bytes{...})`

Instant query: `GET {prometheus_query_url}/api/v1/query?query=...` with
`X-Scope-OrgID` + optional bearer.

### Host page UI
New `HostMetricsSection` at the top of the overview tab: 3 tiles
(CPU/Memory/Disk), value + slim `UsageBar`, green `<75` / amber `75–89`
/ red `>=90`, "as of" staleness. States: not-configured (CTA →
/grafana), no-data-yet, query-error, stale. Refresh: 15s
`refetchInterval`, paused when hidden; existing tab Refresh also pulls.

### Settings page
New `/grafana` route + sidebar INTEGRATIONS entry, cloned from the
Proxmox settings client-page (DataTable + Dialog form + per-row Test +
pre-save Test).

### Data contract
`GET /api/grafana/hosts/{id}/metrics` →
`{ configured, sampled_at, cpu{percent,...}|null, memory{...}|null, disk{...}|null }`.

## Build order
1. Backend: model + alembic migration.
2. Backend: schemas + CRUD API + Test endpoint + audit.
3. Backend: Prometheus query client + `/grafana/hosts/{id}/metrics`.
4. Dispatch: inject identity labels (+ metrics_backend URL mapping).
5. Frontend: types + api + `/grafana` settings page + sidebar.
6. Frontend: `HostMetricsSection` + `UsageBar` on overview tab.
7. labdog-playbooks: external_labels in endpoints.alloy.j2 +
   `metrics_backend` manifest block on alloy-install.
8. Docs (`docs/ui/metrics.md`), tests, TODO/CHANGELOG.

## Deferred (note in TODO, not 0.4.0)
- Per-host `metrics_instance_id` FK + post-run linking (multi-backend).
- Loki log surfacing on the host page.
- Network metrics, per-mount disk, configurable thresholds/interval.
