"use client"

import { useState, useMemo, useEffect } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { LayoutListIcon, TableIcon, PencilIcon, CheckIcon, XIcon, Trash2Icon, PlayIcon } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { cn, useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { GitOpsStatusBadge } from "@/components/status-badge"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable } from "@/components/ui/data-table"
import type { ColumnDef } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import type { HostGroup } from "@/lib/types"

export default function GroupsPage() {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [syncingGroup, setSyncingGroup] = useState<number | null>(null)
  const [viewMode, setViewMode] = useState<"flat" | "grouped">(() =>
    typeof window !== "undefined" && localStorage.getItem("barricade-groups-view") === "grouped" ? "grouped" : "flat"
  )
  const [editingCategory, setEditingCategory] = useState<string | null>(null)
  const [editCategoryValue, setEditCategoryValue] = useState("")
  const queryClient = useQueryClient()

  useEffect(() => {
    localStorage.setItem("barricade-groups-view", viewMode)
  }, [viewMode])

  const { data: groups, isLoading, error } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const allGroups = groups ?? []

  const groupedByCategory = useMemo(() => {
    const sections = new Map<string, HostGroup[]>()
    for (const group of allGroups) {
      const key = group.category || "__uncategorized__"
      if (!sections.has(key)) sections.set(key, [])
      sections.get(key)!.push(group)
    }
    return [...sections.entries()].sort(([a], [b]) => {
      if (a === "__uncategorized__") return 1
      if (b === "__uncategorized__") return -1
      return a.localeCompare(b)
    })
  }, [allGroups])

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
        await apiFetch(`/api/groups/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
      setBulkProgress({ done: success + failed, total: ids.length })
    }
    setBulkDeleting(false)
    setBulkProgress(null)
    setSelected(new Set())
    await queryClient.invalidateQueries({ queryKey: ["groups"] })
    if (failed === 0) {
      showSuccess(`Deleted ${success} group${success !== 1 ? "s" : ""}`)
    } else {
      showError(`Deleted ${success} of ${ids.length}. ${failed} failed.`)
    }
    setBulkConfirmOpen(false)
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
    const categoryGroups = groups?.filter(g => (g.category || "__uncategorized__") === oldCategory) ?? []
    try {
      await Promise.all(
        categoryGroups.map(g =>
          apiFetch(`/api/groups/${g.id}`, {
            method: "PUT",
            json: { category: newCategory },
          })
        )
      )
      await queryClient.invalidateQueries({ queryKey: ["groups"] })
      showSuccess(newCategory ? `Category renamed to "${newCategory}"` : "Category cleared")
    } catch {
      showError("Failed to rename category")
    }
    setEditingCategory(null)
  }

  async function handleDeleteCategory(category: string) {
    if (category === "__uncategorized__") return
    const categoryGroups = groups?.filter(g => g.category === category) ?? []
    try {
      await Promise.all(
        categoryGroups.map(g =>
          apiFetch(`/api/groups/${g.id}`, {
            method: "PUT",
            json: { category: null },
          })
        )
      )
      await queryClient.invalidateQueries({ queryKey: ["groups"] })
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

  function buildColumns(rows: HostGroup[], showCategory: boolean): ColumnDef<HostGroup>[] {
    return [
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
        defaultWidth: 220,
        filter: { type: "text" },
      },
      ...(showCategory ? [{
        key: "category",
        label: "Category",
        accessor: (g: HostGroup) => g.category ?? "",
        cell: (g: HostGroup) => g.category
          ? <span className="text-slate-400">{g.category}</span>
          : <span className="text-slate-500">—</span>,
        defaultWidth: 140,
        filter: { type: "enum" as const, from: "accessor" as const },
      }] : []),
      {
        key: "priority",
        label: "Priority",
        accessor: (g) => g.priority,
        cell: (g) => <span className="tabular-nums">{g.priority}</span>,
        defaultWidth: 96,
      },
      {
        key: "gitops",
        label: "GitOps",
        accessor: (g) => g.gitops_enabled ? "enabled" : "disabled",
        cell: (g) => g.gitops_enabled && g.gitops_status
          ? <GitOpsStatusBadge status={g.gitops_status} />
          : <span className="text-slate-500">—</span>,
        defaultWidth: 120,
        filter: { type: "boolean" },
      },
      {
        key: "description",
        label: "Description",
        accessor: (g) => g.description ?? "",
        cell: (g) => <span className="text-slate-400 truncate">{g.description ?? "—"}</span>,
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
    ]
  }

  const flatColumns = useMemo(
    () => buildColumns(allGroups, true),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [allGroups, selected, syncingGroup]
  )

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

      <div className="flex items-center gap-2">
        <button
          onClick={() => setViewMode(viewMode === "flat" ? "grouped" : "flat")}
          className="flex items-center gap-1.5 h-9 px-3 rounded-md border border-slate-700 bg-slate-900 text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
        >
          {viewMode === "flat" ? <LayoutListIcon className="w-4 h-4" /> : <TableIcon className="w-4 h-4" />}
          {viewMode === "flat" ? "Category View" : "Flat View"}
        </button>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}

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

          {viewMode === "flat" ? (
            <DataTable<HostGroup>
              tableId="groups-flat"
              columns={flatColumns}
              data={allGroups}
              getRowKey={(g) => g.id}
              emptyMessage="No groups found."
            />
          ) : (
            <div className="space-y-4">
              {groupedByCategory.map(([category, categoryGroups]) => {
                const catColumns = buildColumns(categoryGroups, false)
                return (
                  <details key={category} open className="group">
                    <summary className="cursor-pointer flex items-center gap-2 py-2 px-1 text-sm font-medium text-slate-300 hover:text-white select-none">
                      <span className="transition-transform group-open:rotate-90">▶</span>
                      {editingCategory === category ? (
                        <span className="flex items-center gap-1.5" onClick={e => e.preventDefault()}>
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
                            title="Save"
                          >
                            <CheckIcon className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => setEditingCategory(null)}
                            className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-white"
                            title="Cancel"
                          >
                            <XIcon className="w-3.5 h-3.5" />
                          </button>
                        </span>
                      ) : (
                        <>
                          <span>{category === "__uncategorized__" ? "Uncategorized" : category}</span>
                          <span className="text-slate-500 font-normal">({categoryGroups.length})</span>
                          <span className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 ml-1" onClick={e => e.preventDefault()}>
                            <button
                              onClick={() => startEditingCategory(category)}
                              className="p-1 rounded hover:bg-slate-700 text-slate-500 hover:text-white"
                              title="Rename category"
                            >
                              <PencilIcon className="w-3.5 h-3.5" />
                            </button>
                            {category !== "__uncategorized__" && (
                              <button
                                onClick={() => handleDeleteCategory(category)}
                                className="p-1 rounded hover:bg-slate-700 text-slate-500 hover:text-red-400"
                                title="Remove category (moves groups to Uncategorized)"
                              >
                                <Trash2Icon className="w-3.5 h-3.5" />
                              </button>
                            )}
                          </span>
                        </>
                      )}
                    </summary>
                    <div className="mt-1">
                      <DataTable<HostGroup>
                        tableId={`groups-category-${category}`}
                        columns={catColumns}
                        data={categoryGroups}
                        getRowKey={(g) => g.id}
                        emptyMessage="No groups in this category."
                      />
                    </div>
                  </details>
                )
              })}
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
