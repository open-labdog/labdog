"use client"

import { useState, useMemo, useEffect, useRef } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDownIcon } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { cn, useDelayedLoading, formatRelativeTime } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { DataTable } from "@/components/ui/data-table"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import { Tooltip } from "@/components/ui/tooltip"
import { ShieldIcon, FileTextIcon, ServerIcon, UsersIcon, ClockIcon, PackageIcon, GlobeIcon, ShieldCheckIcon } from "lucide-react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { HostGroup, HostSummary, ModuleCounts, SyncStatus, PendingSummary, PendingHostFleet } from "@/lib/types"

const MODULE_ICONS: { key: keyof ModuleCounts; icon: typeof ShieldIcon; label: string }[] = [
  { key: "firewall", icon: ShieldIcon, label: "Firewall" },
  { key: "hosts_file", icon: FileTextIcon, label: "Hosts file" },
  { key: "services", icon: ServerIcon, label: "Services" },
  { key: "users", icon: UsersIcon, label: "Users" },
  { key: "cron", icon: ClockIcon, label: "Cron" },
  { key: "packages", icon: PackageIcon, label: "Packages" },
  { key: "resolver", icon: GlobeIcon, label: "Resolver" },
  { key: "ca_certs", icon: ShieldCheckIcon, label: "CA certs" },
]

function OverrideBadges({ counts }: { counts: ModuleCounts }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  if (total === 0) return <span className="text-slate-700 text-xs">—</span>
  return (
    <div className="flex items-center gap-1">
      {MODULE_ICONS.map(({ key, icon: Icon, label }) => {
        const count = counts[key]
        if (count === 0) return null
        return (
          <Tooltip key={key} content={`${label}: ${count} override${count !== 1 ? "s" : ""}`}>
            <span className="inline-flex items-center justify-center w-5 h-5 rounded text-amber-400">
              <Icon className="w-3.5 h-3.5" />
            </span>
          </Tooltip>
        )
      })}
    </div>
  )
}

const ROW_BORDER: Record<SyncStatus, string> = {
  in_sync: "border-l-2 border-l-green-500/60",
  out_of_sync: "border-l-2 border-l-amber-500/60",
  pending: "border-l-2 border-l-blue-500/60",
  unknown: "border-l-2 border-l-slate-600/60",
  error: "border-l-2 border-l-red-500/60",
}

// ---------------------------------------------------------------------------
// Pending hosts approval section
// ---------------------------------------------------------------------------

