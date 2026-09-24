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

function relativeTime(iso: string, now: number): string {
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

/** Embedded resource-usage strip — rendered as the first block of the host
 *  Overview panel, and again on its own in the Metrics tab. Renders nothing
 *  until a Mimir backend is configured. Instant values only; auto-refreshes
 *  every 15s while visible. */
export function HostMetricsSection({ hostId }: { hostId: number }) {
  const { data } = useQuery<HostMetrics>({
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

  if (!data || !data.configured) return null

  const stale = data.sampled_at != null && now - new Date(data.sampled_at).getTime() > STALE_AFTER_MS
  const hasData = data.cpu != null || data.memory != null || data.disk != null

  let note: React.ReactNode = null
  if (data.error) {
    note = <span className="text-[11px] text-danger" title={data.error}>query error</span>
  } else if (!hasData) {
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
      <div className={`grid grid-cols-1 gap-3 sm:grid-cols-3 ${stale ? "opacity-60" : ""}`}>
        <div title={data.cpu ? subLine(data.cpu) : undefined}><Meter pct={data.cpu?.percent ?? 0} label="cpu" value={data.cpu ? undefined : "—"} /></div>
        <div title={data.memory ? subLine(data.memory) : undefined}><Meter pct={data.memory?.percent ?? 0} label="memory" value={data.memory ? undefined : "—"} /></div>
        <div title={data.disk ? subLine(data.disk) : undefined}><Meter pct={data.disk?.percent ?? 0} label="disk /" value={data.disk ? undefined : "—"} /></div>
      </div>
    </div>
  )
}
