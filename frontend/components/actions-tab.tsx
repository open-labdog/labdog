"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ActionCard } from "@/components/action-card"
import { ActionRunDialog } from "@/components/action-run-dialog"
import { RunStatusBadge } from "@/components/status-badge"
import { DataTable } from "@/components/ui/data-table"
import { TableSkeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/lib/api"
import type { ActionDefinition, ActionRun } from "@/lib/types"
import type { ColumnDef } from "@tanstack/react-table"
import Link from "next/link"

interface ActionsTabProps {
  scope: "host" | "group"
  targetId: number
}

function formatDuration(run: ActionRun): string {
  if (!run.started_at || !run.finished_at) return "—"
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export function ActionsTab({ scope, targetId }: ActionsTabProps) {
  const [selectedAction, setSelectedAction] = useState<ActionDefinition | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const { data: catalog, isLoading: catalogLoading } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 60_000,
  })

  const scopeParam = scope === "host" ? `host_id=${targetId}` : `group_id=${targetId}`
  const { data: runs, isLoading: runsLoading } = useQuery<ActionRun[]>({
    queryKey: ["action-runs", scope, targetId],
    queryFn: () => apiFetch<ActionRun[]>(`/api/actions/runs?${scopeParam}&limit=20`),
    refetchInterval: (query) => {
      const data = query.state.data as ActionRun[] | undefined
      if (!data) return false
      const hasActive = data.some((r) => r.status === "queued" || r.status === "running")
      return hasActive ? 3000 : false
    },
  })

  const filteredCatalog = (catalog ?? []).filter((a) =>
    scope === "host" ? a.supports_host : a.supports_group
  )

  const runColumns: ColumnDef<ActionRun>[] = [
    {
      accessorKey: "action_key",
      header: "Action",
      cell: (r) => {
        const def = (catalog ?? []).find((a) => a.key === r.row.original.action_key)
        return def?.name ?? r.row.original.action_key
      },
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: (r) => <RunStatusBadge status={r.row.original.status} />,
    },
    {
      id: "duration",
      header: "Duration",
      cell: (r) => formatDuration(r.row.original),
    },
    {
      accessorKey: "created_at",
      header: "Started",
      cell: (r) => new Date(r.row.original.created_at).toLocaleString(),
    },
    {
      id: "logs",
      header: "",
      cell: (r) => {
        const base = scope === "host" ? `/hosts/${targetId}` : `/groups/${targetId}`
        return (
          <Link
            href={`${base}/actions/runs/${r.row.original.id}`}
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            Logs →
          </Link>
        )
      },
    },
  ]

  return (
    <div className="space-y-6">
      {/* T8 — Disambiguation note */}
      {scope === "group" && (
        <p className="text-xs text-slate-500">
          Looking for scheduled, snapshot-backed upgrades on Proxmox VMs?{" "}
          <Link href="/schedules" className="text-blue-400 hover:text-blue-300">
            See Update Workflows
          </Link>
          .
        </p>
      )}

      {/* Catalog + recent runs two-column layout */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-5">
        {/* Catalog — 60% */}
        <div className="md:col-span-3 space-y-3">
          <h3 className="text-sm font-semibold text-slate-200">Available Actions</h3>
          {catalogLoading ? (
            <TableSkeleton rows={2} />
          ) : filteredCatalog.length === 0 ? (
            <p className="text-sm text-slate-500">No actions available.</p>
          ) : (
            filteredCatalog.map((action) => {
              const lastRun = (runs ?? []).find((r) => r.action_key === action.key)
              return (
                <ActionCard
                  key={action.key}
                  action={action}
                  onRun={(a) => { setSelectedAction(a); setDialogOpen(true) }}
                  lastRun={lastRun ? { status: lastRun.status, started_at: lastRun.created_at } : null}
                />
              )
            })
          )}
        </div>

        {/* Recent runs — 40% */}
        <div className="md:col-span-2 space-y-3">
          <h3 className="text-sm font-semibold text-slate-200">Recent Runs</h3>
          {runsLoading ? (
            <TableSkeleton rows={3} />
          ) : !runs || runs.length === 0 ? (
            <p className="text-sm text-slate-500">No runs yet.</p>
          ) : (
            <DataTable columns={runColumns} data={runs} />
          )}
        </div>
      </div>

      <ActionRunDialog
        action={selectedAction}
        scope={scope}
        targetId={targetId}
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setSelectedAction(null) }}
      />
    </div>
  )
}
