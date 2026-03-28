"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { TableSkeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import type { HostGroup, WorkflowRunDetail } from "@/lib/types"

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

            {run.host_runs.length === 0 ? (
              <div className="text-slate-400 py-8 text-center">
                No hosts have been processed yet.
              </div>
            ) : (
              <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="border-slate-700">
                      <TableHead>Hostname</TableHead>
                      <TableHead>Current Step</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Snapshot</TableHead>
                      <TableHead>Error</TableHead>
                      <TableHead>Started</TableHead>
                      <TableHead>Completed</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {run.host_runs.map((hr) => (
                      <TableRow key={hr.id} className="border-slate-700">
                        <TableCell className="font-mono text-white text-sm">
                          <Link
                            href={`/hosts/${hr.host_id}`}
                            className="hover:text-blue-400 transition-colors"
                          >
                            {hr.hostname}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <StepBadge step={hr.step} />
                        </TableCell>
                        <TableCell>
                          <HostStatusBadge status={hr.status} />
                        </TableCell>
                        <TableCell className="font-mono text-slate-400 text-xs">
                          {hr.snapshot_name ?? "—"}
                        </TableCell>
                        <TableCell className="text-red-400 text-xs max-w-[400px]">
                          {hr.error_message ? (
                            <span title={hr.error_message}>
                              {hr.error_message}
                            </span>
                          ) : (
                            <span className="text-slate-600">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-slate-300 text-sm whitespace-nowrap">
                          {formatDateTime(hr.started_at)}
                        </TableCell>
                        <TableCell className="text-slate-300 text-sm whitespace-nowrap">
                          {formatDateTime(hr.completed_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </>
      )}

      {showLoading && <TableSkeleton rows={5} columns={7} />}
    </div>
  )
}
