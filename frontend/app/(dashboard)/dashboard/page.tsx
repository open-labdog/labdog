"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { PlayIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import type { Host, SyncStatus } from "@/lib/types"
import { SyncStatusBadge } from "@/components/status-badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { useDelayedLoading, cn, formatRelativeTime } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { showSuccess, showError } from "@/lib/toast"
import { DataTable } from "@/components/ui/data-table"

const ROW_BORDER: Record<SyncStatus, string> = {
  in_sync: "border-l-2 border-l-green-500/60",
  out_of_sync: "border-l-2 border-l-amber-500/60",
  pending: "border-l-2 border-l-blue-500/60",
  unknown: "border-l-2 border-l-slate-600/60",
  error: "border-l-2 border-l-red-500/60",
}

const TRIAGE_ORDER: Record<SyncStatus, number> = {
  error: 0,
  out_of_sync: 1,
  pending: 2,
  unknown: 3,
  in_sync: 4,
}

function StatCard({ label, count, colorClass, sub, textValue }: { label: string; count?: number; colorClass: string; sub?: string; textValue?: string }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/40 px-4 py-3">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      {textValue
        ? <div className={cn("text-lg font-semibold", colorClass)}>{textValue}</div>
        : <div className={cn("text-2xl font-bold tabular-nums", colorClass)}>{count}</div>
      }
      {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function DashboardPage() {
  const [checkingAll, setCheckingAll] = useState(false)
  const [syncingHost, setSyncingHost] = useState<number | null>(null)

  const { data: hosts, isLoading: hostsLoading, refetch: refetchHosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    refetchInterval: 30000,
  })
  const showHostsLoading = useDelayedLoading(hostsLoading)

  const allHosts = hosts ?? []

  const statusCounts: Record<SyncStatus, number> = {
    in_sync: 0, out_of_sync: 0, error: 0, unknown: 0, pending: 0,
  }
  let neverChecked = 0
  let neverSynced = 0
  let lastCheckTime: string | null = null

  for (const h of allHosts) {
    statusCounts[h.sync_status] = (statusCounts[h.sync_status] ?? 0) + 1
    if (!h.last_drift_check_at) neverChecked++
    if (!h.last_sync_at) neverSynced++
    if (h.last_drift_check_at && (!lastCheckTime || h.last_drift_check_at > lastCheckTime)) {
      lastCheckTime = h.last_drift_check_at
    }
  }

  // Sort by triage priority: errors first
  const sortedHosts = [...allHosts].sort((a, b) =>
    (TRIAGE_ORDER[a.sync_status] ?? 3) - (TRIAGE_ORDER[b.sync_status] ?? 3)
  )

  const handleCheckAll = async () => {
    setCheckingAll(true)
    await Promise.allSettled(
      allHosts.map((h) =>
        apiFetch(`/api/hosts/${h.id}/collect-state`, { method: "POST" }).catch(() => null)
      )
    )
    await refetchHosts()
    setCheckingAll(false)
    showSuccess("State collection triggered for all hosts")
  }

  const handleSyncHost = async (hostId: number) => {
    setSyncingHost(hostId)
    try {
      await apiFetch(`/api/hosts/${hostId}/collect-state`, { method: "POST" })
      showSuccess("State collection triggered")
      await refetchHosts()
    } catch {
      showError("Failed to trigger state collection")
    } finally {
      setSyncingHost(null)
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Dashboard" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Fleet Overview</h1>
          <p className="text-slate-400 text-sm mt-1">
            Operational status across all hosts — auto-refreshes every 30s
          </p>
        </div>
        <Button onClick={handleCheckAll} variant="outline" disabled={checkingAll}>
          {checkingAll ? "Checking..." : "Check All"}
        </Button>
      </div>

      {/* Summary cards — two tiers */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <StatCard label="Total Hosts" count={allHosts.length} colorClass="text-white" />
        <StatCard label="Hosts in Sync" count={statusCounts.in_sync} colorClass="text-green-400" />
        <StatCard label="Hosts Drifted" count={statusCounts.out_of_sync} colorClass="text-amber-400" />
        <StatCard label="Hosts with Errors" count={statusCounts.error} colorClass="text-red-400" />
        <StatCard label="Unknown / Pending" count={statusCounts.unknown + statusCounts.pending} colorClass="text-slate-400" />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <StatCard
          label="Last Fleet Check"
          textValue={lastCheckTime ? formatRelativeTime(lastCheckTime) : "Never"}
          colorClass="text-slate-300"
        />
        <StatCard
          label="Never Checked"
          count={neverChecked}
          colorClass={neverChecked > 0 ? "text-amber-400" : "text-slate-500"}
          sub={`of ${allHosts.length} hosts`}
        />
        <StatCard
          label="Never Synced"
          count={neverSynced}
          colorClass={neverSynced > 0 ? "text-amber-400" : "text-slate-500"}
          sub={`of ${allHosts.length} hosts`}
        />
      </div>

      {/* Host triage table */}
      {showHostsLoading && <TableSkeleton rows={5} columns={5} />}

      {!hostsLoading && (
        <DataTable<Host>
          tableId="dashboard-v2"
          data={sortedHosts}
          emptyMessage={
            <>
              No hosts configured yet.{" "}
              <Link href="/hosts/new" className="underline hover:text-white">Add your first host</Link>
            </>
          }
          getRowKey={(h) => h.id}
          rowClassName={(h) => ROW_BORDER[h.sync_status] ?? ROW_BORDER.unknown}
          columns={[
            {
              key: "hostname",
              label: "Hostname",
              accessor: (h) => h.hostname,
              cell: (h) => (
                <Link href={`/hosts/${h.id}`} className="text-sm font-medium text-white hover:text-blue-400 transition-colors">
                  {h.hostname}
                </Link>
              ),
              defaultWidth: 180,
              filter: { type: "text", placeholder: "e.g. web-01" },
            },
            {
              key: "ip_address",
              label: "IP Address",
              accessor: (h) => h.ip_address,
              cell: (h) => <span className="font-mono text-sm text-slate-300">{h.ip_address}</span>,
              defaultWidth: 140,
              filter: { type: "text", placeholder: "e.g. 10.0.1" },
            },
            {
              key: "sync_status",
              label: "Status",
              accessor: (h) => h.sync_status,
              cell: (h) => <SyncStatusBadge status={h.sync_status} />,
              defaultWidth: 130,
              filter: { type: "enum", options: [{label:"Pending",value:"pending"},{label:"In Sync",value:"in_sync"},{label:"Out of Sync",value:"out_of_sync"},{label:"Unknown",value:"unknown"},{label:"Error",value:"error"}] },
            },
            {
              key: "last_drift",
              label: "Last Check",
              accessor: (h) => h.last_drift_check_at ?? "",
              cell: (h) => (
                <span className="text-sm text-slate-300" title={h.last_drift_check_at ? new Date(h.last_drift_check_at).toLocaleString() : undefined}>
                  {h.last_drift_check_at ? formatRelativeTime(h.last_drift_check_at) : <span className="text-slate-600">Never</span>}
                </span>
              ),
              defaultWidth: 120,
            },
            {
              key: "last_sync",
              label: "Last Sync",
              accessor: (h) => h.last_sync_at ?? "",
              cell: (h) => (
                <span className="text-sm text-slate-300" title={h.last_sync_at ? new Date(h.last_sync_at).toLocaleString() : undefined}>
                  {h.last_sync_at ? formatRelativeTime(h.last_sync_at) : <span className="text-slate-600">Never</span>}
                </span>
              ),
              defaultWidth: 120,
            },
            {
              key: "actions",
              label: "",
              cell: (h) => (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={syncingHost === h.id}
                  onClick={() => handleSyncHost(h.id)}
                  title="Collect current state from host"
                >
                  <PlayIcon className="w-3.5 h-3.5 mr-1" />
                  {syncingHost === h.id ? "..." : "Check"}
                </Button>
              ),
              defaultWidth: 100,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}
    </div>
  )
}
