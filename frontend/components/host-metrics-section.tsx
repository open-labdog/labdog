"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { RefreshCwIcon, HelpCircleIcon, AlertTriangleIcon, XCircleIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { Button } from "@/components/ui/button"
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

function Tile({ label, mount, metric }: { label: string; mount?: string; metric: HostMetricValue | null }) {
  if (!metric) {
    return (
      <div className="flex flex-col gap-2 rounded-md border border-slate-800 bg-slate-950/40 p-4">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</span>
        <span className="text-3xl font-semibold tabular-nums text-slate-600">—</span>
        <div className="h-1.5 w-full rounded-full bg-slate-800" />
        <span className="text-xs text-slate-600">no data</span>
      </div>
    )
  }
  const tone = usageTone(metric.percent)
  const sub = subLine(metric)
  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-800 bg-slate-950/40 p-4">
      <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
        {mount && <span className="ml-1 font-mono normal-case text-slate-500">{mount}</span>}
      </span>
      <span className={`text-3xl font-semibold tabular-nums ${tone.text}`}>
        {Math.round(metric.percent)}
        <span className="text-lg text-slate-500">%</span>
      </span>
      <UsageBar value={metric.percent} />
      <span className="text-xs tabular-nums text-slate-500">{sub ?? " "}</span>
    </div>
  )
}

function Callout({
  tone,
  icon,
  children,
}: {
  tone: "slate" | "amber" | "red"
  icon: React.ReactNode
  children: React.ReactNode
}) {
  const cls =
    tone === "red"
      ? "border-red-700/50 bg-red-950/20 text-red-300"
      : tone === "amber"
        ? "border-amber-700/50 bg-amber-950/20 text-amber-200"
        : "border-slate-700/50 bg-slate-900/50 text-slate-300"
  return (
    <div className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${cls}`}>
      {icon}
      <div className="flex-1">{children}</div>
    </div>
  )
}

export function HostMetricsSection({ hostId }: { hostId: number }) {
  const { data, isLoading, refetch, isFetching } = useQuery<HostMetrics>({
    queryKey: ["host-metrics", hostId],
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

  const stale =
    data?.sampled_at != null &&
    now - new Date(data.sampled_at).getTime() > STALE_AFTER_MS
  const hasData = !!data && (data.cpu != null || data.memory != null || data.disk != null)

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-slate-200">Resource Usage</h3>
          {hasData && data?.sampled_at && (
            <span
              className={`text-xs ${stale ? "text-amber-400" : "text-slate-500"}`}
              title={new Date(data.sampled_at).toLocaleString()}
            >
              {stale && <AlertTriangleIcon className="mr-1 inline h-3 w-3" />}
              as of {relativeTime(data.sampled_at, now)}
            </span>
          )}
        </div>
        {data?.configured && (
          <Button variant="outline" size="sm" disabled={isFetching} onClick={() => refetch()}>
            <RefreshCwIcon className={`mr-1 h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-28 animate-pulse rounded-md border border-slate-800 bg-slate-950/40" />
          ))}
        </div>
      ) : !data || data.configured === false ? (
        <Callout tone="slate" icon={<HelpCircleIcon className="h-4 w-4 shrink-0 text-slate-400" />}>
          No metrics backend configured.{" "}
          <Link href="/grafana" className="text-sky-400 underline hover:text-sky-300">
            Set up Grafana
          </Link>{" "}
          to show live CPU, memory, and disk usage.
        </Callout>
      ) : data.error ? (
        <Callout tone="red" icon={<XCircleIcon className="h-4 w-4 shrink-0 text-red-400" />}>
          Failed to query metrics: {data.error}
        </Callout>
      ) : !hasData ? (
        <Callout tone="amber" icon={<AlertTriangleIcon className="h-4 w-4 shrink-0 text-amber-400" />}>
          No metrics found for this host yet. Run the <strong>Install Alloy agent</strong> action and
          allow a minute for the first scrape.
        </Callout>
      ) : (
        <>
          <div className={`grid grid-cols-1 gap-3 sm:grid-cols-3 ${stale ? "opacity-60" : ""}`}>
            <Tile label="CPU" metric={data.cpu} />
            <Tile label="Memory" metric={data.memory} />
            <Tile label="Disk" mount="/" metric={data.disk} />
          </div>
          {stale && (
            <p className="mt-2 text-xs text-amber-400">
              Metrics may be stale — the agent has not reported recently.
            </p>
          )}
        </>
      )}
    </div>
  )
}
