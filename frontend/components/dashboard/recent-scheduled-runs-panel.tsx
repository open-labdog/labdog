"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { CalendarClock, ChevronDown } from "lucide-react"
import { apiFetch } from "@/lib/api"
import type { ScheduledActionRun } from "@/lib/types"
import { RunStatusBadge, RUN_STATUS_COLORS } from "@/components/status-badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn, formatRelativeTime, useDelayedLoading } from "@/lib/utils"
import { PanelShell, PanelHeader, PanelFootnote, PANEL_BODY_HEIGHT } from "@/components/dashboard/panel-shell"

type ViewMode = "per_run" | "grouped"

interface RunGroup {
  scheduledActionId: number
  displayAction: string
  displayTarget: string
  /** Newest-first, same order as the source feed. */
  runs: ScheduledActionRun[]
}

function displayName(run: ScheduledActionRun): { action: string; target: string } {
  return {
    action: run.action_name ?? run.action_key,
    target: run.target_name ?? run.target_kind,
  }
}

/** finished_at − started_at, compact ("3m 41s", "8s"); "running"/"queued"
 *  for runs that haven't finished yet. */
function formatDuration(run: ScheduledActionRun): string {
  if (run.started_at && run.finished_at) {
    const totalSeconds = Math.max(0, Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000))
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`
  }
  if (run.status === "running") return "running"
  if (run.status === "queued" || run.status === "pending") return "queued"
  return "—"
}

/** Groups a newest-first run feed by schedule, capped at 10 groups sorted
 *  by each group's latest run. Relies on the source order being
 *  newest-first so each group's `runs` array stays newest-first too. */
function buildGroups(runs: ScheduledActionRun[]): RunGroup[] {
  const byId = new Map<number, ScheduledActionRun[]>()
  for (const run of runs) {
    const existing = byId.get(run.scheduled_action_id)
    if (existing) {
      existing.push(run)
    } else {
      byId.set(run.scheduled_action_id, [run])
    }
  }

  const groups: RunGroup[] = Array.from(byId.values()).map((groupRuns) => {
    const { action, target } = displayName(groupRuns[0])
    return {
      scheduledActionId: groupRuns[0].scheduled_action_id,
      displayAction: action,
      displayTarget: target,
      runs: groupRuns,
    }
  })

  groups.sort((a, b) => {
    const aTime = a.runs[0].started_at ?? a.runs[0].created_at
    const bTime = b.runs[0].started_at ?? b.runs[0].created_at
    return bTime.localeCompare(aTime)
  })

  return groups.slice(0, 10)
}

function EmptyRuns() {
  return (
    <div className={cn(PANEL_BODY_HEIGHT, "flex flex-col items-center justify-center gap-2 text-center")}>
      <CalendarClock className="h-8 w-8 text-slate-600" />
      <p className="text-sm text-slate-400">No scheduled runs yet</p>
      <p className="max-w-[240px] text-xs text-slate-500">
        Runs will appear here once a schedule fires.
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

/** Single-run row shell, shared by the flat per-run list and the expanded
 *  sub-rows under a group. Links to the run detail page. */
function RunRow({ run, nested = false }: { run: ScheduledActionRun; nested?: boolean }) {
  const { action, target } = displayName(run)
  return (
    <Link
      href={`/actions/runs/${run.id}`}
      className={cn(
        "flex min-w-0 items-center gap-2.5 border-b border-slate-800 py-2 last:border-b-0 -mx-1 px-1 rounded transition-colors hover:bg-slate-800/40",
        nested && "py-1.5 pl-4"
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs text-slate-300">
          <span className="font-medium text-white">{action}</span>{" "}
          <span className="text-slate-400">on</span>{" "}
          <span className="font-medium text-slate-200">{target}</span>
        </p>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="text-[11px] text-slate-500">
            {formatRelativeTime(run.started_at ?? run.created_at)}
          </span>
          <span className="text-[11px] text-slate-600">·</span>
          <span className="text-[11px] text-slate-500">{formatDuration(run)}</span>
        </div>
      </div>
      <RunStatusBadge status={run.status} />
    </Link>
  )
}

/** Collapsed group row: schedule name, dot-strip run-history, "N runs ·
 *  latest Xm ago", the latest run's badge, and a chevron. Clicking the row
 *  toggles expansion to per-run sub-rows (reusing `RunRow`). */
function GroupRow({ group, isOpen, onToggle }: { group: RunGroup; isOpen: boolean; onToggle: () => void }) {
  const latest = group.runs[0]
  // Up to 5 most-recent runs, oldest→newest left to right for the strip.
  const dots = group.runs.slice(0, 5).slice().reverse()

  return (
    <div className="border-b border-slate-800 last:border-b-0">
      <button
        type="button"
        aria-expanded={isOpen}
        onClick={onToggle}
        className="flex w-full min-w-0 items-center gap-2.5 py-2 -mx-1 px-1 rounded text-left transition-colors hover:bg-slate-800/40"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs text-slate-300">
            <span className="font-medium text-white">{group.displayAction}</span>{" "}
            <span className="text-slate-400">on</span>{" "}
            <span className="font-medium text-slate-200">{group.displayTarget}</span>
          </p>
          <div className="mt-0.5 flex items-center gap-1.5">
            <div className="flex shrink-0 items-center gap-1">
              {dots.map((run) => (
                <span
                  key={run.id}
                  className={cn("h-1.5 w-1.5 rounded-full", RUN_STATUS_COLORS[run.status] ?? "bg-slate-600")}
                />
              ))}
            </div>
            <span className="text-[11px] text-slate-600">·</span>
            <span className="truncate text-[11px] text-slate-500">
              {group.runs.length} run{group.runs.length === 1 ? "" : "s"} · latest{" "}
              {formatRelativeTime(latest.started_at ?? latest.created_at)}
            </span>
          </div>
        </div>
        <RunStatusBadge status={latest.status} />
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 text-slate-500 transition-transform", isOpen && "rotate-180")}
        />
      </button>
      {isOpen && (
        <div className="pb-1">
          {group.runs.map((run) => (
            <RunRow key={run.id} run={run} nested />
          ))}
        </div>
      )}
    </div>
  )
}

export function RecentScheduledRunsPanel() {
  const [mode, setMode] = useState<ViewMode>("per_run")
  const grouped = mode === "grouped"
  const [openGroups, setOpenGroups] = useState<Set<number>>(new Set())

  const perRunQuery = useQuery<ScheduledActionRun[]>({
    queryKey: ["scheduled-action-runs", "per-run"],
    queryFn: () => apiFetch<ScheduledActionRun[]>("/api/scheduled-actions/runs?limit=10"),
    refetchInterval: 30000,
    enabled: mode === "per_run",
  })

  const groupedQuery = useQuery<ScheduledActionRun[]>({
    queryKey: ["scheduled-action-runs", "grouped"],
    queryFn: () => apiFetch<ScheduledActionRun[]>("/api/scheduled-actions/runs?limit=40"),
    refetchInterval: 30000,
    enabled: mode === "grouped",
  })

  const { data, isLoading, error } = mode === "per_run" ? perRunQuery : groupedQuery
  const showLoading = useDelayedLoading(isLoading)

  const runs = data ?? []
  const groups = mode === "grouped" ? buildGroups(runs) : []

  function toggleGroup(id: number) {
    setOpenGroups((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <PanelShell>
      <PanelHeader
        title="Recent Scheduled Runs"
        action={
          <div className="flex items-center gap-3">
            <button
              type="button"
              aria-pressed={grouped}
              onClick={() => setMode(grouped ? "per_run" : "grouped")}
              title="Group runs by schedule"
              className={cn(
                "h-7 rounded-md border px-2.5 text-xs font-medium transition-colors",
                grouped
                  ? "border-slate-600 bg-slate-600 text-white"
                  : "border-slate-700 bg-slate-800 text-slate-400 hover:text-slate-200"
              )}
            >
              Grouped
            </button>
            <Link href="/schedules" className="text-xs text-blue-400 hover:underline">
              View all →
            </Link>
          </div>
        }
      />
      <PanelFootnote />

      {showLoading && <div className={PANEL_BODY_HEIGHT}><RunsSkeleton /></div>}

      {!showLoading && error && (
        <div className={cn(PANEL_BODY_HEIGHT, "flex items-center justify-center text-sm text-red-400")}>
          Failed to load scheduled runs
        </div>
      )}

      {!showLoading && !error && runs.length === 0 && <EmptyRuns />}

      {!showLoading && !error && runs.length > 0 && (
        <div className={cn(PANEL_BODY_HEIGHT, "overflow-y-auto overflow-x-hidden")}>
          {mode === "per_run"
            ? runs.map((run) => <RunRow key={run.id} run={run} />)
            : groups.map((group) => (
                <GroupRow
                  key={group.scheduledActionId}
                  group={group}
                  isOpen={openGroups.has(group.scheduledActionId)}
                  onToggle={() => toggleGroup(group.scheduledActionId)}
                />
              ))}
        </div>
      )}
    </PanelShell>
  )
}
