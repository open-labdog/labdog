"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { CalendarClock } from "lucide-react"
import { apiFetch } from "@/lib/api"
import type { ScheduledAction } from "@/lib/types"
import { RunStatusBadge } from "@/components/status-badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn, formatRelativeTime, useDelayedLoading } from "@/lib/utils"
import { PanelShell, PanelHeader, PanelFootnote, PANEL_BODY_HEIGHT } from "@/components/dashboard/panel-shell"

function EmptyNoSchedules() {
  return (
    <div className={cn(PANEL_BODY_HEIGHT, "flex flex-col items-center justify-center gap-2 text-center")}>
      <CalendarClock className="h-8 w-8 text-slate-600" />
      <p className="text-sm text-slate-400">No schedules configured</p>
      <p className="max-w-[240px] text-xs text-slate-500">
        Set up cron-driven runs from the Schedules page.
      </p>
    </div>
  )
}

function EmptyNoRuns() {
  return (
    <div className={cn(PANEL_BODY_HEIGHT, "flex flex-col items-center justify-center gap-2 text-center")}>
      <CalendarClock className="h-8 w-8 text-slate-600" />
      <p className="text-sm text-slate-400">No runs yet</p>
      <p className="max-w-[240px] text-xs text-slate-500">
        Scheduled runs will appear here once they fire.
      </p>
    </div>
  )
}

function RunsSkeleton() {
  return (
    <div className="space-y-3">
      {[85, 70, 90, 60, 75, 80].map((w, i) => (
        <div key={i} className="flex items-center gap-2.5 py-1">
          <div className="flex-1 space-y-1">
            <Skeleton className="h-3" style={{ width: `${w}%` }} />
            <Skeleton className="h-2 w-24" />
          </div>
          <Skeleton className="h-5 w-16 rounded-full" />
        </div>
      ))}
    </div>
  )
}

export function RecentScheduledRunsPanel() {
  const { data, isLoading, error } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions", "recent"],
    queryFn: () => apiFetch<ScheduledAction[]>("/api/scheduled-actions?include_last_run=true"),
    refetchInterval: 30000,
  })
  const showLoading = useDelayedLoading(isLoading)

  const all = data ?? []
  const withRuns = all
    .filter((row) => row.last_run !== null)
    .sort((a, b) => {
      const aTime = a.last_run!.started_at ?? a.last_run!.created_at
      const bTime = b.last_run!.started_at ?? b.last_run!.created_at
      return bTime.localeCompare(aTime)
    })
    .slice(0, 10)

  return (
    <PanelShell>
      <PanelHeader
        title="Recent Scheduled Runs"
        action={
          <Link href="/schedules" className="text-xs text-blue-400 hover:underline">
            View all →
          </Link>
        }
      />
      <PanelFootnote />

      {showLoading && <div className={PANEL_BODY_HEIGHT}><RunsSkeleton /></div>}

      {!showLoading && error && (
        <div className={cn(PANEL_BODY_HEIGHT, "flex items-center justify-center text-sm text-red-400")}>
          Failed to load scheduled runs
        </div>
      )}

      {!showLoading && !error && all.length === 0 && <EmptyNoSchedules />}

      {!showLoading && !error && all.length > 0 && withRuns.length === 0 && <EmptyNoRuns />}

      {!showLoading && !error && withRuns.length > 0 && (
        <div className={cn(PANEL_BODY_HEIGHT, "overflow-y-auto")}>
          {withRuns.map((row) => (
            <Link
              key={row.id}
              href={`/actions/runs/${row.last_run!.id}`}
              className="flex items-center gap-2.5 py-2 border-b border-slate-800 last:border-b-0 -mx-1 px-1 rounded transition-colors hover:bg-slate-800/40"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs text-slate-300">
                  <span className="font-medium text-white">{row.action_name ?? row.action_key}</span>{" "}
                  <span className="text-slate-400">on</span>{" "}
                  <span className="font-medium text-slate-200">{row.target_name ?? "—"}</span>
                </p>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <span className="text-[11px] text-slate-500">
                    {formatRelativeTime(row.last_run!.started_at ?? row.last_run!.created_at)}
                  </span>
                  <span className="text-[11px] text-slate-600">·</span>
                  <span className="font-mono text-[11px] text-slate-500">{row.action_key}</span>
                </div>
              </div>
              <RunStatusBadge status={row.last_run!.status} />
            </Link>
          ))}
        </div>
      )}
    </PanelShell>
  )
}
