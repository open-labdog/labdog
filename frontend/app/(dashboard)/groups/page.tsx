"use client"

import { useState, useMemo } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { PencilIcon, CheckIcon, XIcon, Trash2Icon, PlayIcon, ShieldIcon, ServerIcon, UsersIcon, ClockIcon, PackageIcon, GlobeIcon, FileTextIcon, ShieldCheckIcon, AlertTriangleIcon } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Tooltip } from "@/components/ui/tooltip"
import { cn, useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { GitOpsStatusBadge } from "@/components/status-badge"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable } from "@/components/ui/data-table"
import type { ColumnDef } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import type { GroupSummary, ModuleCounts } from "@/lib/types"

const MODULE_ICONS: { key: keyof ModuleCounts; icon: typeof ShieldIcon; label: string }[] = [
  { key: "firewall", icon: ShieldIcon, label: "Firewall rules" },
  { key: "hosts_file", icon: FileTextIcon, label: "Hosts file entries" },
  { key: "services", icon: ServerIcon, label: "Services" },
  { key: "users", icon: UsersIcon, label: "Users / groups" },
  { key: "cron", icon: ClockIcon, label: "Cron jobs" },
  { key: "packages", icon: PackageIcon, label: "Packages" },
  { key: "resolver", icon: GlobeIcon, label: "DNS resolver" },
  { key: "ca_certs", icon: ShieldCheckIcon, label: "CA certificates" },
]

function ModuleBadges({ counts }: { counts: ModuleCounts }) {
  return (
    <div className="flex items-center gap-1">
      {MODULE_ICONS.map(({ key, icon: Icon, label }) => {
        const count = counts[key]
        const active = count > 0
        return (
          <Tooltip key={key} content={`${label}: ${count}`}>
            <span className={cn(
              "inline-flex items-center justify-center w-5 h-5 rounded",
              active ? "text-sky-400" : "text-slate-700"
            )}>
              <Icon className="w-3.5 h-3.5" />
            </span>
          </Tooltip>
        )
      })}
    </div>
  )
}

function PriorityBar({ priority, min, max }: { priority: number; min: number; max: number }) {
  const range = max - min || 1
  const pct = Math.round(((priority - min) / range) * 100)
  return (
    <div className="flex items-center gap-2">
      <span className="tabular-nums text-sm w-8 text-right">{priority}</span>
      <div className="w-16 h-1.5 rounded-full bg-slate-700 overflow-hidden">
        <div
          className="h-full rounded-full bg-sky-500/70"
          style={{ width: `${Math.max(pct, 6)}%` }}
        />
      </div>
    </div>
  )
}

