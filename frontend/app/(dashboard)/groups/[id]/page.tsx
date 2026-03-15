"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { HostGroup, Host } from "@/lib/types"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { buttonVariants } from "@/components/ui/button"
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
import { cn } from "@/lib/utils"

export default function GroupDetailPage() {
  const params = useParams()
  const id = Number(params.id)

  const { data: groups, isLoading: groupsLoading } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  const { data: hosts, isLoading: hostsLoading } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })

  const group = groups?.find((g) => g.id === id)

  // Filter hosts that belong to this group
  // The API doesn't expose group membership directly on Host, so we show all hosts
  // and note that group membership is managed server-side
  const groupHosts = hosts ?? []

  if (groupsLoading) {
    return <div className="text-slate-400 py-8 text-center">Loading group…</div>
  }

  if (!group && !groupsLoading) {
    return (
      <div className="text-red-400 py-8 text-center">
        Group not found.{" "}
        <Link href="/groups" className="underline hover:text-white">
          Back to Groups
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/groups"
              className="text-slate-400 hover:text-white text-sm transition-colors"
            >
              Groups
            </Link>
            <span className="text-slate-600">/</span>
            <span className="text-white text-sm">{group?.name}</span>
          </div>
          <h1 className="text-2xl font-bold text-white">{group?.name}</h1>
          {group?.description && (
            <p className="text-slate-400 text-sm mt-1">{group.description}</p>
          )}
        </div>
        <div className="flex gap-3">
          <Link
            href={`/groups/${id}/rules`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Manage Rules
          </Link>
          <Link
            href={`/groups/${id}/sync`}
            className={cn(buttonVariants())}
          >
            Sync
          </Link>
        </div>
      </div>

      {/* Group info card */}
      {group && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm text-slate-400">Priority</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-white">{group.priority}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm text-slate-400">Created</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-sm text-slate-300">
                {new Date(group.created_at).toLocaleDateString()}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm text-slate-400">Last Updated</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-sm text-slate-300">
                {new Date(group.updated_at).toLocaleDateString()}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Quick actions */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h2 className="text-base font-semibold text-white mb-3">Quick Actions</h2>
        <div className="flex gap-3 flex-wrap">
          <Link
            href={`/groups/${id}/rules`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            View &amp; Edit Rules
          </Link>
          <Link
            href={`/groups/${id}/sync`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            Preview &amp; Apply Sync
          </Link>
        </div>
      </div>

      {/* Hosts section */}
      <div>
        <h2 className="text-lg font-semibold text-white mb-3">Hosts</h2>
        <p className="text-slate-400 text-sm mb-4">
          All hosts that may be affected by this group&apos;s rules.
        </p>

        {hostsLoading && (
          <div className="text-slate-400 py-4 text-center">Loading hosts…</div>
        )}

        {!hostsLoading && groupHosts.length === 0 && (
          <div className="text-slate-400 py-4 text-center">
            No hosts configured.{" "}
            <Link href="/hosts/new" className="underline hover:text-white">
              Add a host
            </Link>
          </div>
        )}

        {!hostsLoading && groupHosts.length > 0 && (
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead>Hostname</TableHead>
                  <TableHead>IP Address</TableHead>
                  <TableHead>Firewall</TableHead>
                  <TableHead>Sync Status</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groupHosts.map((host) => (
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
                    <TableCell>
                      <Link
                        href={`/hosts/${host.id}`}
                        className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}
                      >
                        View
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}
