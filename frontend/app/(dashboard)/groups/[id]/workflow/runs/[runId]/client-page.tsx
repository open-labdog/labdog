"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import type { HostGroup, WorkflowRunDetail, WorkflowHostRun } from "@/lib/types"

function RunStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-slate-600 text-white",
    running: "bg-blue-600 text-white",
    completed: "bg-green-600 text-white",
    failed: "bg-red-600 text-white",
    partial: "bg-amber-600 text-white",
  }
  return (
    <Badge className={colors[status] ?? "bg-slate-600 text-white"}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  )
}

function HostStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-slate-600 text-white",
    running: "bg-blue-600 text-white",
    success: "bg-green-600 text-white",
    failed: "bg-red-600 text-white",
    skipped: "bg-slate-600 text-white",
  }
  return (
    <Badge className={colors[status] ?? "bg-slate-600 text-white"}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  )
}

function StepBadge({ step }: { step: string }) {
  const colors: Record<string, string> = {
    preflight: "bg-slate-700 text-slate-200",
    snapshot: "bg-indigo-700 text-white",
    update: "bg-blue-700 text-white",
    reboot: "bg-violet-700 text-white",
    verify: "bg-teal-700 text-white",
    cleanup: "bg-slate-600 text-white",
    rollback: "bg-red-700 text-white",
  }
  return (
    <Badge className={colors[step] ?? "bg-slate-600 text-white"}>
      {step.charAt(0).toUpperCase() + step.slice(1)}
    </Badge>
  )
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString()
}

export default function WorkflowRunDetailPage() {
  const params = useParams()
  const groupId = Number(params.id)
  const runId = Number(params.runId)

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", groupId],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${groupId}`),
    enabled: !!groupId,
  })

  const {
    data: run,
    isLoading,
    error,
  } = useQuery<WorkflowRunDetail>({
    queryKey: ["workflow-run", runId],
    queryFn: () => apiFetch<WorkflowRunDetail>(`/api/workflow-runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) => {
      const data = query.state.data as WorkflowRunDetail | undefined
      if (!data) return false
      const isActive = data.status === "pending" || data.status === "running"
      return isActive ? 3000 : false
    },
  })

  const showLoading = useDelayedLoading(isLoading)

  return (
    <div className="space-y-8">
      <Breadcrumb
        items={[
          { label: "Groups", href: "/groups" },
          { label: group?.name ?? "Group", href: `/groups/${groupId}` },
          { label: "Workflow", href: `/groups/${groupId}/workflow` },
          { label: `Run #${runId}` },
        ]}
      />

      <div>
        <h1 className="text-2xl font-bold text-white">Run #{runId}</h1>
        <p className="text-slate-400 text-sm mt-1">Workflow run detail</p>
      </div>

      {showLoading && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-3">
          <div className="h-4 bg-slate-800 animate-pulse rounded w-1/4" />
          <div className="h-4 bg-slate-800 animate-pulse rounded w-1/3" />
          <div className="h-4 bg-slate-800 animate-pulse rounded w-1/2" />
        </div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load run details</div>
      )}

      {!isLoading && !error && run && (
        <>
          {/* Summary card */}
          <div className="rounded-lg border border-slate-700 bg-slate-900 p-5 grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-slate-500 mb-1">Status</div>
              <RunStatusBadge status={run.status} />
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">Started</div>
              <div className="text-sm text-slate-300">{formatDateTime(run.started_at)}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">Completed</div>
              <div className="text-sm text-slate-300">{formatDateTime(run.completed_at)}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">Triggered By</div>
              <div className="text-sm text-slate-300">
                {run.triggered_by ? `User ${run.triggered_by}` : "Scheduled"}
              </div>
            </div>
          </div>

          {/* Per-host table */}
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-white">Host Progress</h2>

            <DataTable<WorkflowHostRun>
              tableId="workflow-run-hosts"
              data={run.host_runs}
              emptyMessage="No hosts have been processed yet."
              getRowKey={(hr) => hr.id}
              columns={[
                {
                  key: "hostname",
                  label: "Hostname",
                  accessor: (hr) => hr.hostname,
                  cell: (hr) => (
                    <Link
                      href={`/hosts/${hr.host_id}`}
                      className="font-mono text-white text-sm hover:text-blue-400 transition-colors"
                    >
                      {hr.hostname}
                    </Link>
                  ),
                  defaultWidth: 180,
                  filter: { type: "text", placeholder: "e.g. web-01" },
                },
                {
                  key: "step",
                  label: "Current Step",
                  accessor: (hr) => hr.step,
                  cell: (hr) => <StepBadge step={hr.step} />,
                  defaultWidth: 130,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "status",
                  label: "Status",
                  accessor: (hr) => hr.status,
                  cell: (hr) => <HostStatusBadge status={hr.status} />,
                  defaultWidth: 120,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "snapshot_name",
                  label: "Snapshot",
                  accessor: (hr) => hr.snapshot_name ?? "",
                  cell: (hr) => (
                    <span className="font-mono text-slate-400 text-xs">{hr.snapshot_name ?? "—"}</span>
                  ),
                  defaultWidth: 180,
                },
                {
                  key: "error_message",
                  label: "Error",
                  accessor: (hr) => hr.error_message ?? "",
                  cell: (hr) => hr.error_message ? (
                    <span className="text-red-400 text-xs" title={hr.error_message}>{hr.error_message}</span>
                  ) : (
                    <span className="text-slate-600">—</span>
                  ),
                  defaultWidth: 300,
                },
                {
                  key: "started_at",
                  label: "Started",
                  accessor: (hr) => hr.started_at,
                  cell: (hr) => (
                    <span className="text-slate-300 text-sm whitespace-nowrap">{formatDateTime(hr.started_at)}</span>
                  ),
                  defaultWidth: 180,
                  filter: { type: "dateRange" },
                },
                {
                  key: "completed_at",
                  label: "Completed",
                  accessor: (hr) => hr.completed_at,
                  cell: (hr) => (
                    <span className="text-slate-300 text-sm whitespace-nowrap">{formatDateTime(hr.completed_at)}</span>
                  ),
                  defaultWidth: 180,
                  filter: { type: "dateRange" },
                },
              ]}
            />
          </div>
        </>
      )}

      {showLoading && <TableSkeleton rows={5} columns={7} />}
    </div>
  )
}
