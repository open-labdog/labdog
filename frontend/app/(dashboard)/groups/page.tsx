"use client"

import { useState } from "react"
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
  const queryClient = useQueryClient()

  const { data: groups, isLoading, error } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const filteredGroups = groups?.filter(g =>
    g.name.toLowerCase().includes(searchQuery.toLowerCase())
  ) ?? []

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
                    <TableCell className="font-medium text-white">{group.name}</TableCell>
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
                      <Link href={`/groups/${group.id}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>View</Link>
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
