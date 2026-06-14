"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { AlertTriangleIcon, XCircleIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { UsageBar, usageTone } from "@/components/usage-bar"
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

function subLine(m: HostMetricValue): string | null {
  if (m.unit === "cores" && m.used != null && m.total != null) {
    return `${m.used.toFixed(1)} / ${m.total} cores`
  }
  if (m.unit === "bytes" && m.used != null && m.total != null) {
    return `${formatBytes(m.used)} / ${formatBytes(m.total)}`
  }
  return null
}

/** One compact metric cell: label + % on a baseline, slim bar below.
 *  Absolute used/total lives in the title tooltip to keep height down. */
function Cell({ label, mount, metric }: { label: string; mount?: string; metric: HostMetricValue | null }) {
  const labelEl = (
    <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
      {label}
      {mount && <span className="ml-1 font-mono normal-case text-slate-500">{mount}</span>}
    </span>
  )
  if (!metric) {
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          {labelEl}
          <span className="text-xl font-semibold leading-none tabular-nums text-slate-600">—</span>
        </div>
        <div className="h-1.5 w-full rounded-full bg-slate-800" />
      </div>
    )
  }
  const tone = usageTone(metric.percent)
  const sub = subLine(metric)
  return (
    <div className="flex flex-col gap-1" title={sub ?? undefined}>
      <div className="flex items-baseline justify-between gap-2">
        {labelEl}
        <span className={`text-xl font-semibold leading-none tabular-nums ${tone.text}`}>
          {Math.round(metric.percent)}
          <span className="text-xs text-slate-500">%</span>
        </span>
      </div>
      <UsageBar value={metric.percent} />
    </div>
  )
}

/** Embedded resource-usage strip — rendered as the first block of the host
 *  Overview info card (not its own card). Renders nothing until a Mimir
 *  backend is configured. Instant values only; auto-refreshes every 15s
 *  while visible (the page's Refresh also refetches it). */
export function HostMetricsSection({ hostId }: { hostId: number }) {
  const { data } = useQuery<HostMetrics>({
    queryKey: ["host-metrics", String(hostId)],
    queryFn: () => apiFetch<HostMetrics>(`/api/grafana/hosts/${hostId}/metrics`),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })

  // Tick "now" on an interval (not Date.now() in render — that's impure).
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])

  // Hidden entirely until a Mimir backend is configured (incl. first fetch).
  if (!data || !data.configured) return null

  const stale =
    data.sampled_at != null && now - new Date(data.sampled_at).getTime() > STALE_AFTER_MS
  const hasData = data.cpu != null || data.memory != null || data.disk != null

  // Right-aligned one-line status note (icon + short text + tooltip) — keeps
  // abnormal states from growing the strip into a callout block.
  let note: React.ReactNode = null
  if (data.error) {
    note = (
      <span className="flex items-center gap-1 text-xs text-red-400" title={data.error}>
        <XCircleIcon className="h-3.5 w-3.5" /> query error
      </span>
    )
  } else if (!hasData) {
    note = (
      <span
        className="flex items-center gap-1 text-xs text-amber-400"
        title="Run the Install Alloy agent action and allow ~1 min for the first scrape."
      >
        <AlertTriangleIcon className="h-3.5 w-3.5" /> no metrics yet
      </span>
    )
  } else if (stale) {
    note = (
      <span
        className="flex items-center gap-1 text-xs text-amber-400"
        title={data.sampled_at ? `Last sample ${relativeTime(data.sampled_at, now)}` : undefined}
      >
        <AlertTriangleIcon className="h-3.5 w-3.5" /> stale
      </span>
    )
  }

  return (
    <div className="mb-3 border-b border-slate-800 pb-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-slate-400">Resource Usage</span>
        {note}
      </div>
      <div className={`grid grid-cols-3 gap-4 ${stale ? "opacity-60" : ""}`}>
        <Cell label="CPU" metric={data.cpu} />
        <Cell label="Memory" metric={data.memory} />
        <Cell label="Disk" mount="/" metric={data.disk} />
      </div>
    </div>
  )
}