export default function GroupsPage() {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkSyncing, setBulkSyncing] = useState(false)
  const [bulkAction, setBulkAction] = useState<"delete" | "sync" | null>(null)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [syncingGroup, setSyncingGroup] = useState<number | null>(null)
  const [categoryFilter, setCategoryFilter] = useState<string>("__all__")
  const [search, setSearch] = useState("")
  const [editingCategory, setEditingCategory] = useState<string | null>(null)
  const [editCategoryValue, setEditCategoryValue] = useState("")
  const queryClient = useQueryClient()

  const { data: groups, isLoading, error } = useQuery<GroupSummary[]>({
    queryKey: ["groups-summary"],
    queryFn: () => apiFetch<GroupSummary[]>("/api/groups/summary"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const allGroups = useMemo(() => groups ?? [], [groups])

  const { minPriority, maxPriority } = useMemo(() => {
    if (allGroups.length === 0) return { minPriority: 0, maxPriority: 1000 }
    const pris = allGroups.map(g => g.priority)
    return { minPriority: Math.min(...pris), maxPriority: Math.max(...pris) }
  }, [allGroups])

  const categories = useMemo(() => {
    const cats = new Set<string>()
    for (const g of allGroups) cats.add(g.category || "__uncategorized__")
    return [...cats].sort((a, b) => {
      if (a === "__uncategorized__") return 1
      if (b === "__uncategorized__") return -1
      return a.localeCompare(b)
    })
  }, [allGroups])

  const filteredGroups = useMemo(() => {
    let result = allGroups
    if (categoryFilter !== "__all__") {
      result = result.filter(g => (g.category || "__uncategorized__") === categoryFilter)
    }
    if (search) {
      const q = search.toLowerCase()
      result = result.filter(g =>
        g.name.toLowerCase().includes(q) ||
        (g.description?.toLowerCase().includes(q) ?? false)
      )
    }
    return result
  }, [allGroups, categoryFilter, search])

  const groupedByCategory = useMemo(() => {
    const sections = new Map<string, GroupSummary[]>()
    for (const group of filteredGroups) {
      const key = group.category || "__uncategorized__"
      if (!sections.has(key)) sections.set(key, [])
      sections.get(key)!.push(group)
    }
    return [...sections.entries()].sort(([a], [b]) => {
      if (a === "__uncategorized__") return 1
      if (b === "__uncategorized__") return -1
      return a.localeCompare(b)
    })
  }, [filteredGroups])

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
    setBulkAction("delete")
    setBulkProgress({ done: 0, total: ids.length })
    let success = 0, failed = 0
    for (const id of ids) {
      try {
        await apiFetch(`/api/groups/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
      setBulkProgress({ done: success + failed, total: ids.length })
    }
    setBulkDeleting(false)
    setBulkAction(null)
    setBulkProgress(null)
    setSelected(new Set())
    await queryClient.invalidateQueries({ queryKey: ["groups-summary"] })
    if (failed === 0) {
      showSuccess(`Deleted ${success} group${success !== 1 ? "s" : ""}`)
    } else {
      showError(`Deleted ${success} of ${ids.length}. ${failed} failed.`)
    }
    setBulkConfirmOpen(false)
  }

  async function handleBulkSync() {
    // Coalesced multi-module bulk sync per group: each selected group
    // dispatches a unified-playbook sync for every host it owns. Hosts
    // already in flight for a bulk sync are skipped server-side.
    const ids = Array.from(selected)
    setBulkSyncing(true)
    setBulkAction("sync")
    setBulkProgress({ done: 0, total: ids.length })
    let triggered = 0, skipped = 0, failed = 0
    for (const id of ids) {
      try {
        const resp = await apiFetch<{
          triggered_job_ids: number[]
          skipped_host_ids: number[]
        }>(`/api/sync/groups/${id}/bulk`, {
          method: "POST",
          json: { module_filter: null },
        })
        triggered += resp.triggered_job_ids.length
        skipped += resp.skipped_host_ids.length
      } catch {
        failed++
      }
      setBulkProgress({ done: triggered + skipped + failed, total: ids.length })
    }
    setBulkSyncing(false)
    setBulkAction(null)
    setBulkProgress(null)
    setSelected(new Set())
    await queryClient.invalidateQueries({ queryKey: ["groups-summary"] })
    if (failed === 0) {
      const skippedNote = skipped > 0 ? ` (${skipped} host${skipped !== 1 ? "s" : ""} already in flight, skipped)` : ""
      showSuccess(`Sync triggered for ${triggered} host${triggered !== 1 ? "s" : ""} across ${ids.length} group${ids.length !== 1 ? "s" : ""}${skippedNote}`)
    } else {
      showError(`Triggered ${triggered}; ${failed} group${failed !== 1 ? "s" : ""} failed.`)
    }
  }

  function startEditingCategory(category: string) {
    setEditingCategory(category)
    setEditCategoryValue(category === "__uncategorized__" ? "" : category)
  }

  async function handleRenameCategory(oldCategory: string) {
    const newCategory = editCategoryValue.trim() || null
    const oldValue = oldCategory === "__uncategorized__" ? null : oldCategory
    if (newCategory === oldValue) {
      setEditingCategory(null)
      return
    }
    const categoryGroups = allGroups.filter(g => (g.category || "__uncategorized__") === oldCategory)
    try {
      await Promise.all(
        categoryGroups.map(g =>
          apiFetch(`/api/groups/${g.id}`, {
            method: "PUT",
            json: { category: newCategory },
          })
        )
      )
      await queryClient.invalidateQueries({ queryKey: ["groups-summary"] })
      showSuccess(newCategory ? `Category renamed to "${newCategory}"` : "Category cleared")
    } catch {
      showError("Failed to rename category")
    }
    setEditingCategory(null)
  }

  async function handleDeleteCategory(category: string) {
    if (category === "__uncategorized__") return
    const categoryGroups = allGroups.filter(g => g.category === category)
    try {
      await Promise.all(
        categoryGroups.map(g =>
          apiFetch(`/api/groups/${g.id}`, {
            method: "PUT",
            json: { category: null },
          })
        )
      )
      await queryClient.invalidateQueries({ queryKey: ["groups-summary"] })
      showSuccess(`Category "${category}" removed`)
    } catch {
      showError("Failed to remove category")
    }
  }

  async function handleSyncGroup(groupId: number) {
    setSyncingGroup(groupId)
    const endpoints = [
      `/api/sync/groups/${groupId}/sync`,
      `/api/services/groups/${groupId}/sync`,
      `/api/hosts-mgmt/groups/${groupId}/sync`,
      `/api/linux-users/groups/${groupId}/sync`,
      `/api/cron/groups/${groupId}/sync`,
      `/api/packages/groups/${groupId}/sync`,
      `/api/resolver/groups/${groupId}/sync`,
    ]
    let success = 0
    for (const ep of endpoints) {
      try { await apiFetch(ep, { method: "POST" }); success++ } catch { /* skip modules with no config */ }
    }
    setSyncingGroup(null)
    if (success > 0) showSuccess("Sync triggered for all hosts in group")
    else showError("No modules to sync")
  }

  const cardColumns: ColumnDef<GroupSummary>[] = useMemo(() => [
    {
      key: "select",
      label: "",
      cell: (group) => (
        <input
          type="checkbox"
          checked={selected.has(group.id)}
          onChange={() => toggleSelect(group.id)}
          className="rounded border-slate-600"
        />
      ),
      defaultWidth: 40,
      resizable: false,
      sortable: false,
    },
    {
      key: "name",
      label: "Name",
      accessor: (g) => g.name,
      cell: (g) => (
        <Link href={`/groups/${g.id}`} className="text-white hover:text-blue-400 transition-colors font-medium">
          {g.name}
        </Link>
      ),
      defaultWidth: 180,
    },
    {
      key: "priority",
      label: "Priority",
      accessor: (g) => g.priority,
      cell: (g) => (
        <div className="flex items-center gap-1.5">
          <PriorityBar priority={g.priority} min={minPriority} max={maxPriority} />
          {g.has_shared_hosts && (
            <Tooltip content="Shares hosts with other groups — priority determines conflict resolution">
              <AlertTriangleIcon className="w-3.5 h-3.5 text-amber-500/80" />
            </Tooltip>
          )}
        </div>
      ),
      defaultWidth: 140,
    },
    {
      key: "hosts",
      label: "Hosts",
      accessor: (g) => g.host_count,
      cell: (g) => <span className="tabular-nums text-slate-300">{g.host_count}</span>,
      defaultWidth: 64,
    },
    {
      key: "modules",
      label: "Modules",
      cell: (g) => <ModuleBadges counts={g.module_counts} />,
      defaultWidth: 180,
      sortable: false,
    },
    {
      key: "gitops",
      label: "GitOps",
      accessor: (g) => g.gitops_enabled ? "enabled" : "disabled",
      cell: (g) => g.gitops_enabled && g.gitops_status
        ? <GitOpsStatusBadge status={g.gitops_status as import("@/lib/types").GitOpsStatus} />
        : <span className="text-slate-500">—</span>,
      defaultWidth: 90,
    },
    {
      key: "description",
      label: "Description",
      accessor: (g) => g.description ?? "",
      cell: (g) => <span className="text-slate-400 truncate" title={g.description ?? undefined}>{g.description ?? "—"}</span>,
    },
    {
      key: "actions",
      label: "Actions",
      cell: (group) => (
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={syncingGroup === group.id}
            onClick={() => handleSyncGroup(group.id)}
          >
            <PlayIcon className="w-3.5 h-3.5 mr-1" />
            {syncingGroup === group.id ? "..." : "Sync"}
          </Button>
          <Link href={`/groups/${group.id}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>View</Link>
        </div>
      ),
      defaultWidth: 160,
      resizable: false,
      sortable: false,
    },
  ], [selected, syncingGroup, minPriority, maxPriority])

  const displayLabel = (cat: string) => cat === "__uncategorized__" ? "Other" : cat

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Groups" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Groups</h1>
          <p className="text-slate-400 text-sm mt-1">Manage host groups for firewall rule organization</p>
        </div>
        <Link href="/groups/new" className={cn(buttonVariants())}>New Group</Link>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <Input
          placeholder="Search groups..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs h-9"
        />
        {categories.length > 1 && (
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="h-9 rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-slate-300 focus:outline-none focus:ring-2 focus:ring-slate-500"
          >
            <option value="__all__">All categories</option>
            {categories.map(cat => (
              <option key={cat} value={cat}>{displayLabel(cat)}</option>
            ))}
          </select>
        )}
      </div>

      {showLoading && <TableSkeleton rows={5} columns={5} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load groups</div>
      )}

      {!isLoading && !error && allGroups.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No groups yet.{" "}
          <Link href="/groups/new" className="underline hover:text-white">
            Create your first group
          </Link>
        </div>
      )}

      {!isLoading && !error && allGroups.length > 0 && (
        <>
          {selected.size > 0 && (
            <div className="flex items-center gap-3 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700">
              <span className="text-sm text-slate-300">{selected.size} selected</span>
              {bulkProgress ? (
                <span className="text-sm text-slate-400">
                  {bulkAction === "delete" ? "Deleting" : "Syncing"} {bulkProgress.done}/{bulkProgress.total}...
                </span>
              ) : (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleBulkSync}
                    disabled={bulkDeleting || bulkSyncing}
                  >
                    Sync Selected
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setBulkConfirmOpen(true)}
                    disabled={bulkDeleting || bulkSyncing}
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

          {filteredGroups.length === 0 ? (
            <div className="text-slate-400 py-8 text-center">No groups match your search.</div>
          ) : (
            <div className="space-y-4">
              {groupedByCategory.map(([category, categoryGroups]) => (
                <div key={category} className="rounded-lg border border-slate-700 overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-2.5 bg-slate-800/60 border-b border-slate-700">
                    {editingCategory === category ? (
                      <div className="flex items-center gap-1.5">
                        <Input
                          autoFocus
                          value={editCategoryValue}
                          onChange={e => setEditCategoryValue(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === "Enter") handleRenameCategory(category)
                            if (e.key === "Escape") setEditingCategory(null)
                          }}
                          placeholder="Category name (empty to clear)"
                          className="h-7 w-48 text-sm"
                        />
                        <button
                          onClick={() => handleRenameCategory(category)}
                          className="p-1 rounded hover:bg-slate-700 text-green-400 hover:text-green-300"
                        >
                          <CheckIcon className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => setEditingCategory(null)}
                          className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-white"
                        >
                          <XIcon className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 group/header">
                        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                          {displayLabel(category)}
                        </span>
                        <span className="text-xs text-slate-500">
                          {categoryGroups.length} {categoryGroups.length === 1 ? "group" : "groups"}
                        </span>
                        <span className="opacity-0 group-hover/header:opacity-100 flex items-center gap-0.5 ml-1 transition-opacity">
                          <button
                            onClick={() => startEditingCategory(category)}
                            className="p-1 rounded hover:bg-slate-700 text-slate-500 hover:text-white"
                            title="Rename category"
                          >
                            <PencilIcon className="w-3 h-3" />
                          </button>
                          {category !== "__uncategorized__" && (
                            <button
                              onClick={() => handleDeleteCategory(category)}
                              className="p-1 rounded hover:bg-slate-700 text-slate-500 hover:text-red-400"
                              title="Remove category"
                            >
                              <Trash2Icon className="w-3 h-3" />
                            </button>
                          )}
                        </span>
                      </div>
                    )}
                  </div>
                  <DataTable<GroupSummary>
                    tableId={`groups-cat-${category}`}
                    columns={cardColumns}
                    data={categoryGroups}
                    getRowKey={(g) => g.id}
                    emptyMessage="No groups in this category."
                  />
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Delete ${selected.size} ${selected.size === 1 ? "group" : "groups"}?`}
        description="This action cannot be undone."
        confirmLabel="Delete All"
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />
    </div>
  )
}
