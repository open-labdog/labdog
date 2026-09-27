# Live host metrics (Grafana Mimir/Loki)

> **Two different features share the word "metrics".**
> This page is about LabDog **reading** per-host CPU / memory / disk *inward*
> from a Grafana Mimir backend.
> For LabDog **exposing** its own fleet state and health *outward* so Prometheus
> can scrape it, see [Metrics export](../metrics-export.md).
> They are independent — you can use either, both, or neither.

LabDog can show **instant** CPU, memory, and disk usage on each host's page
by querying a Grafana Mimir (or any Prometheus-compatible)
backend. These are single current values, not graphs — LabDog points you at
Grafana for history; it just surfaces "what is this host doing right now"
next to everything else it already shows about the host.

It closes the loop with the bundled **Install Alloy agent** action: register
your metrics backend once, run the action, and metrics flow back
automatically.

## The loop

1. **Register your endpoints** under **Settings › Integrations → Grafana**
   (`/grafana`), with **Add instance…**. Mimir (metrics) and Loki (logs) are registered
   **separately** — add one instance per endpoint. For each, provide:
   - **Name** — a label for the instance.
   - **Kind** — Mimir / Prometheus (metrics) or Loki (logs).
   - **Ingest URL** — a single URL: the remote-write / push URL the agent
     ships to, e.g. `https://mimir.example.com/api/v1/push` (add whatever
     path your setup needs). LabDog hands this to the Alloy install action
     as-is, and for querying it strips the path down to the host and appends
     the right API path automatically (Mimir →
     `…/prometheus/api/v1/query`). You never enter the query URL.
   - Optional tenant (`X-Scope-OrgID`) — defaults to `"anonymous"` if left
     blank, which is the conventional single-tenant value and matches the
     Alloy agent's own default. Set it explicitly only if your Mimir/Loki
     uses a different tenant.
   - Optional authentication (none, bearer token, or basic
     username/password), TLS verification and a CA certificate (PEM) for a
     private CA.

   Use **Test connection** to confirm the (derived) query API is reachable
   before saving; each row also has **test**. The first instance of each kind
   becomes that kind's **default** — the Mimir LabDog queries for the host
   page, and the Mimir/Loki it feeds the Alloy action. Tick **default** on
   another instance's edit dialog to move it.

2. **Run the *Install Alloy agent* action** against a host or group
   (**Run action…** on its page). LabDog automatically:
   - fills the Alloy remote-write/Loki URLs from your default Grafana
     instance (no need to re-type them), and
   - injects two identity labels — `labdog_host_id` (the stable host id) and
     `labdog_hostname` — which Alloy stamps on every series it ships.

3. **Open the host's page.** Once a Mimir instance is registered, three
   meters — cpu, memory and disk `/` — appear at the top of the Overview
   tab's host panel and in the **Metrics** tab's *resource usage* panel,
   refreshing every 15 seconds while the page is visible. Hover a meter for
   the absolute figures (cores, bytes). With no Mimir instance configured,
   the Overview shows nothing and the Metrics panel stays empty.

Because metrics are matched on `labdog_host_id`, renaming a host or changing
its IP never detaches its metrics.

## States you may see

| State | Meaning |
|-------|---------|
| **Nothing shown** | No Mimir instance is registered. Add one under Settings › Integrations → Grafana. |
| **no metrics yet** | A Mimir instance is configured but this host isn't shipping data — run *Install Alloy agent*, then allow a minute for the first scrape. |
| **query error** | The query backend was unreachable or rejected the request (check the instance's URL/token with **test**). |
| **stale** (amber) | The newest sample is older than two minutes — the agent may have stopped reporting. Last-known values are shown dimmed; hover for when the last sample was. |

## Thresholds

Meters colour by usage: green up to 75%, amber above 75%, red above 90%.
Disk reports the root filesystem (`/`).

## Notes & limits

- Metrics come from node_exporter via Alloy's `prometheus.exporter.unix`.
- LabDog queries the **default Mimir** instance. Per-host routing to
  different backends is not yet supported.
- Log surfacing from Loki, network/per-mount metrics, and configurable
  thresholds are not part of this release.
