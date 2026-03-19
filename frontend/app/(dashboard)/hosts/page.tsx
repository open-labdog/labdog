"use client"

import { useState, useMemo } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { SearchIcon, XIcon } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import type { Host, HostGroup } from "@/lib/types"

export default function HostsPage() {
  const [searchQuery, setSearchQuery] = useState("")
  const [filterGroup, setFilterGroup] = useState<number | "ungrouped" | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const queryClient = useQueryClient()

  const { data: hosts, isLoading, error } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })
  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })
  const groupMap = useMemo(() => {
    const map = new Map<number, HostGroup>()
    groups?.forEach(g => map.set(g.id, g))
    return map
  }, [groups])
  const showLoading = useDelayedLoading(isLoading)

  const filteredHosts = hosts?.filter(h => {
    const q = searchQuery.toLowerCase()
    const matchesSearch = h.hostname.toLowerCase().includes(q) || h.ip_address.toLowerCase().includes(q)
    const matchesGroup = filterGroup === null ? true
      : filterGroup === "ungrouped" ? h.group_ids.length === 0
      : h.group_ids.includes(filterGroup)
    return matchesSearch && matchesGroup
  }) ?? []

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selected.size === filteredHosts.length && filteredHosts.length > 0) {
      setSelected(new Set())
    } else {
      setSelected(new Set(filteredHosts.map(h => h.id)))
    }
  }

  async function handleBulkDelete() {
    const ids = Array.from(selected)
    setBulkDeleting(true)
    setBulkProgress({ done: 0, total: ids.length })
    let success = 0, failed = 0
    for (const id of ids) {
      try {
        await apiFetch(`/api/hosts/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
      setBulkProgress({ done: success + failed, total: ids.length })
    }
    setBulkDeleting(false)
    setBulkProgress(null)
    setSelected(new Set())
    await queryClient.invalidateQueries({ queryKey: ["hosts"] })
    if (failed === 0) {
      showSuccess(`Deleted ${success} host${success !== 1 ? "s" : ""}`)
    } else {
      showError(`Deleted ${success} of ${ids.length}. ${failed} failed.`)
    }
    setBulkConfirmOpen(false)
  }

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
        <select
          value={filterGroup === null ? "" : String(filterGroup)}
          onChange={(e) => {
            const v = e.target.value
            setFilterGroup(v === "" ? null : v === "ungrouped" ? "ungrouped" : Number(v))
          }}
          className="h-9 rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-slate-300"
        >
          <option value="">All Groups</option>
          {groups?.sort((a, b) => a.name.localeCompare(b.name)).map(g => (
            <option key={g.id} value={g.id}>{g.name}</option>
          ))}
          <option value="ungrouped">Ungrouped</option>
        </select>
        {(searchQuery || filterGroup !== null) && (
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
        <>
          {selected.size > 0 && (
            <div className="flex items-center gap-3 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700 mb-2">
              <span className="text-sm text-slate-300">{selected.size} selected</span>
              {bulkProgress ? (
                <span className="text-sm text-slate-400">Deleting {bulkProgress.done}/{bulkProgress.total}...</span>
              ) : (
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => setBulkConfirmOpen(true)}
                  disabled={bulkDeleting}
                >
                  Delete Selected
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                Clear
              </Button>
            </div>
          )}
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead className="w-10">
                    <input
                      type="checkbox"
                      checked={selected.size === filteredHosts.length && filteredHosts.length > 0}
                      onChange={toggleSelectAll}
                      className="rounded border-slate-600"
                    />
                  </TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead>IP Address</TableHead>
                  <TableHead>Groups</TableHead>
                  <TableHead>Firewall</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredHosts.map((host) => (
                  <TableRow key={host.id} className="border-slate-700">
                    <TableCell>
                      <input
                        type="checkbox"
                        checked={selected.has(host.id)}
                        onChange={() => toggleSelect(host.id)}
                        className="rounded border-slate-600"
                      />
                    </TableCell>
                    <TableCell className="font-medium text-white">{host.hostname}</TableCell>
                    <TableCell className="font-mono text-slate-300">{host.ip_address}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {host.group_ids.length > 0 ? (
                          host.group_ids.map(gid => {
                            const g = groupMap.get(gid)
                            return g ? (
                              <Link key={gid} href={`/groups/${gid}`} className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-white transition-colors">
                                {g.name}
                              </Link>
                            ) : null
                          })
                        ) : (
                          <span className="text-xs text-slate-500">&mdash;</span>
                        )}
                      </div>
                    </TableCell>
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
        </>
      )}

      <ConfirmDialog
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Delete ${selected.size} ${selected.size === 1 ? "host" : "hosts"}?`}
        description="This action cannot be undone."
        confirmLabel="Delete All"
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />
    </div>
  )
}
