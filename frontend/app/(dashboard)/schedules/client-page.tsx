"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { CameraIcon, RotateCcwIcon, RotateCwIcon, PlayIcon, ServerIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading, cn, formatRelativeTime } from "@/lib/utils"
import { cronToHuman } from "@/lib/cron"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Tooltip } from "@/components/ui/tooltip"
import { TableSkeleton } from "@/components/ui/skeleton"
import { Button, buttonVariants } from "@/components/ui/button"
import { DataTable } from "@/components/ui/data-table"
import { showSuccess, showError } from "@/lib/toast"
import type { WorkflowSummary } from "@/lib/types"

const RUN_STATUS_STYLE: Record<string, { label: string; className: string; border: string }> = {
  completed: { label: "Completed", className: "bg-green-600 text-white", border: "border-l-green-500/60" },
  failed: { label: "Failed", className: "bg-red-600 text-white", border: "border-l-red-500/60" },
  partial: { label: "Partial", className: "bg-amber-600 text-white", border: "border-l-amber-500/60" },
  running: { label: "Running", className: "bg-blue-600 text-white animate-pulse", border: "border-l-blue-500/60" },
  pending: { label: "Pending", className: "bg-blue-600/60 text-white", border: "border-l-blue-500/60" },
}
const DEFAULT_STATUS = { label: "Unknown", className: "bg-slate-600 text-white", border: "border-l-slate-600/60" }

const OPTION_ICONS: { key: keyof Pick<WorkflowSummary, "pre_update_snapshot" | "auto_rollback" | "auto_reboot">; icon: typeof CameraIcon; label: string }[] = [
  { key: "pre_update_snapshot", icon: CameraIcon, label: "Pre-update snapshot" },
  { key: "auto_rollback", icon: RotateCcwIcon, label: "Auto-rollback" },
  { key: "auto_reboot", icon: RotateCwIcon, label: "Auto-reboot" },
]

function OptionIcons({ wf }: { wf: WorkflowSummary }) {
  return (
    <div className="flex items-center gap-1.5">
      {OPTION_ICONS.map(({ key, icon: Icon, label }) => {
        const on = wf[key]
        return (
          <Tooltip key={key} content={`${label}: ${on ? "on" : "off"}`}>
            <span className={cn("inline-flex items-center justify-center w-5 h-5 rounded", on ? "text-green-400" : "text-slate-700")}>
              <Icon className="w-3.5 h-3.5" />
            </span>
          </Tooltip>
        )
      })}
    </div>
  )
}

function rowBorder(wf: WorkflowSummary): string {
  if (!wf.enabled) return "border-l-2 border-l-slate-700/60"
  if (!wf.last_run) return "border-l-2 border-l-slate-600/60"
  return `border-l-2 ${(RUN_STATUS_STYLE[wf.last_run.status] ?? DEFAULT_STATUS).border}`
}

