"use client"

import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { Host, HostGroup, SyncStatus } from "@/lib/types"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

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
    refetchInterval: 10000,
  })
  const showHostsLoading = useDelayedLoading(hostsLoading)

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
    refetchInterval: 10000,
  })

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
    if (!groups) return
    await Promise.allSettled(
      groups.map((g) =>
        apiFetch(`/api/drift/groups/${g.id}/check`, { method: "POST" }).catch(() => null)
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

      {!hostsLoading && hosts && hosts.length === 0 && (
        <div className="text-slate-400 py-8 text-center">No hosts configured yet.</div>
      )}

      {!hostsLoading && hosts && hosts.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Hostname</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Firewall</TableHead>
                <TableHead>Sync Status</TableHead>
                <TableHead>Last Drift Check</TableHead>
                <TableHead>Last Sync</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hosts.map((host) => (
                <TableRow key={host.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">
                    {host.hostname}
                  </TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs">
                    {host.ip_address}
                  </TableCell>
                  <TableCell>
                    <FirewallBadge backend={host.firewall_backend} />
                  </TableCell>
                  <TableCell>
                    <SyncStatusBadge status={host.sync_status} />
                  </TableCell>
                  <TableCell className="text-slate-400 text-xs">
                    {formatDate(host.last_drift_check_at)}
                  </TableCell>
                  <TableCell className="text-slate-400 text-xs">
                    {formatDate(host.last_sync_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
