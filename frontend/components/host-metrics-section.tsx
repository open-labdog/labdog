"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Meter } from "@/components/ld"
import type { HostMetrics, HostMetricValue } from "@/lib/types"

const STALE_AFTER_MS = 120_000

function formatBytes(n: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"]
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function relativeTime(iso: string, now: number): string {
  const secs = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`
  return `${Math.round(secs / 3600)}h ago`
}

function subLine(m: HostMetricValue): string | undefined {
  if (m.unit === "cores" && m.used != null && m.total != null) return `${m.used.toFixed(1)} / ${m.total} cores`
  if (m.unit === "bytes" && m.used != null && m.total != null) return `${formatBytes(m.used)} / ${formatBytes(m.total)}`
  return undefined
}

export function hasMetrics(data: HostMetrics): boolean {
  return data.cpu != null || data.memory != null || data.disk != null
}

export function isStale(data: HostMetrics, now: number): boolean {
  return data.sampled_at != null && now - new Date(data.sampled_at).getTime() > STALE_AFTER_MS
}

/** The host's instant metrics, refetched every 15s while the page is
 *  visible, and a clock that ticks with it for the staleness check. The
 *  Overview strip and the Metrics tab share the query key, so switching
 *  between them doesn't refetch. */
export function useHostMetrics(hostId: number) {
  const query = useQuery<HostMetrics>({
    queryKey: ["host-metrics", String(hostId)],
    queryFn: () => apiFetch<HostMetrics>(`/api/grafana/hosts/${hostId}/metrics`),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })

  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])

  return { query, now }
}

/** CPU, memory and root-disk meters. `detail` prints the absolute figures
 *  under each bar; without it they are the bar's tooltip. */
export function MetricMeters({ data, dim, detail }: { data: HostMetrics; dim?: boolean; detail?: boolean }) {
  const cells: [string, HostMetricValue | null][] = [["cpu", data.cpu], ["memory", data.memory], ["disk /", data.disk]]
  return (
    <div className={`grid grid-cols-1 gap-3 sm:grid-cols-3 ${dim ? "opacity-60" : ""}`}>
      {cells.map(([label, m]) => (
        <div key={label} title={!detail && m ? subLine(m) : undefined}>
          <Meter pct={m?.percent ?? 0} label={label} value={m ? undefined : "—"} />
          {detail && m && subLine(m) && <div className="mono num mt-1 text-[11px] text-text-3">{subLine(m)}</div>}
        </div>
      ))}
    </div>
  )
}

/** Embedded resource-usage strip — the first block of the host Overview
 *  panel. Renders nothing until a Mimir backend is configured: an extra on
 *  the host's front page shouldn't nag. The Metrics tab
 *  (hosts/[id]/_tabs/metrics.tsx) is where the setup states are explained. */
export function HostMetricsSection({ hostId }: { hostId: number }) {
  const { query: { data }, now } = useHostMetrics(hostId)

  if (!data || !data.configured) return null

  const stale = isStale(data, now)

  let note: React.ReactNode = null
  if (data.error) {
    note = <span className="text-[11px] text-danger" title={data.error}>query error</span>
  } else if (!hasMetrics(data)) {
    note = <span className="text-[11px] text-warn" title="Run the Install Alloy agent action and allow ~1 min for the first scrape.">no metrics yet</span>
  } else if (stale) {
    note = <span className="text-[11px] text-warn" title={data.sampled_at ? `Last sample ${relativeTime(data.sampled_at, now)}` : undefined}>stale</span>
  }

  return (
    <div className="border-b border-line pb-3">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="tt">resource usage</span>
        {note}
      </div>
      <MetricMeters data={data} dim={stale} />
    </div>
  )
}
