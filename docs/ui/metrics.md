# Live host metrics (Grafana Mimir/Loki)

LabDog can show **instant** CPU, memory, and disk usage on each host's
**Overview** tab by querying a Grafana Mimir (or any Prometheus-compatible)
backend. These are single current values, not graphs — LabDog points you at
Grafana for history; it just surfaces "what is this host doing right now"
next to everything else it already shows about the host.

It closes the loop with the bundled **Install Alloy agent** action: register
your metrics backend once, run the action, and metrics flow back
automatically.

## The loop

1. **Register a Grafana instance** under **Integrations → Grafana**
   ([/grafana](/grafana)). Provide:
   - **Mimir / Prometheus query URL** — what LabDog queries, e.g.
     `http://mimir:9009/prometheus`.
   - **Prometheus remote-write URL** — where Alloy ships metrics, e.g.
     `http://mimir:9009/api/v1/push`.
   - **Loki push URL** (optional) — where Alloy ships logs.
   - Optional tenant (`X-Scope-OrgID`), bearer token, TLS verification and
     CA certificate.

   Use **Test connection** to confirm the query API is reachable before
   saving. The first instance you add becomes the **default** (the one the
   host page queries); you can change which is default at any time.

2. **Run the *Install Alloy agent* action** against a host or group
   (Actions tab). LabDog automatically:
   - fills the Alloy remote-write/Loki URLs from your default Grafana
     instance (no need to re-type them), and
   - injects two identity labels — `labdog_host_id` (the stable host id) and
     `labdog_hostname` — which Alloy stamps on every series it ships.

3. **Open the host's Overview tab.** The **Resource Usage** card shows three
   tiles (CPU / Memory / Disk). They auto-refresh every 15 seconds while the
   tab is visible.

Because metrics are matched on `labdog_host_id`, renaming a host or changing
its IP never detaches its metrics.

## States you may see

| State | Meaning |
|-------|---------|
| **No metrics backend configured** | No Grafana instance registered — a link takes you to set one up. |
| **No metrics found for this host yet** | Backend is configured but this host isn't shipping data — run *Install Alloy agent*, then allow a minute for the first scrape. |
| **Failed to query metrics** | The query backend was unreachable or rejected the request (check the instance's URL/token via **Test**). |
| **as of … (amber)** | The newest sample is older than two minutes — the agent may have stopped reporting. Last-known values are shown dimmed. |

## Thresholds

Tiles colour by usage: green below 75%, amber 75–89%, red at 90% or above.
Disk reports the root filesystem (`/`).

## Notes & limits

- Metrics come from node_exporter via Alloy's `prometheus.exporter.unix`.
- LabDog queries the **default** Grafana instance. Per-host routing to
  different backends is not yet supported.
- Log surfacing from Loki, network/per-mount metrics, and configurable
  thresholds are not part of this release.
