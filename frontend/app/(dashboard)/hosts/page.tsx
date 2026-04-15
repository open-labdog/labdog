"use client"

import { useState, useMemo, useEffect, useRef } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDownIcon } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { cn, useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { DataTable } from "@/components/ui/data-table"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import type { Host, HostGroup } from "@/lib/types"

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "Never"
  const diff = Date.now() - new Date(dateStr).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours !== 1 ? "s" : ""} ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} day${days !== 1 ? "s" : ""} ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months} month${months !== 1 ? "s" : ""} ago`
  const years = Math.floor(months / 12)
  return `${years} year${years !== 1 ? "s" : ""} ago`
}

export default function HostsPage() {
  const [filterGroup, setFilterGroup] = useState<number | "ungrouped" | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [bulkDriftConfirmOpen, setBulkDriftConfirmOpen] = useState(false)
  const [bulkDriftTarget, setBulkDriftTarget] = useState<boolean>(true)
  const [bulkDriftUpdating, setBulkDriftUpdating] = useState(false)
  const [bulkDriftProgress, setBulkDriftProgress] = useState<{ done: number; total: number } | null>(null)
  const [groupDropdownOpen, setGroupDropdownOpen] = useState(false)
  const [groupSearch, setGroupSearch] = useState("")
  const groupDropdownRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (groupDropdownRef.current && !groupDropdownRef.current.contains(e.target as Node)) {
        setGroupDropdownOpen(false)
        setGroupSearch("")
      }
    }
    if (groupDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside)
      return () => document.removeEventListener("mousedown", handleClickOutside)
    }
  }, [groupDropdownOpen])

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
    const matchesGroup = filterGroup === null ? true
      : filterGroup === "ungrouped" ? h.group_ids.length === 0
      : h.group_ids.includes(filterGroup)
    return matchesGroup
  }) ?? []

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
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

  async function handleBulkDriftToggle() {
    const ids = Array.from(selected)
    setBulkDriftUpdating(true)
    setBulkDriftProgress({ done: 0, total: ids.length })
    let success = 0, failed = 0
    for (const id of ids) {
      try {
        await apiFetch(`/api/hosts/${id}`, {
          method: "PUT",
          body: JSON.stringify({ drift_check_enabled: bulkDriftTarget }),
        })
        success++
      } catch {
        failed++
      }
      setBulkDriftProgress({ done: success + failed, total: ids.length })
    }
    setBulkDriftUpdating(false)
    setBulkDriftProgress(null)
    setSelected(new Set())
    await queryClient.invalidateQueries({ queryKey: ["hosts"] })
    const label = bulkDriftTarget ? "enabled" : "disabled"
    if (failed === 0) {
      showSuccess(`Drift check ${label} for ${success} host${success !== 1 ? "s" : ""}`)
    } else {
      showError(`Updated ${success} of ${ids.length}. ${failed} failed.`)
    }
    setBulkDriftConfirmOpen(false)
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Hosts" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Hosts</h1>
          <p className="text-slate-400 text-sm mt-1">Manage hosts, configurations, and sync status</p>
        </div>
        <div className="flex gap-2">
          <Link href="/hosts/discover" className={cn(buttonVariants({ variant: "outline" }))}>
            Discover Hosts
          </Link>
          <Link href="/hosts/new" className={cn(buttonVariants())}>Add Host</Link>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div className="relative" ref={groupDropdownRef}>
          <Button
            variant="outline"
            onClick={() => { setGroupDropdownOpen(!groupDropdownOpen); setGroupSearch("") }}
          >
            {filterGroup === null ? "All Groups" : filterGroup === "ungrouped" ? "Ungrouped" : groupMap.get(filterGroup as number)?.name ?? "Unknown"}
            <ChevronDownIcon className="w-4 h-4" />
          </Button>
          {groupDropdownOpen && (
            <div className="absolute top-full left-0 z-50 mt-1 w-56 rounded-md border border-slate-700 bg-slate-900 shadow-lg">
              <div className="p-2 border-b border-slate-700">
                <input
                  autoFocus
                  type="text"
                  placeholder="Search groups..."
                  value={groupSearch}
                  onChange={(e) => setGroupSearch(e.target.value)}
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 outline-none focus:border-slate-500"
                />
              </div>
              <div className="max-h-60 overflow-y-auto py-1">
                {(!groupSearch || "all groups".includes(groupSearch.toLowerCase())) && (
                  <button
                    onClick={() => { setFilterGroup(null); setGroupDropdownOpen(false); setGroupSearch("") }}
                    className={cn("w-full text-left px-3 py-1.5 text-sm hover:bg-slate-800 transition-colors", filterGroup === null ? "text-white bg-slate-800" : "text-slate-300")}
                  >
                    All Groups
                  </button>
                )}
                {groups
                  ?.filter(g => !groupSearch || g.name.toLowerCase().includes(groupSearch.toLowerCase()))
                  .sort((a, b) => a.name.localeCompare(b.name))
                  .map(g => (
                    <button
                      key={g.id}
                      onClick={() => { setFilterGroup(g.id); setGroupDropdownOpen(false); setGroupSearch("") }}
                      className={cn("w-full text-left px-3 py-1.5 text-sm hover:bg-slate-800 transition-colors", filterGroup === g.id ? "text-white bg-slate-800" : "text-slate-300")}
                    >
                      {g.name}
                    </button>
                  ))}
                {(!groupSearch || "ungrouped".includes(groupSearch.toLowerCase())) && (
                  <button
                    onClick={() => { setFilterGroup("ungrouped"); setGroupDropdownOpen(false); setGroupSearch("") }}
                    className={cn("w-full text-left px-3 py-1.5 text-sm hover:bg-slate-800 transition-colors", filterGroup === "ungrouped" ? "text-white bg-slate-800" : "text-slate-300")}
                  >
                    Ungrouped
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
        {filterGroup !== null && (
          <span className="text-sm text-slate-400">
            Showing {filteredHosts.length} of {hosts?.length ?? 0} hosts
          </span>
        )}
      </div>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700">
          <span className="text-sm text-slate-300">{selected.size} selected</span>
          {bulkProgress ? (
            <span className="text-sm text-slate-400">Deleting {bulkProgress.done}/{bulkProgress.total}...</span>
          ) : bulkDriftProgress ? (
            <span className="text-sm text-slate-400">Updating drift check {bulkDriftProgress.done}/{bulkDriftProgress.total}...</span>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => { setBulkDriftTarget(true); setBulkDriftConfirmOpen(true) }}
                disabled={bulkDeleting || bulkDriftUpdating}
              >
                Enable Drift Check
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => { setBulkDriftTarget(false); setBulkDriftConfirmOpen(true) }}
                disabled={bulkDeleting || bulkDriftUpdating}
              >
                Disable Drift Check
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => setBulkConfirmOpen(true)}
                disabled={bulkDeleting || bulkDriftUpdating}
              >
                Delete Selected
              </Button>
            </>
          )}
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      )}

      {showLoading && <TableSkeleton rows={5} columns={4} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load hosts</div>
      )}

      {!isLoading && !error && (
        <DataTable<Host>
          tableId="hosts"
          data={filteredHosts}
          emptyMessage={
            hosts?.length === 0
              ? undefined
              : "No hosts match the current filter."
          }
          getRowKey={(h) => h.id}
          columns={[
            {
              key: "select",
              label: "",
              cell: (h) => (
                <input
                  type="checkbox"
                  checked={selected.has(h.id)}
                  onChange={() => toggleSelect(h.id)}
                  className="rounded border-slate-600"
                  aria-label={`Select ${h.hostname}`}
                />
              ),
              defaultWidth: 40,
              resizable: false,
              sortable: false,
            },
            {
              key: "hostname",
              label: "Hostname",
              accessor: (h) => h.hostname,
              cell: (h) => (
                <Link href={`/hosts/${h.id}`} className="text-white hover:text-blue-400 transition-colors font-medium">
                  {h.hostname}
                </Link>
              ),
              defaultWidth: 200,
              filter: { type: "text", placeholder: "e.g. web-01" },
            },
            {
              key: "ip_address",
              label: "IP Address",
              accessor: (h) => h.ip_address,
              cell: (h) => <span className="font-mono text-slate-300">{h.ip_address}</span>,
              defaultWidth: 140,
              filter: { type: "text", placeholder: "e.g. 10.0.1" },
            },
            {
              key: "drift_check_enabled",
              label: "Drift Check",
              accessor: (h) => h.drift_check_enabled,
              cell: (h) => (
                <Badge variant={h.drift_check_enabled ? "default" : "secondary"} className="text-xs">
                  {h.drift_check_enabled ? "Enabled" : "Disabled"}
                </Badge>
              ),
              defaultWidth: 120,
              filter: { type: "boolean", trueLabel: "Enabled", falseLabel: "Disabled" },
            },
            {
              key: "last_drift_check_at",
              label: "Last Drift Check",
              accessor: (h) => h.last_drift_check_at ?? "",
              cell: (h) => <span className="text-slate-400 text-sm">{formatRelativeTime(h.last_drift_check_at)}</span>,
              defaultWidth: 160,
              filter: { type: "dateRange" },
            },
            {
              key: "firewall_backend",
              label: "Firewall",
              accessor: (h) => h.firewall_backend,
              cell: (h) => <FirewallBadge backend={h.firewall_backend} />,
              defaultWidth: 120,
              filter: { type: "enum", options: [{label:"nftables",value:"nftables"},{label:"iptables",value:"iptables"},{label:"Unknown",value:"unknown"}] },
            },
            {
              key: "sync_status",
              label: "Status",
              accessor: (h) => h.sync_status,
              cell: (h) => <SyncStatusBadge status={h.sync_status} />,
              defaultWidth: 130,
              filter: { type: "enum", options: [{label:"Pending",value:"pending"},{label:"In Sync",value:"in_sync"},{label:"Out of Sync",value:"out_of_sync"},{label:"Unknown",value:"unknown"},{label:"Error",value:"error"}] },
            },
          ]}
        />
      )}

      {!isLoading && !error && hosts?.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No hosts yet.{" "}
          <Link href="/hosts/new" className="underline hover:text-white">
            Add your first host
          </Link>
        </div>
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
      <ConfirmDialog
        open={bulkDriftConfirmOpen}
        onOpenChange={setBulkDriftConfirmOpen}
        title={`${bulkDriftTarget ? "Enable" : "Disable"} drift check for ${selected.size} ${selected.size === 1 ? "host" : "hosts"}?`}
        description={bulkDriftTarget
          ? "Drift checking will be scheduled for the selected hosts."
          : "Drift checking will be stopped for the selected hosts."}
        confirmLabel={bulkDriftTarget ? "Enable" : "Disable"}
        loading={bulkDriftUpdating}
        onConfirm={handleBulkDriftToggle}
      />
    </div>
  )
}
