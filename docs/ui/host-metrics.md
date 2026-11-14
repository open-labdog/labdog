# Live host metrics (Grafana Mimir/Loki)

> **Two different features share the word "metrics".**
> This page is about unbork **reading** per-host CPU / memory / disk *inward*
> from a Grafana Mimir backend.
> For unbork **exposing** its own fleet state and health *outward* so Prometheus
> can scrape it, see [Metrics export](../metrics-export.md).
> They are independent — you can use either, both, or neither.

unbork can show **instant** CPU, memory, and disk usage on each host's page
by querying a Grafana Mimir (or any Prometheus-compatible)
backend. These are single current values, not graphs — unbork points you at
Grafana for history; it just surfaces "what is this host doing right now"
next to everything else it already shows about the host.

It closes the loop with the **Install Alloy agent** action from `labdog-playbooks`: register
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
     path your setup needs). unbork hands this to the Alloy install action
     as-is, and for querying it strips the path down to the host and appends
     the right API path automatically (Mimir →
     `…/prometheus/api/v1/query`). You never enter the query URL.
   - Optional tenant (`X-Scope-OrgID`). On a Mimir instance it defaults to
     `"anonymous"` if left blank, which is the conventional single-tenant
     value and matches the Alloy agent's own default. On a Loki instance a
     blank tenant sends no header. Either is also what the Alloy action
     ships with, so a multi-tenant Mimir or Loki works through it. Two
     tenants on the same URL are two instances.
   - Optional authentication (none, bearer token, or basic
     username/password), TLS verification and a CA certificate (PEM) for a
     private CA. unbork uses these when it **queries**. Of them, only
     *TLS verification* reaches the Alloy action today (as
     `insecure_skip_verify`); a backend that needs a token, basic auth or a
     private CA for **ingest** can't yet be set up through the action.

   Use **Test connection** to confirm the (derived) query API is reachable
   before saving; each row also has **test**. The first instance of each kind
   becomes that kind's **default**: the destination of every host that
   doesn't have one of its own (see
   [Per-host and per-group destinations](#per-host-and-per-group-destinations)).
   Tick **default** on another instance's edit dialog to move it.

2. **Run the *Install Alloy agent* action** against a host or group
   (**Run action…** on its page). unbork automatically:
   - fills each host's Alloy remote-write/Loki URLs, tenants and TLS
     setting from the instances that host ships to (no need to re-type
     them). The run dialog's destination pickers start on **Each host's
     assigned destination**; picking an instance there sends every host in
     that run to it instead, and
   - injects two identity labels — `labdog_host_id` (the stable host id) and
     `labdog_hostname` — which Alloy stamps on every series it ships.

3. **Open the host's page.** Once a Mimir instance is registered, three
   meters — cpu, memory and disk `/` — appear at the top of the Overview
   tab's host panel and in the **Metrics** tab's *resource usage* panel,
   refreshing every 15 seconds while the page is visible. The Metrics tab
   prints the absolute figures (cores, bytes) under each meter and says when
   the newest sample was taken; on the Overview, hover a meter for them.
   Until metrics arrive, the Overview shows nothing and the Metrics tab says
   what is missing — see below.

Because metrics are matched on `labdog_host_id`, renaming a host or changing
its IP never detaches its metrics.

## Per-host and per-group destinations

Hosts don't all have to ship to the same backend. A host or a group can
name its own Mimir and Loki instance, in the **metrics destinations**
panel: on a host's **Metrics** tab, and at the bottom of a group's
**Overview**. A host's destination, per kind, is decided the same way as
every other merged setting:

1. the host's own choice, if it has one;
2. otherwise the highest-priority group the host belongs to that names one
   (a group that leaves it blank has no opinion and is skipped);
3. otherwise the default instance of that kind.

A host with no choice and no group choice therefore behaves exactly as
before. The host panel says where the host's data currently goes and why
("from group *edge*", "the default").

Everything that reads a host's data follows the same rule: the host page,
the Overview's fleet resource hot spots (each host is read from its own
destination only), the assistant's metrics and log tools, and the
Alertmanager poll, which reads every Mimir some host ships to.

Saving a destination changes what unbork **queries** at once, but the host
keeps shipping where its Alloy config says until the **Install Alloy
agent** action runs on it again. Run the action after changing a
destination. An instance that a host or group still names can't be deleted;
reassign them first.

## States you may see

The Overview stays quiet about metrics until they work — it is the host's
front page, and a set-up prompt on every host would be noise. The
**Metrics** tab is where each state is explained.

| Situation | Metrics tab | Overview | What to do |
|-----------|-------------|----------|------------|
| No Mimir instance registered | **No metrics backend**, with **Add a Mimir instance…** | nothing | Add one under Settings › Integrations → Grafana. |
| Several Mimir instances, none marked default | **No default Mimir instance** | nothing | Edit one of them and tick **default**. unbork won't guess which to ask. |
| Mimir set up, nothing from this host | **No metrics from this host yet**, with a button to run the metrics agent action — **Run Install Alloy agent…** with the default pack | **no metrics yet** | Run the action on the host, then allow a minute for the first scrape. The tab checks again every 15 seconds. |
| The query failed | **The metrics query failed:** and the backend's error | **query error** — hover for the error | Check the instance's URL and token with **test** on the Grafana page. |
| No new sample for two minutes | A banner saying how long since the last sample; the last values dimmed | **stale** (amber), values dimmed | The agent may have stopped, or can't reach Mimir from the host. |

The run button offers any action whose manifest maps a Prometheus push URL
(`metrics_backend.prometheus_push_var`), not the Alloy action by name. With
no such action in any pack, the tab says what the agent needs to do instead:
label its series with `labdog_host_id`.

## Thresholds

Meters colour by usage: green up to 75%, amber above 75%, red above 90%.
Disk reports the root filesystem (`/`).

## Notes & limits

- Metrics come from node_exporter via Alloy's `prometheus.exporter.unix`.
- unbork queries the Mimir each host ships to (see
  [Per-host and per-group destinations](#per-host-and-per-group-destinations)).
- Ingest authentication (token, basic auth) and a private CA are not passed
  to the Alloy action yet; only TLS verification is.
- Log surfacing from Loki, network/per-mount metrics, and configurable
  thresholds are not part of this release.
