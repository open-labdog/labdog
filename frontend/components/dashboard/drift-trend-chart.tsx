"use client"

import { useQuery } from "@tanstack/react-query"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import Link from "next/link"
import { History, ShieldOff } from "lucide-react"
import { apiFetch } from "@/lib/api"
import type { DriftTrendPoint, DriftTrendSeries } from "@/lib/types"
import { useDelayedLoading } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import { ChartContainer, ChartTooltip, ChartTooltipContent, CHART_CURSOR_BAR, type ChartConfig } from "@/components/ui/chart"
import { PanelShell, PanelHeader, PanelFootnote, PANEL_BODY_HEIGHT } from "@/components/dashboard/panel-shell"
import { cn } from "@/lib/utils"

export const driftTrendChartConfig = {
  drifted_checks: { label: "Drifted Checks", color: "#f59e0b" }, // amber-500
  total_drift: { label: "Total Drift", color: "#fbbf24" }, // amber-400 -- tooltip-only, see chart spec
} satisfies ChartConfig

const DAYS = 7

function formatBucketLabel(bucket: string): string {
  return new Date(bucket).toLocaleDateString([], { month: "short", day: "numeric" })
}

function formatBucketFull(bucket: string): string {
  return new Date(bucket).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

function renderLabel(_: string | number | undefined, point: DriftTrendPoint) {
  return formatBucketFull(point.bucket)
}

function renderBody(point: DriftTrendPoint) {
  return (
    <>
      <p className="text-amber-400 font-semibold text-sm">{plural(point.drifted_checks, "drifted check")}</p>
      <p className="text-slate-400">Total drift: {plural(point.total_drift, "change")}</p>
    </>
  )
}

/**
 * @param anyChecksConfigured Whether any host has any drift check switched
 *   on. `undefined` while the coverage query is in flight — the empty state
 *   stays neutral until we know, rather than accusing an install that is
 *   simply still loading.
 */
export function DriftTrendChart({ anyChecksConfigured }: { anyChecksConfigured?: boolean }) {
  const { data, isLoading, error } = useQuery<DriftTrendSeries>({
    queryKey: ["dashboard", "drift-trend", DAYS],
    queryFn: () => apiFetch<DriftTrendSeries>(`/api/dashboard/drift-trend?days=${DAYS}&granularity=day`),
    refetchInterval: 30000,
  })
  const showLoading = useDelayedLoading(isLoading)

  const points = data?.points ?? []
  const allZero = points.length > 0 && points.every((p) => p.drifted_checks === 0)

  return (
    <PanelShell>
      <PanelHeader title="Drift Trend" meta={`Last ${DAYS} days`} />
      <PanelFootnote>
        {!showLoading && !error && allZero && (
          <span className="text-green-400">No drift detected in the last {DAYS} days.</span>
        )}
      </PanelFootnote>

      {showLoading && <Skeleton className={cn(PANEL_BODY_HEIGHT, "w-full rounded-md")} />}

      {!showLoading && error && (
        <div className={cn(PANEL_BODY_HEIGHT, "flex items-center justify-center text-sm text-red-400")}>
          Failed to load drift trend data
        </div>
      )}

      {/*
        Two different empty states, which used to render as one. "No history
        yet" is a matter of waiting; "nothing is being checked" never
        resolves on its own and is the state a fresh install sits in
        permanently, because every drift flag defaults to off and the sweep
        runs on schedule finding no candidates. Showing the patient message
        for the second case is how a silent misconfiguration reads as normal.
      */}
      {!showLoading && !error && points.length === 0 && (
        <div className={cn(PANEL_BODY_HEIGHT, "flex flex-col items-center justify-center gap-2 text-center")}>
          {anyChecksConfigured === false ? (
            <>
              <ShieldOff className="h-8 w-8 text-amber-500/70" />
              <p className="text-sm text-amber-400">No drift checks are configured</p>
              <p className="max-w-[300px] text-xs text-slate-500">
                Nothing is being checked, so nothing will ever appear here. Enable
                drift checking on the{" "}
                <Link href="/hosts" className="text-slate-300 underline underline-offset-2 hover:text-white">
                  Hosts
                </Link>{" "}
                page.
              </p>
            </>
          ) : (
            <>
              <History className="h-8 w-8 text-slate-600" />
              <p className="text-sm text-slate-400">Drift history is being collected</p>
              <p className="max-w-[280px] text-xs text-slate-500">
                Trend appears after the first drift checks run across your fleet.
              </p>
            </>
          )}
        </div>
      )}

      {!showLoading && !error && points.length > 0 && (
        <div className={cn(PANEL_BODY_HEIGHT, "w-full")}>
          <ChartContainer config={driftTrendChartConfig}>
            <BarChart data={points} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis
                dataKey="bucket"
                tickLine={false}
                axisLine={{ stroke: "#334155" }}
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                tickFormatter={formatBucketLabel}
                interval="preserveStartEnd"
              />
              <YAxis
                allowDecimals={false}
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <ChartTooltip
                cursor={CHART_CURSOR_BAR}
                content={<ChartTooltipContent<DriftTrendPoint> renderLabel={renderLabel} renderBody={renderBody} />}
              />
              <Bar
                dataKey="drifted_checks"
                fill="#f59e0b"
                radius={[3, 3, 0, 0]}
                activeBar={{ fill: "#fbbf24" }}
                isAnimationActive={false}
              />
            </BarChart>
          </ChartContainer>
        </div>
      )}
    </PanelShell>
  )
}
