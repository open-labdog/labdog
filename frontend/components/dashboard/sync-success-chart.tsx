"use client"

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { History } from "lucide-react"
import { apiFetch } from "@/lib/api"
import type { Granularity, SyncRateSeries } from "@/lib/types"
import { useDelayedLoading } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import { ChartContainer, ChartTooltip, ChartTooltipContent, CHART_CURSOR_BAR, CHART_CURSOR_LINE, type ChartConfig } from "@/components/ui/chart"
import { PanelShell, PanelHeader, PanelFootnote, PANEL_BODY_HEIGHT } from "@/components/dashboard/panel-shell"
import { cn } from "@/lib/utils"

export const syncRateChartConfig = {
  rate: { label: "Success Rate", color: "#22c55e" }, // green-500
} satisfies ChartConfig

const DAYS = 7

interface ChartPoint {
  bucket: string
  /** Percentage 0-100, or null when `total === 0` for that bucket (no data, not "0% success"). */
  rate: number | null
  success: number
  total: number
}

function formatBucketLabel(bucket: string, granularity: Granularity): string {
  const d = new Date(bucket)
  if (granularity === "hour") {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" })
}

function formatBucketFull(bucket: string, granularity: Granularity): string {
  const d = new Date(bucket)
  if (granularity === "hour") {
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
  }
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
}

function EmptyChartState() {
  return (
    <div className={cn(PANEL_BODY_HEIGHT, "flex flex-col items-center justify-center gap-2 text-center")}>
      <History className="h-8 w-8 text-slate-600" />
      <p className="text-sm text-slate-400">No sync activity recorded yet</p>
      <p className="max-w-[280px] text-xs text-slate-500">
        Success rate appears once hosts start syncing.
      </p>
    </div>
  )
}

export function SyncSuccessChart() {
  const { data, isLoading, error } = useQuery<SyncRateSeries>({
    queryKey: ["dashboard", "sync-rate", DAYS],
    queryFn: () => apiFetch<SyncRateSeries>(`/api/dashboard/sync-success-rate?days=${DAYS}&granularity=day`),
    refetchInterval: 30000,
  })
  const showLoading = useDelayedLoading(isLoading)
  const granularity = data?.granularity ?? "day"

  const points: ChartPoint[] = useMemo(
    () =>
      (data?.points ?? []).map((p) => ({
        bucket: p.bucket,
        rate: p.total === 0 || p.success_rate === null ? null : p.success_rate * 100,
        success: p.success,
        total: p.total,
      })),
    [data]
  )

  const renderLabel = (_: string | number | undefined, point: ChartPoint) =>
    formatBucketFull(point.bucket, granularity)

  const renderBody = (point: ChartPoint) => (
    <>
      <p className="text-slate-400">
        {point.success} / {point.total} hosts synced
      </p>
      <p className="text-green-400 font-semibold text-sm">
        {point.rate === null ? "No syncs attempted" : `${Math.round(point.rate)}% success rate`}
      </p>
    </>
  )

  return (
    <PanelShell>
      <PanelHeader title="Sync Success Rate" meta={`Last ${DAYS} days`} />
      <PanelFootnote>
        {!showLoading && !error && points.length === 1 && (
          <span className="text-slate-500">
            Only one data point so far — the trend line appears with more history.
          </span>
        )}
      </PanelFootnote>

      {showLoading && <Skeleton className={cn(PANEL_BODY_HEIGHT, "w-full rounded-md")} />}

      {!showLoading && error && (
        <div className={cn(PANEL_BODY_HEIGHT, "flex items-center justify-center text-sm text-red-400")}>
          Failed to load sync rate data
        </div>
      )}

      {!showLoading && !error && points.length === 0 && <EmptyChartState />}

      {!showLoading && !error && points.length === 1 && (
        <div className={cn(PANEL_BODY_HEIGHT, "w-full")}>
          <ChartContainer config={syncRateChartConfig}>
            <BarChart data={points} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis
                dataKey="bucket"
                tickLine={false}
                axisLine={{ stroke: "#334155" }}
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                tickFormatter={(v: string) => formatBucketLabel(v, granularity)}
              />
              <YAxis
                domain={[0, 100]}
                ticks={[0, 50, 100]}
                tickFormatter={(v: number) => `${v}%`}
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <ChartTooltip
                cursor={CHART_CURSOR_BAR}
                content={<ChartTooltipContent<ChartPoint> renderLabel={renderLabel} renderBody={renderBody} />}
              />
              <Bar dataKey="rate" fill="#22c55e" radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartContainer>
        </div>
      )}

      {!showLoading && !error && points.length > 1 && (
        <div className={cn(PANEL_BODY_HEIGHT, "w-full")}>
          <ChartContainer config={syncRateChartConfig}>
            <AreaChart data={points} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
              <defs>
                <linearGradient id="syncRateFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22c55e" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#22c55e" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis
                dataKey="bucket"
                tickLine={false}
                axisLine={{ stroke: "#334155" }}
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                tickFormatter={(v: string) => formatBucketLabel(v, granularity)}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={[0, 100]}
                ticks={[0, 50, 100]}
                tickFormatter={(v: number) => `${v}%`}
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <ChartTooltip
                cursor={CHART_CURSOR_LINE}
                content={<ChartTooltipContent<ChartPoint> renderLabel={renderLabel} renderBody={renderBody} />}
              />
              <Area
                type="monotone"
                dataKey="rate"
                stroke="#22c55e"
                strokeWidth={2}
                fill="url(#syncRateFill)"
                dot={false}
                activeDot={{ r: 4, fill: "#4ade80", stroke: "#0f172a", strokeWidth: 2 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ChartContainer>
        </div>
      )}
    </PanelShell>
  )
}
