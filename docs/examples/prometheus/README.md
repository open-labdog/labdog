# Prometheus + Grafana examples for LabDog

Everything needed to scrape LabDog with an existing Prometheus stack and get
useful dashboards and alerts out of it.

| File | What it is |
|------|------------|
| [`labdog.alloy`](labdog.alloy) | Grafana Alloy `prometheus.scrape` block for LabDog (plus commented `remote_write` and basic-auth variants) |
| [`labdog-alerts.yml`](labdog-alerts.yml) | 14 alerting rules — availability, fleet state, sync health, scheduling, certificate expiry |
| [`labdog-overview.json`](labdog-overview.json) | Grafana dashboard — fleet, sync, drift, actions, certificates, exporter health |

Scraping is shown with **Alloy**, since LabDog already deploys it to managed
hosts via the bundled `alloy-install` action. The endpoint serves standard
Prometheus text exposition, so any compatible scraper works — point it at the
same URL. The alert rules and dashboard are ordinary Prometheus/Grafana
artifacts and apply either way.

Full reference: [`docs/metrics-export.md`](../../metrics-export.md).

> **Not to be confused with** [`docs/ui/metrics.md`](../../ui/metrics.md), which
> is the *opposite* direction — LabDog **reading** host CPU/memory/disk from a
> Grafana Mimir backend. This directory is about Prometheus **reading LabDog**.

## 60-second quickstart

**1. Enable the endpoint.** It is disabled by default. In
`/etc/labdog/labdog.toml`:

```toml
[metrics]
enabled = true
```

Restart LabDog, then verify:

```bash
curl -s http://labdog.example.com:8000/metrics | head -20
```

You should get `text/plain` starting with `# HELP labdog_build_info ...`. If you
get HTML back, the endpoint is disabled (LabDog returns 404, and your browser or
proxy may be serving the SPA fallback page instead).

> ⚠️ **The endpoint is unauthenticated.** Restrict it at your reverse proxy
> before exposing LabDog to an untrusted network — see
> [`docs/security-hardening.md`](../../security-hardening.md).

**2. Point Alloy at it.** Copy the `prometheus.scrape` block from
[`labdog.alloy`](labdog.alloy) into your Alloy config, replacing the target host
and pointing `forward_to` at the `remote_write` component you already have.
Reload Alloy and confirm the target is up on its `/-/targets` page (or in
Mimir, once the series land).

**3. Load the alerts.** [`labdog-alerts.yml`](labdog-alerts.yml) is a standard
Prometheus rule group — load it into the Mimir ruler, or reference it under
`rule_files:` if you run Prometheus. Review the thresholds first: they are tuned
for a small fleet, and `LabDogHostsOutOfSync` will be noisy if you routinely
leave hosts drifted.

**4. Import the dashboard.** In Grafana: **Dashboards → New → Import → Upload
JSON file**, choose [`labdog-overview.json`](labdog-overview.json), then pick
your Prometheus datasource when prompted.

## Notes on the dashboard

- It is **hand-authored**, not exported from a live Grafana. It targets
  schema version 39 (Grafana 10+) and imports cleanly, but if you customise it,
  re-export via **Share → Export → "Export for sharing externally"** and commit
  that — a real export is more faithful than hand-edited JSON.
- The `Module` template variable is populated from
  `label_values(labdog_host_modules, module)`, so it shows LabDog's **database**
  module vocabulary (`linux_user`, `hosts_file`) rather than the playbook
  spelling (`linux-users`, `hosts-file`). That is intentional — the metrics
  expose the column values verbatim.
- **"Drift check ERRORS by module"** has no equivalent in the LabDog UI. A drift
  check that *errors* is not the same as one that *finds drift* — the host's
  real state is unknown. The in-app drift trend counts only confirmed drift, so
  a check failing 100% of the time looks identical to a healthy one there. This
  panel is the reason to wire up alerting.
- Percentile panels use `histogram_quantile(...rate(..._bucket[$__rate_interval]))`.
  LabDog's histogram buckets are cumulative all-time counters sourced from
  PostgreSQL; differentiating them with `rate()` is what turns them into a
  recent-window latency view.

## Cardinality

The default export is roughly 250 series and does not include any per-host
label — that is deliberate (a 10,000-host fleet would otherwise produce hundreds
of thousands of series). Per-host telemetry belongs to the Alloy → Mimir path
documented in [`docs/ui/metrics.md`](../../ui/metrics.md).

The one knob that can grow the series count is the `action_key` label on
`labdog_action_runs_total`. If you run a very large number of distinct actions
and want to trade that detail away:

```toml
[metrics]
action_key_label = false
```
