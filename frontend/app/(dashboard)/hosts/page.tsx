"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
import type { Host } from "@/lib/types"

export default function HostsPage() {
  const { data: hosts, isLoading, error } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Hosts</h1>
          <p className="text-slate-400 text-sm mt-1">Manage firewall hosts</p>
        </div>
        <div className="flex gap-2">
          <Link href="/hosts/discover" className={cn(buttonVariants({ variant: "outline" }))}>
            Discover Hosts
          </Link>
          <Link href="/hosts/new" className={cn(buttonVariants())}>Add Host</Link>
        </div>
      </div>

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading hosts...</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load hosts</div>
      )}

      {!isLoading && !error && hosts && hosts.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No hosts yet.{" "}
          <Link href="/hosts/new" className="underline hover:text-white">
            Add your first host
          </Link>
        </div>
      )}

      {!isLoading && !error && hosts && hosts.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Hostname</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Firewall</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hosts.map((host) => (
                <TableRow key={host.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">{host.hostname}</TableCell>
                  <TableCell className="font-mono text-slate-300">{host.ip_address}</TableCell>
                  <TableCell>
                    <FirewallBadge backend={host.firewall_backend} />
                  </TableCell>
                  <TableCell>
                    <SyncStatusBadge status={host.sync_status} />
                  </TableCell>
                  <TableCell>
                    <Link href={`/hosts/${host.id}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>View</Link>
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
