"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { SearchIcon, XIcon } from "lucide-react"
import { buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { cn, useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
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
  const [searchQuery, setSearchQuery] = useState("")
  const { data: hosts, isLoading, error } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const filteredHosts = hosts?.filter(h => {
    const q = searchQuery.toLowerCase()
    return h.hostname.toLowerCase().includes(q) || h.ip_address.toLowerCase().includes(q)
  }) ?? []

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Hosts" }]} />
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

      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            placeholder="Search by hostname or IP..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 pr-8"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white"
            >
              <XIcon className="w-4 h-4" />
            </button>
          )}
        </div>
        {searchQuery && (
          <span className="text-sm text-slate-400">
            Showing {filteredHosts.length} of {hosts?.length ?? 0} hosts
          </span>
        )}
      </div>

      {showLoading && <TableSkeleton rows={5} columns={4} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load hosts</div>
      )}

      {!isLoading && !error && filteredHosts.length === 0 && searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No results matching &apos;{searchQuery}&apos;
        </div>
      )}

      {!isLoading && !error && hosts?.length === 0 && !searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No hosts yet.{" "}
          <Link href="/hosts/new" className="underline hover:text-white">
            Add your first host
          </Link>
        </div>
      )}

      {!isLoading && !error && filteredHosts.length > 0 && (
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
              {filteredHosts.map((host) => (
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