function PendingSection({ queryClient }: { queryClient: ReturnType<typeof useQueryClient> }) {
  const [pendingSelected, setPendingSelected] = useState<Set<number>>(new Set())
  const [actionLoading, setActionLoading] = useState(false)

  const { data: summary } = useQuery<PendingSummary>({
    queryKey: ["scans", "pending-summary"],
    queryFn: () => apiFetch<PendingSummary>("/api/scans/pending-summary"),
    refetchInterval: 30000,
  })

  const { data: pendingHosts } = useQuery<PendingHostFleet[]>({
    queryKey: ["scans", "pending"],
    queryFn: () => apiFetch<PendingHostFleet[]>("/api/scans/pending"),
    enabled: (summary?.total ?? 0) > 0,
    refetchInterval: 30000,
  })

  if (!summary || summary.total === 0) return null

  const uniqueConfigCount = new Set(pendingHosts?.map((h) => h.scan_config_id) ?? []).size
  const allIds = pendingHosts?.map((h) => h.id) ?? []
  const allSelected = allIds.length > 0 && allIds.every((id) => pendingSelected.has(id))

  function toggleAll() {
    if (allSelected) {
      setPendingSelected(new Set())
    } else {
      setPendingSelected(new Set(allIds))
    }
  }

  function toggleOne(id: number) {
    setPendingSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function invalidateAfterAction() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scans", "pending-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["scans", "pending"] }),
      queryClient.invalidateQueries({ queryKey: ["hosts-summary"] }),
    ])
  }

  async function handleApprove() {
    if (pendingSelected.size === 0) return
    const selectedHosts = pendingHosts?.filter((h) => pendingSelected.has(h.id)) ?? []

    // Group by scan_config_id
    const byConfig = new Map<number, number[]>()
    for (const h of selectedHosts) {
      const existing = byConfig.get(h.scan_config_id) ?? []
      existing.push(h.id)
      byConfig.set(h.scan_config_id, existing)
    }

    setActionLoading(true)
    let totalApproved = 0
    let totalSkipped = 0
    let hadError = false

    const results = await Promise.allSettled(
      Array.from(byConfig.entries()).map(([configId, ids]) =>
        apiFetch<{ approved: number; skipped: number; skipped_ips: string[] }>(
          `/api/scans/${configId}/pending/approve`,
          { method: "POST", body: JSON.stringify({ ids }) }
        )
      )
    )

    for (const result of results) {
      if (result.status === "fulfilled") {
        totalApproved += result.value.approved
        totalSkipped += result.value.skipped
      } else {
        hadError = true
      }
    }

    await invalidateAfterAction()
    setPendingSelected(new Set())
    setActionLoading(false)

    if (hadError) {
      showError("Some approvals failed. Please try again.")
    } else if (totalSkipped > 0) {
      showSuccess(`Approved ${totalApproved} host${totalApproved !== 1 ? "s" : ""} (${totalSkipped} skipped as duplicate${totalSkipped !== 1 ? "s" : ""})`)
    } else {
      showSuccess(`Approved ${totalApproved} host${totalApproved !== 1 ? "s" : ""}`)
    }
  }

  async function handleDismiss() {
    if (pendingSelected.size === 0) return
    const selectedHosts = pendingHosts?.filter((h) => pendingSelected.has(h.id)) ?? []

    // Group by scan_config_id
    const byConfig = new Map<number, number[]>()
    for (const h of selectedHosts) {
      const existing = byConfig.get(h.scan_config_id) ?? []
      existing.push(h.id)
      byConfig.set(h.scan_config_id, existing)
    }

    setActionLoading(true)
    let totalDismissed = 0
    let hadError = false

    const results = await Promise.allSettled(
      Array.from(byConfig.entries()).map(([configId, ids]) =>
        apiFetch<{ dismissed: number }>(
          `/api/scans/${configId}/pending/dismiss`,
          { method: "POST", body: JSON.stringify({ ids }) }
        )
      )
    )

    for (const result of results) {
      if (result.status === "fulfilled") {
        totalDismissed += result.value.dismissed
      } else {
        hadError = true
      }
    }

    await invalidateAfterAction()
    setPendingSelected(new Set())
    setActionLoading(false)

    if (hadError) {
      showError("Some dismissals failed. Please try again.")
    } else {
      showSuccess(`Dismissed ${totalDismissed} host${totalDismissed !== 1 ? "s" : ""}`)
    }
  }

  return (
    <details className="group rounded-lg border border-amber-500/40 bg-slate-900">
      <summary className="flex cursor-pointer select-none list-none items-center gap-2.5 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <span className="h-2 w-2 shrink-0 rounded-full bg-amber-500" />
        <span className="text-sm font-medium text-amber-400">
          {summary.total} host{summary.total !== 1 ? "s" : ""} pending review
          {uniqueConfigCount > 0 && (
            <> &middot; From {uniqueConfigCount} scan config{uniqueConfigCount !== 1 ? "s" : ""}</>
          )}
        </span>
        <ChevronDownIcon className="ml-auto h-4 w-4 text-slate-400 transition-transform group-open:rotate-180" />
      </summary>

      <div className="border-t border-amber-500/20 px-4 pb-4 pt-3">
        {/* Toolbar */}
        <div className="mb-3 flex items-center gap-2">
          <span className="text-xs text-slate-400">
            {pendingSelected.size > 0 ? `${pendingSelected.size} selected` : "Select hosts to act"}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={pendingSelected.size === 0 || actionLoading}
            onClick={handleApprove}
          >
            {actionLoading ? "Working..." : "Approve selected"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={pendingSelected.size === 0 || actionLoading}
            onClick={handleDismiss}
          >
            Dismiss selected
          </Button>
        </div>

        {/* Table */}
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead className="w-10">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="rounded border-slate-600"
                    aria-label="Select all pending hosts"
                  />
                </TableHead>
                <TableHead className="text-slate-400 text-xs">IP</TableHead>
                <TableHead className="text-slate-400 text-xs">Hostname</TableHead>
                <TableHead className="text-slate-400 text-xs">From config</TableHead>
                <TableHead className="text-slate-400 text-xs">Discovered</TableHead>
                <TableHead className="text-slate-400 text-xs">SSH</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pendingHosts && pendingHosts.length > 0 ? (
                pendingHosts.map((host) => (
                  <TableRow key={host.id} className="border-slate-700">
                    <TableCell>
                      <input
                        type="checkbox"
                        checked={pendingSelected.has(host.id)}
                        onChange={() => toggleOne(host.id)}
                        className="rounded border-slate-600"
                        aria-label={`Select ${host.ip_address}`}
                      />
                    </TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{host.ip_address}</TableCell>
                    <TableCell className="text-slate-300 text-xs">
                      {host.hostname ?? <span className="text-slate-500">—</span>}
                    </TableCell>
                    <TableCell className="text-xs">
                      <Link
                        href={`/hosts/scans/${host.scan_config_id}/pending`}
                        className="text-blue-400 hover:underline"
                      >
                        {host.scan_config_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-slate-400 text-xs">
                      {formatRelativeTime(host.discovered_at)}
                    </TableCell>
                    <TableCell>
                      {host.ssh_verified ? (
                        <Badge className="bg-green-600 text-white text-xs">verified</Badge>
                      ) : host.ssh_error ? (
                        <Tooltip content={host.ssh_error}>
                          <Badge className="bg-amber-600 text-white text-xs cursor-help">unverified</Badge>
                        </Tooltip>
                      ) : (
                        <Badge className="bg-amber-600 text-white text-xs">unverified</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-slate-400 text-sm py-4">
                    Loading...
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </details>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

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

  const { data: hosts, isLoading, error } = useQuery<HostSummary[]>({
    queryKey: ["hosts-summary"],
    queryFn: () => apiFetch<HostSummary[]>("/api/hosts/summary"),
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
    await queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
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
    await queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
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

      <PendingSection queryClient={queryClient} />

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

      {showLoading && <TableSkeleton rows={5} columns={6} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load hosts</div>
      )}

      {!isLoading && !error && (
        <DataTable<HostSummary>
          tableId="hosts-v3"
          data={filteredHosts}
          emptyMessage={
            hosts?.length === 0
              ? undefined
              : "No hosts match the current filter."
          }
          getRowKey={(h) => h.id}
          rowClassName={(h) => ROW_BORDER[h.sync_status] ?? ROW_BORDER.unknown}
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
                <Link href={`/hosts/${h.id}`} className="text-sm text-white hover:text-blue-400 transition-colors font-medium truncate">
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
              cell: (h) => <span className="font-mono text-slate-300 text-sm">{h.ip_address}</span>,
              defaultWidth: 140,
              filter: { type: "text", placeholder: "e.g. 10.0.1" },
            },
            {
              key: "groups",
              label: "Groups",
              accessor: (h) => h.group_ids.map(id => groupMap.get(id)?.name ?? "").join(" "),
              cell: (h) => (
                h.group_ids.length === 0
                  ? <span className="text-slate-600 text-xs italic">ungrouped</span>
                  : <div className="flex items-center gap-1.5 overflow-hidden">
                      {h.group_ids.slice(0, 2).map((gid, i) => (
                        <span key={gid} className="text-sm text-slate-300 flex items-center gap-1.5 shrink-0">
                          {i > 0 && <span className="w-px h-3 bg-slate-600" />}
                          {groupMap.get(gid)?.name ?? `#${gid}`}
                        </span>
                      ))}
                      {h.group_ids.length > 2 && (
                        <span className="text-xs text-slate-400 shrink-0">+{h.group_ids.length - 2}</span>
                      )}
                    </div>
              ),
              defaultWidth: 180,
              filter: { type: "text", placeholder: "group name" },
            },
            {
              key: "overrides",
              label: "Overrides",
              cell: (h) => <OverrideBadges counts={h.override_counts} />,
              defaultWidth: 160,
              sortable: false,
            },
            {
              key: "sync_drift",
              label: "Sync / Drift",
              accessor: (h) => h.last_sync_at ?? "",
              cell: (h) => (
                <div className="leading-relaxed">
                  <div className="text-sm text-slate-300">
                    {h.last_sync_at ? formatRelativeTime(h.last_sync_at) : <span className="text-xs text-slate-500">Never synced</span>}
                  </div>
                  <div className="text-xs text-slate-500">
                    {h.drift_check_enabled
                      ? (h.last_drift_check_at ? `drift ${formatRelativeTime(h.last_drift_check_at)}` : "drift: never")
                      : "drift off"}
                  </div>
                </div>
              ),
              defaultWidth: 130,
            },
            {
              key: "firewall_backend",
              label: "Firewall",
              accessor: (h) => h.firewall_backend,
              cell: (h) => <FirewallBadge backend={h.firewall_backend} />,
              defaultWidth: 100,
              filter: { type: "enum", options: [{label:"nftables",value:"nftables"},{label:"iptables",value:"iptables"},{label:"Unknown",value:"unknown"}] },
            },
            {
              key: "sync_status",
              label: "Status",
              accessor: (h) => h.sync_status,
              cell: (h) => <SyncStatusBadge status={h.sync_status} />,
              defaultWidth: 120,
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
