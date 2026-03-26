"use client"

import { useState, useMemo, useEffect } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { SearchIcon, XIcon, LayoutListIcon, TableIcon, PencilIcon, CheckIcon, Trash2Icon, PlayIcon } from "lucide-react"
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
import { GitOpsStatusBadge } from "@/components/status-badge"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import type { HostGroup } from "@/lib/types"

export default function GroupsPage() {
  const [searchQuery, setSearchQuery] = useState("")
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

  const filteredGroups = groups?.filter(g =>
    g.name.toLowerCase().includes(searchQuery.toLowerCase())
  ) ?? []

  const groupedByCategory = useMemo(() => {
    const sections = new Map<string, HostGroup[]>()
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

  const toggleSelectAll = () => {
    if (selected.size === filteredGroups.length && filteredGroups.length > 0) {
      setSelected(new Set())
    } else {
      setSelected(new Set(filteredGroups.map(g => g.id)))
    }
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
        <div className="relative flex-1 max-w-sm">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            placeholder="Search groups..."
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
        <button
          onClick={() => setViewMode(viewMode === "flat" ? "grouped" : "flat")}
          className="flex items-center gap-1.5 h-9 px-3 rounded-md border border-slate-700 bg-slate-900 text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
        >
          {viewMode === "flat" ? <LayoutListIcon className="w-4 h-4" /> : <TableIcon className="w-4 h-4" />}
          {viewMode === "flat" ? "Category View" : "Flat View"}
        </button>
        {searchQuery && (
          <span className="text-sm text-slate-400">
            Showing {filteredGroups.length} of {groups?.length ?? 0} groups
          </span>
        )}
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load groups</div>
      )}

      {!isLoading && !error && filteredGroups.length === 0 && searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No results matching &apos;{searchQuery}&apos;
        </div>
      )}

      {!isLoading && !error && groups?.length === 0 && !searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No groups yet.{" "}
          <Link href="/groups/new" className="underline hover:text-white">
            Create your first group
          </Link>
        </div>
      )}

      {!isLoading && !error && filteredGroups.length > 0 && (
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
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead className="w-10">
                      <input
                        type="checkbox"
                        checked={selected.size === filteredGroups.length && filteredGroups.length > 0}
                        onChange={toggleSelectAll}
                        className="rounded border-slate-600"
                      />
                    </TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>GitOps</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredGroups.map((group) => (
                    <TableRow key={group.id} className="border-slate-700">
                      <TableCell>
                        <input
                          type="checkbox"
                          checked={selected.has(group.id)}
                          onChange={() => toggleSelect(group.id)}
                          className="rounded border-slate-600"
                        />
                      </TableCell>
                      <TableCell className="font-medium">
                        <Link href={`/groups/${group.id}`} className="text-white hover:text-blue-400 transition-colors">{group.name}</Link>
                      </TableCell>
                      <TableCell className="text-slate-400">{group.category ?? <span className="text-slate-500">—</span>}</TableCell>
                      <TableCell>{group.priority}</TableCell>
                      <TableCell>
                        {group.gitops_enabled && group.gitops_status ? (
                          <GitOpsStatusBadge status={group.gitops_status} />
                        ) : (
                          <span className="text-slate-500">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-slate-400">{group.description ?? "—"}</TableCell>
                      <TableCell>
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
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="space-y-4">
              {groupedByCategory.map(([category, categoryGroups]) => (
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
                  <div className="rounded-lg border border-slate-700 bg-slate-900 mt-1">
                    <Table>
                      <TableHeader>
                        <TableRow className="border-slate-700">
                          <TableHead className="w-10">
                            <input
                              type="checkbox"
                              checked={categoryGroups.every(g => selected.has(g.id)) && categoryGroups.length > 0}
                              onChange={() => {
                                const allSelected = categoryGroups.every(g => selected.has(g.id))
                                setSelected(prev => {
                                  const next = new Set(prev)
                                  categoryGroups.forEach(g => allSelected ? next.delete(g.id) : next.add(g.id))
                                  return next
                                })
                              }}
                              className="rounded border-slate-600"
                            />
                          </TableHead>
                          <TableHead>Name</TableHead>
                          <TableHead>Priority</TableHead>
                          <TableHead>GitOps</TableHead>
                          <TableHead>Description</TableHead>
                          <TableHead>Actions</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {categoryGroups.map((group) => (
                          <TableRow key={group.id} className="border-slate-700">
                            <TableCell>
                              <input
                                type="checkbox"
                                checked={selected.has(group.id)}
                                onChange={() => toggleSelect(group.id)}
                                className="rounded border-slate-600"
                              />
                            </TableCell>
                            <TableCell className="font-medium">
                              <Link href={`/groups/${group.id}`} className="text-white hover:text-blue-400 transition-colors">{group.name}</Link>
                            </TableCell>
                            <TableCell>{group.priority}</TableCell>
                            <TableCell>
                              {group.gitops_enabled && group.gitops_status ? (
                                <GitOpsStatusBadge status={group.gitops_status} />
                              ) : (
                                <span className="text-slate-500">—</span>
                              )}
                            </TableCell>
                            <TableCell className="text-slate-400">{group.description ?? "—"}</TableCell>
                            <TableCell>
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
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </details>
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