export default function SchedulesPage() {
  const [runningGroup, setRunningGroup] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const { data: workflows, isLoading, error } = useQuery<WorkflowSummary[]>({
    queryKey: ["workflows-summary"],
    queryFn: () => apiFetch<WorkflowSummary[]>("/api/workflows/summary"),
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.some(wf => wf.last_run?.status === "running" || wf.last_run?.status === "pending")) return 3000
      return false
    },
  })
  const showLoading = useDelayedLoading(isLoading)

  async function handleRunNow(groupId: number) {
    setRunningGroup(groupId)
    try {
      await apiFetch(`/api/groups/${groupId}/workflow/run`, { method: "POST" })
      showSuccess("Workflow run triggered")
      await queryClient.invalidateQueries({ queryKey: ["workflows-summary"] })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to trigger run"
      showError(msg)
    } finally {
      setRunningGroup(null)
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Update Workflows" }]} />

      <div>
        <h1 className="text-2xl font-bold text-white">Update Workflows</h1>
        <p className="text-slate-400 text-sm mt-1">
          Automated update workflows configured across your groups.
        </p>
      </div>

      {showLoading && <TableSkeleton rows={4} columns={6} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load workflows</div>
      )}

      {!isLoading && !error && (
        <DataTable<WorkflowSummary>
          tableId="workflows-v2"
          data={workflows}
          emptyMessage={
            <>
              No update workflows configured yet.{" "}
              <Link href="/groups" className="underline hover:text-white">
                Open a group
              </Link>{" "}
              and configure one from its <strong>Workflow</strong> tab.
            </>
          }
          getRowKey={(wf) => wf.id}
          rowClassName={(wf) => rowBorder(wf)}
          columns={[
            {
              key: "group",
              label: "Group",
              accessor: (wf) => wf.group_name,
              cell: (wf) => (
                <div>
                  <Link href={`/groups/${wf.group_id}`} className="text-sm text-white font-medium hover:text-blue-400 transition-colors">
                    {wf.group_name}
                  </Link>
                  {wf.group_category && (
                    <div className="text-xs text-slate-500">{wf.group_category}</div>
                  )}
                </div>
              ),
              defaultWidth: 180,
              filter: { type: "text" },
            },
            {
              key: "status",
              label: "Status",
              accessor: (wf) => wf.enabled ? "enabled" : "disabled",
              cell: (wf) => (
                <Badge className={wf.enabled ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
                  {wf.enabled ? "Enabled" : "Disabled"}
                </Badge>
              ),
              defaultWidth: 100,
            },
            {
              key: "schedule",
              label: "Schedule",
              accessor: (wf) => wf.schedule_cron ?? "",
              cell: (wf) => wf.schedule_cron ? (
                <div>
                  <span className="font-mono text-sm text-white">{wf.schedule_cron}</span>
                  {cronToHuman(wf.schedule_cron) !== wf.schedule_cron && (
                    <div className="text-xs text-slate-500 mt-0.5">{cronToHuman(wf.schedule_cron)}</div>
                  )}
                </div>
              ) : (
                <span className="text-sm text-slate-500">Manual only</span>
              ),
              defaultWidth: 200,
            },
            {
              key: "hosts",
              label: "Hosts",
              accessor: (wf) => wf.host_count,
              cell: (wf) => (
                <div className="flex items-center gap-1.5">
                  <ServerIcon className="w-3.5 h-3.5 text-slate-500" />
                  <span className="text-sm tabular-nums text-slate-300">{wf.host_count}</span>
                </div>
              ),
              defaultWidth: 80,
            },
            {
              key: "batch",
              label: "Batch",
              accessor: (wf) => wf.batch_size,
              cell: (wf) => <span className="text-sm tabular-nums text-slate-300">{wf.batch_size}</span>,
              defaultWidth: 70,
            },
            {
              key: "options",
              label: "Options",
              cell: (wf) => <OptionIcons wf={wf} />,
              defaultWidth: 100,
              sortable: false,
            },
            {
              key: "last_run",
              label: "Last Run",
              accessor: (wf) => wf.last_run?.created_at ?? "",
              cell: (wf) => {
                if (!wf.last_run) return <span className="text-xs text-slate-600">Never run</span>
                const style = RUN_STATUS_STYLE[wf.last_run.status] ?? DEFAULT_STATUS
                const isActive = wf.last_run.status === "running" || wf.last_run.status === "pending"
                return (
                  <div className="flex items-center gap-2">
                    <Badge className={cn("text-xs", style.className)}>{style.label}</Badge>
                    <span className="text-xs text-slate-400">
                      {isActive ? "started " : ""}{formatRelativeTime(wf.last_run.started_at ?? wf.last_run.created_at)}
                    </span>
                  </div>
                )
              },
              defaultWidth: 200,
            },
            {
              key: "actions",
              label: "Actions",
              cell: (wf) => {
                const isActive = wf.last_run?.status === "running" || wf.last_run?.status === "pending"
                return (
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!wf.enabled || isActive || runningGroup === wf.group_id}
                      onClick={() => handleRunNow(wf.group_id)}
                      title={!wf.enabled ? "Enable workflow first" : isActive ? "Run already active" : "Trigger run now"}
                    >
                      <PlayIcon className="w-3.5 h-3.5 mr-1" />
                      {runningGroup === wf.group_id ? "..." : "Run"}
                    </Button>
                    <Link href={`/groups/${wf.group_id}?tab=workflow`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
                      Configure
                    </Link>
                  </div>
                )
              },
              defaultWidth: 180,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}
    </div>
  )
}
