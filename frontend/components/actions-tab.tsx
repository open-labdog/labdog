"use client"

import { useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { ActionCard } from "@/components/action-card"
import { ActionRunDialog } from "@/components/action-run-dialog"
import { RunStatusBadge } from "@/components/status-badge"
import { DataTable } from "@/components/ui/data-table"
import { TableSkeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/lib/api"
import { formatRelativeTime } from "@/lib/utils"
import type { ColumnDef } from "@/components/ui/data-table"
import type { ActionDefinition, ActionRun, Host } from "@/lib/types"
import Link from "next/link"
import { useRouter } from "next/navigation"

interface ActionsTabProps {
  scope: "host" | "group"
  targetId: number
  host?: Host
}

function formatDuration(run: ActionRun): string {
  if (!run.started_at || !run.finished_at) return "—"
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export function ActionsTab({ scope, targetId, host }: ActionsTabProps) {
  const router = useRouter()
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

  const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
  useEffect(() => {
    if (scope !== "host" || !host) return
    const stale =
      !host.os_facts_collected_at ||
      Date.now() - new Date(host.os_facts_collected_at).getTime() > SEVEN_DAYS_MS
    if (stale) {
      apiFetch(`/api/hosts/${targetId}/facts/refresh`, { method: "POST" }).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, host?.id, host?.os_facts_collected_at])

  const filteredCatalog = (catalog ?? []).filter((a) =>
    scope === "host" ? a.supports_host : a.supports_group
  )

  const runColumns: ColumnDef<ActionRun>[] = [
    {
      key: "action_key",
      label: "Action",
      cell: (r) => {
        const def = (catalog ?? []).find((a) => a.key === r.action_key)
        return <span className="text-sm">{def?.name ?? r.action_key}</span>
      },
    },
    {
      key: "status",
      label: "Status",
      cell: (r) => <RunStatusBadge status={r.status} />,
      defaultWidth: 100,
    },
    {
      key: "duration",
      label: "Duration",
      cell: (r) => <span className="text-xs text-slate-400">{formatDuration(r)}</span>,
      defaultWidth: 80,
    },
    {
      key: "created_at",
      label: "Started",
      cell: (r) => (
        <span className="text-xs text-slate-400" title={new Date(r.created_at).toLocaleString()}>
          {formatRelativeTime(r.created_at)}
        </span>
      ),
      defaultWidth: 100,
    },
  ]

  const runsBasePath = scope === "host" ? `/hosts/${targetId}` : `/groups/${targetId}`

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
            <DataTable
              tableId="action-runs"
              columns={runColumns}
              data={runs}
              onRowClick={(r) => router.push(`${runsBasePath}/actions/runs/${r.id}`)}
              rowClassName={() => "cursor-pointer"}
            />
          )}
        </div>
      </div>

      <ActionRunDialog
        action={selectedAction}
        scope={scope}
        targetId={targetId}
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setSelectedAction(null) }}
        hostOsCodename={scope === "host" ? host?.os_codename : undefined}
      />
    </div>
  )
}
