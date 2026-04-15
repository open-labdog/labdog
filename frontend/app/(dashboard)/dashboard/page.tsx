"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { Host, SyncStatus } from "@/lib/types"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { useDelayedLoading } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { DataTable } from "@/components/ui/data-table"

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "Never"
  return new Date(dateStr).toLocaleString()
}

function SummaryCard({
  label,
  count,
  colorClass,
}: {
  label: string
  count: number
  colorClass: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm text-slate-400">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-3xl font-bold ${colorClass}`}>{count}</div>
      </CardContent>
    </Card>
  )
}

export default function DashboardPage() {
  const { data: hosts, isLoading: hostsLoading, refetch: refetchHosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    refetchInterval: 30000,
  })
  const showHostsLoading = useDelayedLoading(hostsLoading)

  const statusCounts: Record<SyncStatus, number> = {
    in_sync: 0,
    out_of_sync: 0,
    error: 0,
    unknown: 0,
    pending: 0,
  }

  if (hosts) {
    for (const host of hosts) {
      statusCounts[host.sync_status] = (statusCounts[host.sync_status] ?? 0) + 1
    }
  }

  const handleCheckAll = async () => {
    if (!hosts) return
    await Promise.allSettled(
      hosts.map((h) =>
        apiFetch(`/api/hosts/${h.id}/collect-state`, { method: "POST" }).catch(() => null)
      )
    )
    refetchHosts()
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Dashboard" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Drift Dashboard</h1>
          <p className="text-slate-400 text-sm mt-1">
            Live sync status across all hosts — auto-refreshes every 10s
          </p>
        </div>
        <Button onClick={handleCheckAll} variant="outline">
          Check All
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        <SummaryCard
          label="Total Hosts"
          count={hosts?.length ?? 0}
          colorClass="text-white"
        />
        <SummaryCard
          label="In Sync"
          count={statusCounts.in_sync}
          colorClass="text-green-400"
        />
        <SummaryCard
          label="Out of Sync"
          count={statusCounts.out_of_sync}
          colorClass="text-amber-400"
        />
        <SummaryCard
          label="Error"
          count={statusCounts.error}
          colorClass="text-red-400"
        />
        <SummaryCard
          label="Unknown"
          count={statusCounts.unknown + statusCounts.pending}
          colorClass="text-slate-400"
        />
      </div>

      {/* Host table */}
      {showHostsLoading && <TableSkeleton rows={5} columns={5} />}

      {!hostsLoading && (
        <DataTable<Host>
          tableId="dashboard-hosts"
          data={hosts}
          emptyMessage="No hosts configured yet."
          getRowKey={(h) => h.id}
          columns={[
            {
              key: "hostname",
              label: "Hostname",
              accessor: (h) => h.hostname,
              cell: (h) => (
                <Link href={`/hosts/${h.id}`} className="font-medium text-white hover:text-blue-400 transition-colors">
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
              cell: (h) => (
                <span className="font-mono text-slate-300 text-xs">{h.ip_address}</span>
              ),
              defaultWidth: 150,
              filter: { type: "text", placeholder: "e.g. 10.0.1" },
            },
            {
              key: "firewall",
              label: "Firewall",
              accessor: (h) => h.firewall_backend,
              cell: (h) => <FirewallBadge backend={h.firewall_backend} />,
              defaultWidth: 130,
              filter: { type: "enum", options: [{label:"nftables",value:"nftables"},{label:"iptables",value:"iptables"},{label:"Unknown",value:"unknown"}] },
            },
            {
              key: "sync_status",
              label: "Sync Status",
              accessor: (h) => h.sync_status,
              cell: (h) => <SyncStatusBadge status={h.sync_status} />,
              defaultWidth: 140,
              filter: { type: "enum", options: [{label:"Pending",value:"pending"},{label:"In Sync",value:"in_sync"},{label:"Out of Sync",value:"out_of_sync"},{label:"Unknown",value:"unknown"},{label:"Error",value:"error"}] },
            },
            {
              key: "last_drift_check",
              label: "Last Drift Check",
              accessor: (h) => h.last_drift_check_at ?? "",
              cell: (h) => (
                <span className="text-slate-400 text-xs">{formatDate(h.last_drift_check_at)}</span>
              ),
              defaultWidth: 180,
              filter: { type: "dateRange" },
            },
            {
              key: "last_sync",
              label: "Last Sync",
              accessor: (h) => h.last_sync_at ?? "",
              cell: (h) => (
                <span className="text-slate-400 text-xs">{formatDate(h.last_sync_at)}</span>
              ),
              defaultWidth: 180,
              filter: { type: "dateRange" },
            },
          ]}
        />
      )}
    </div>
  )
}
