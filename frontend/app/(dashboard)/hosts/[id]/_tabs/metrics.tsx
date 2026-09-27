"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { ActionDefinition, Host } from "@/lib/types"
import { Banner, Empty, Panel } from "@/components/ld"
import { RunActionButton } from "@/components/run-action-button"
import { MetricMeters, hasMetrics, isStale, relativeTime, useHostMetrics } from "@/components/host-metrics-section"

/** An action that installs an agent shipping metrics to Mimir — the
 *  default pack's alloy-install, or anything else whose manifest maps a
 *  Prometheus push URL. Found by manifest, not by key, so core never
 *  hardcodes a pack's action. */
const shipsMetrics = (a: ActionDefinition) => a.supports_host && !!a.metrics_backend?.prometheus_push_var

/**
 * The host's Metrics tab. Unlike the Overview strip, which stays silent
 * until metrics are set up, this tab is the one place that says why there
 * is nothing to show and what to do about it: register a Mimir instance,
 * pick a default among several, or run an agent on this host.
 */
export function MetricsTab({ hostId, host }: { hostId: number; host?: Host }) {
  const { query: { data, error }, now } = useHostMetrics(hostId)
  const { data: catalog } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 60_000,
  })
  const shippers = (catalog ?? []).filter(shipsMetrics)

  const stale = data ? isStale(data, now) : false

  let body: React.ReactNode
  if (error) {
    body = <Banner tone="danger">Could not load metrics: {error.message}</Banner>
  } else if (!data) {
    body = <span className="text-xs text-text-3">loading…</span>
  } else if (!data.configured && data.unconfigured_reason === "no_default") {
    body = (
      <Empty
        title="No default Mimir instance"
        note="Several Mimir instances are registered and none is marked as the default, so LabDog doesn't know which one to ask. Edit one and tick default."
        action={<Link href="/grafana" className="btn btn-sm btn-primary hover:no-underline">Grafana instances →</Link>}
      />
    )
  } else if (!data.configured) {
    body = (
      <Empty
        title="No metrics backend"
        note="This tab shows CPU, memory and disk usage from Grafana Mimir or another Prometheus-compatible backend. Register one, then install a metrics agent on this host."
        action={<Link href="/grafana" className="btn btn-sm btn-primary hover:no-underline">Add a Mimir instance…</Link>}
      />
    )
  } else if (!data.error && !hasMetrics(data)) {
    body = (
      <Empty
        title="No metrics from this host yet"
        note={
          shippers.length > 0 ? (
            <>
              Mimir is set up, but nothing labelled with this host has arrived. Run{" "}
              {shippers.length === 1 ? <b>{shippers[0].name}</b> : "a metrics agent action"} on it, then allow about a
              minute for the first scrape. This tab checks again every 15 seconds.
            </>
          ) : (
            <>
              Mimir is set up, but nothing labelled with this host has arrived. Add an action pack with an agent that
              labels its series with <span className="mono">labdog_host_id</span> — the default pack&apos;s{" "}
              <b>Install Alloy agent</b> does — and run it on this host.
            </>
          )
        }
        action={
          shippers.length > 0 && (
            <RunActionButton scope="host" targetId={hostId} targetLabel={host?.hostname} host={host} only={shipsMetrics} className="btn btn-sm btn-primary">
              {shippers.length === 1 ? `Run ${shippers[0].name}…` : "Run a metrics agent…"}
            </RunActionButton>
          )
        }
      />
    )
  } else if (data.error) {
    body = <Banner tone="danger">The metrics query failed: <span className="mono">{data.error}</span></Banner>
  } else {
    body = (
      <>
        {stale && data.sampled_at && (
          <Banner tone="warn">
            No new sample since {relativeTime(data.sampled_at, now)}. The agent on this host may have stopped, or Mimir may be
            unreachable from it.
          </Banner>
        )}
        <MetricMeters data={data} dim={stale} detail />
      </>
    )
  }

  const sampled = data?.configured && data.sampled_at ? `sampled ${relativeTime(data.sampled_at, now)}` : undefined

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <Panel title="resource usage" meta={sampled} pad={11}>
        <div className="flex flex-col gap-3">{body}</div>
      </Panel>
    </div>
  )
}
