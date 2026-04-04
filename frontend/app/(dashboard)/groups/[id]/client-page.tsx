"use client"

import { useState, useEffect, useMemo } from "react"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import type { HostGroup, Host, GitRepository } from "@/lib/types"
import { SyncStatusBadge, FirewallBadge, GitOpsStatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Breadcrumb } from "@/components/ui/breadcrumb"
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
import { cn, useDelayedLoading } from "@/lib/utils"
import { CardSkeleton, TableSkeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { groupSchema, type GroupInput } from "@/lib/schemas"
import GroupRulesPage from "./rules/client-page"
import GroupServicesPage from "./services/client-page"
import GroupHostsEntriesPage from "./hosts-entries/client-page"
import GroupUsersPage from "./users/client-page"
import GroupCronJobsPage from "./cron-jobs/client-page"
import GroupPackagesPage from "./packages/client-page"
import GroupResolverPage from "./resolver/client-page"
import GroupSyncPage from "./sync/client-page"
import WorkflowConfigPage from "./workflow/client-page"

type Tab = "overview" | "rules" | "services" | "hosts-file" | "users" | "cron-jobs" | "packages" | "dns" | "sync" | "workflow"

export default function GroupDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()
  const searchParams = useSearchParams()
  const initialTab = (searchParams.get("tab") as Tab) || "overview"
  const [activeTab, setActiveTab] = useState<Tab>(initialTab)
  const [enableDialogOpen, setEnableDialogOpen] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [addHostsOpen, setAddHostsOpen] = useState(false)
  const [addHostsSearch, setAddHostsSearch] = useState("")
  const [addHostsSelected, setAddHostsSelected] = useState<Set<number>>(new Set())
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(null)
  const [filePath, setFilePath] = useState("")
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const editForm = useForm<GroupInput>({
    resolver: zodResolver(groupSchema),
    mode: "onSubmit",
  })

  const { data: groups, isLoading: groupsLoading } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  const { data: hosts, isLoading: hostsLoading } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })
  const showGroupLoading = useDelayedLoading(groupsLoading)
  const showHostsLoading = useDelayedLoading(hostsLoading)

  const { data: gitRepos } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })

  const enableGitopsMutation = useApiMutation<unknown, { git_repository_id: number; file_path: string }, HostGroup>({
    mutationFn: (data) =>
      apiFetch(`/api/groups/${id}/gitops/enable`, { method: "POST", body: JSON.stringify(data) }),
    invalidateKeys: [["groups"]],
    onSuccess: () => { setEnableDialogOpen(false); setSelectedRepoId(null); setFilePath("") },
    optimisticUpdate: {
      queryKey: ["groups"],
      updater: (old, data) => old.map((g) =>
        g.id === id ? { ...g, gitops_enabled: true, git_repository_id: data.git_repository_id, gitops_file_path: data.file_path } : g
      ),
    },
  })

  const disableGitopsMutation = useApiMutation<unknown, void, HostGroup>({
    mutationFn: () => apiFetch(`/api/groups/${id}/gitops/disable`, { method: "POST" }),
    invalidateKeys: [["groups"]],
    optimisticUpdate: {
      queryKey: ["groups"],
      updater: (old) => old.map((g) =>
        g.id === id ? { ...g, gitops_enabled: false, gitops_status: null, gitops_error_message: null } : g
      ),
    },
  })

  const group = groups?.find((g) => g.id === id)

  const groupHosts = hosts?.filter((h) => h.group_ids?.includes(id)) ?? []

  const availableHosts = useMemo(() => {
    const notInGroup = hosts?.filter((h) => !h.group_ids?.includes(id)) ?? []
    const q = addHostsSearch.toLowerCase()
    if (!q) return notInGroup
    return notInGroup.filter(
      (h) => h.hostname.toLowerCase().includes(q) || h.ip_address.includes(q)
    )
  }, [hosts, id, addHostsSearch])

  const addHostsMutation = useApiMutation<unknown, { host_ids: number[] }, Host>({
    mutationFn: (data) =>
      apiFetch(`/api/groups/${id}/hosts`, { method: "POST", body: JSON.stringify(data) }),
    invalidateKeys: [["hosts"]],
    onSuccess: () => {
      setAddHostsOpen(false)
      setAddHostsSelected(new Set())
      setAddHostsSearch("")
    },
    successMessage: "Hosts added to group",
  })

  function toggleAddHost(hostId: number) {
    setAddHostsSelected((prev) => {
      const next = new Set(prev)
      if (next.has(hostId)) next.delete(hostId)
      else next.add(hostId)
      return next
    })
  }

  function toggleAddAllHosts() {
    if (addHostsSelected.size === availableHosts.length && availableHosts.length > 0) {
      setAddHostsSelected(new Set())
    } else {
      setAddHostsSelected(new Set(availableHosts.map((h) => h.id)))
    }
  }

  // Update form when group loads
  useEffect(() => {
    if (group && editDialogOpen) {
      editForm.reset({
        name: group.name,
        description: group.description || "",
        category: group.category || "",
        priority: group.priority,
      })
    }
  }, [group, editDialogOpen, editForm])

  const editMutation = useApiMutation<HostGroup, GroupInput, HostGroup>({
    mutationFn: (data) =>
      apiFetch(`/api/groups/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [["groups"]],
    onSuccess: () => setEditDialogOpen(false),
    successMessage: "Group updated",
    optimisticUpdate: {
      queryKey: ["groups"],
      updater: (old, data) => old.map((g) =>
        g.id === id ? { ...g, ...data } : g
      ),
    },
  })

  const handleEditSubmit = editForm.handleSubmit(async (data) => {
    editMutation.mutate(data)
  })

  function relativeTime(iso: string | null): string {
    if (!iso) return "Never"
    const diff = Date.now() - new Date(iso).getTime()
    const seconds = Math.floor(diff / 1000)
    if (seconds < 60) return "just now"
    const minutes = Math.floor(seconds / 60)
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`
    const days = Math.floor(hours / 24)
    return `${days} day${days === 1 ? "" : "s"} ago`
  }

  function handleEnableGitOps(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedRepoId) return
    enableGitopsMutation.mutate({ git_repository_id: selectedRepoId, file_path: filePath })
  }

  function handleDisableGitOps() {
    setConfirmState({
      open: true,
      title: "Disable GitOps",
      description: "Rules will remain but will no longer sync from Git. This action cannot be undone.",
      action: async () => {
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try {
          await disableGitopsMutation.mutateAsync(undefined as never)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  if (showGroupLoading) {
    return <CardSkeleton />
  }

  if (groupsLoading) {
    return null
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
      <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group" }]} />
       {/* Header */}
       <div className="flex items-center justify-between">
         <div>
           <div className="flex items-center gap-3">
             <h1 className="text-2xl font-bold text-white">{group?.name}</h1>
             {group?.gitops_enabled && (
               <Badge className="bg-indigo-600 text-white">Managed by GitOps</Badge>
             )}
           </div>
           {group?.description && (
             <p className="text-slate-400 text-sm mt-1">{group.description}</p>
           )}
         </div>
         <div className="flex items-center gap-2">
           <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
             <DialogTrigger render={<Button variant="outline" size="sm" />}>
               Edit Group
             </DialogTrigger>
             <DialogContent>
               <DialogHeader>
                 <DialogTitle>Edit Group</DialogTitle>
               </DialogHeader>
               <form onSubmit={handleEditSubmit} className="space-y-4 mt-2">
                 <div className="space-y-2">
                   <Label htmlFor="edit-name">Name</Label>
                   <Input
                     id="edit-name"
                     type="text"
                     placeholder="e.g. production-servers"
                     {...editForm.register("name")}
                   />
                   {editForm.formState.errors.name && (
                     <p className="text-sm text-red-400">{editForm.formState.errors.name.message}</p>
                   )}
                 </div>
                 <div className="space-y-2">
                   <Label htmlFor="edit-description">Description</Label>
                   <textarea
                     id="edit-description"
                     placeholder="Optional description..."
                     {...editForm.register("description")}
                     rows={3}
                     className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring resize-none dark:bg-input/30"
                   />
                 </div>
                 <div className="space-y-2">
                   <Label htmlFor="edit-category">Category</Label>
                   <Input
                     id="edit-category"
                     type="text"
                     placeholder="e.g. Production, Security, Networking"
                     {...editForm.register("category")}
                   />
                 </div>
                 <div className="space-y-2">
                   <Label htmlFor="edit-priority">Priority</Label>
                   <Input
                     id="edit-priority"
                     type="number"
                     {...editForm.register("priority", { valueAsNumber: true })}
                     min={1}
                     max={1000}
                   />
                   {editForm.formState.errors.priority && (
                     <p className="text-sm text-red-400">{editForm.formState.errors.priority.message}</p>
                   )}
                 </div>
                 {editMutation.error && (
                   <p className="text-sm text-red-400">{editMutation.error.message}</p>
                 )}
                 <DialogFooter>
                   <Button type="button" variant="outline" onClick={() => setEditDialogOpen(false)}>
                     Cancel
                   </Button>
                   <Button type="submit" disabled={editMutation.isPending}>
                     {editMutation.isPending ? "Saving..." : "Save Changes"}
                   </Button>
                 </DialogFooter>
               </form>
             </DialogContent>
           </Dialog>
         </div>
       </div>

      {/* Group info card */}
      {group && (
        <div className={`grid grid-cols-1 gap-4 ${group.gitops_enabled ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
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
          {group.gitops_enabled && group.gitops_status && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-slate-400">GitOps Status</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-col gap-2">
                  <GitOpsStatusBadge status={group.gitops_status} />
                  {group.gitops_file_path && (
                    <div className="text-xs text-slate-400 font-mono truncate" title={group.gitops_file_path}>
                      {group.gitops_file_path}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Sync & GitOps */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Sync Status */}
        {(() => {
          const synced = groupHosts.filter((h) => h.sync_status === "in_sync").length
          const outOfSync = groupHosts.filter((h) => h.sync_status === "out_of_sync").length
          const errored = groupHosts.filter((h) => h.sync_status === "error").length
          const unknown = groupHosts.filter((h) => h.sync_status === "unknown" || h.sync_status === "pending").length
          const total = groupHosts.length
          return (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 flex flex-col">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-base font-semibold text-white">Sync Status — {group?.name}</h2>
                <Button
                  size="sm"
                  onClick={() => setActiveTab("sync")}
                >
                  Sync
                </Button>
              </div>
              {total > 0 ? (
                <div className="grid grid-cols-3 sm:grid-cols-5 gap-3">
                  <div>
                    <div className="text-xs text-slate-400">Hosts</div>
                    <div className="text-xl font-bold text-white">{total}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">In Sync</div>
                    <div className="text-xl font-bold text-green-400">{synced}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Out of Sync</div>
                    <div className="text-xl font-bold text-amber-400">{outOfSync}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Error</div>
                    <div className="text-xl font-bold text-red-400">{errored}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Unknown</div>
                    <div className="text-xl font-bold text-slate-400">{unknown}</div>
                  </div>
                </div>
              ) : (
                <p className="text-slate-400 text-sm">No hosts in this group yet.</p>
              )}
            </div>
          )
        })()}

        {/* GitOps */}
        {group && (
          <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 flex flex-col">
            <h2 className="text-base font-semibold text-white mb-3">GitOps</h2>
            {!group.gitops_enabled ? (
              <div className="flex items-center justify-between flex-1">
                <p className="text-slate-400 text-sm">
                  Manage rules from a Git repository.
                </p>
                <Dialog open={enableDialogOpen} onOpenChange={setEnableDialogOpen}>
                   <DialogTrigger render={<Button variant="outline" size="sm" />}>
                     Enable
                   </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Enable GitOps</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleEnableGitOps} className="space-y-4 mt-2">
                      <div className="space-y-2">
                        <Label htmlFor="git-repo">Git Repository</Label>
                        <select
                          id="git-repo"
                          value={selectedRepoId ?? ""}
                          onChange={(e) => setSelectedRepoId(e.target.value ? Number(e.target.value) : null)}
                          required
                          className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:bg-input/30"
                        >
                          <option value="">Select a repository…</option>
                          {gitRepos?.map((repo) => (
                            <option key={repo.id} value={repo.id}>{repo.name} ({repo.url})</option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="file-path">File Path</Label>
                        <Input
                          id="file-path"
                          type="text"
                          placeholder="groups/my-group.yaml"
                          value={filePath}
                          onChange={(e) => setFilePath(e.target.value)}
                          required
                        />
                      </div>
                      {enableGitopsMutation.error && (
                        <p className="text-sm text-red-400">{enableGitopsMutation.error.message}</p>
                      )}
                      <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setEnableDialogOpen(false)}>
                          Cancel
                        </Button>
                        <Button type="submit" disabled={enableGitopsMutation.isPending}>
                          {enableGitopsMutation.isPending ? "Enabling..." : "Enable"}
                        </Button>
                      </DialogFooter>
                    </form>
                  </DialogContent>
                </Dialog>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Status</div>
                    <GitOpsStatusBadge status={group.gitops_status!} />
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Repository</div>
                    <div className="text-sm text-slate-300 truncate">
                      {gitRepos?.find((r) => r.id === group.git_repository_id)?.name ?? "Unknown"}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-1">File Path</div>
                    <div className="text-sm text-slate-300 font-mono truncate">{group.gitops_file_path ?? "—"}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Last Import</div>
                    <div className="text-sm text-slate-300">{relativeTime(group.gitops_last_import_at)}</div>
                  </div>
                </div>
                {group.gitops_status === "error" && group.gitops_error_message && (
                  <div className="text-sm text-red-400 bg-red-950/30 border border-red-900/50 rounded-md px-3 py-2">
                    {group.gitops_error_message}
                  </div>
                )}
                <div className="pt-1">
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={handleDisableGitOps}
                    disabled={disableGitopsMutation.isPending}
                  >
                    {disableGitopsMutation.isPending ? "Disabling..." : "Disable GitOps"}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div role="tablist" className="flex gap-1 border-b border-slate-700 flex-wrap">
        {([
          ["overview", "Overview"],
          ["rules", "Rules"],
          ["services", "Services"],
          ["hosts-file", "Hosts File"],
          ["users", "Users"],
          ["cron-jobs", "Cron Jobs"],
          ["packages", "Packages"],
          ["dns", "DNS Resolver"],
          ["sync", "Sync"],
          ["workflow", "Updates"],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={activeTab === key}
            onClick={() => setActiveTab(key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === key
                ? "text-white border-b-2 border-white"
                : "text-slate-400 hover:text-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <>
          {/* Hosts section */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-white">Hosts</h2>
                <p className="text-slate-400 text-sm mt-1">
                  All hosts that may be affected by this group&apos;s rules.
                </p>
              </div>
              <Button size="sm" onClick={() => { setAddHostsOpen(true); setAddHostsSelected(new Set()); setAddHostsSearch("") }}>
                Add Hosts
              </Button>
            </div>

            {showHostsLoading && <TableSkeleton rows={3} columns={4} />}

            {!hostsLoading && groupHosts.length === 0 && (
              <div className="text-slate-400 py-4 text-center">
                No hosts in this group.
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
                        <TableCell className="font-medium">
                          <Link href={`/hosts/${host.id}`} className="text-white hover:text-blue-400 transition-colors">{host.hostname}</Link>
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

          {/* Add Hosts Dialog */}
          <Dialog open={addHostsOpen} onOpenChange={setAddHostsOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add Hosts to {group?.name}</DialogTitle>
              </DialogHeader>
              <div className="mt-2 space-y-3">
                <Input
                  placeholder="Search by hostname or IP..."
                  value={addHostsSearch}
                  onChange={(e) => setAddHostsSearch(e.target.value)}
                />

                {availableHosts.length === 0 ? (
                  <p className="text-slate-400 text-sm py-4 text-center">
                    {addHostsSearch ? "No matching hosts found." : "All hosts are already in this group."}
                  </p>
                ) : (
                  <div className="rounded-lg border border-slate-700 max-h-[360px] overflow-y-auto">
                    <Table>
                      <TableHeader>
                        <TableRow className="border-slate-700">
                          <TableHead className="w-10">
                            <input
                              type="checkbox"
                              checked={addHostsSelected.size === availableHosts.length && availableHosts.length > 0}
                              onChange={toggleAddAllHosts}
                              className="rounded border-slate-600"
                              aria-label="Select all hosts"
                            />
                          </TableHead>
                          <TableHead>Hostname</TableHead>
                          <TableHead>IP Address</TableHead>
                          <TableHead>Firewall</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {availableHosts.map((host) => (
                          <TableRow
                            key={host.id}
                            className="border-slate-700 cursor-pointer hover:bg-slate-800"
                            onClick={() => toggleAddHost(host.id)}
                          >
                            <TableCell>
                              <input
                                type="checkbox"
                                checked={addHostsSelected.has(host.id)}
                                onChange={(e) => e.stopPropagation()}
                                className="rounded border-slate-600"
                                aria-label={`Select ${host.hostname}`}
                              />
                            </TableCell>
                            <TableCell className="font-medium text-white">{host.hostname}</TableCell>
                            <TableCell className="font-mono text-slate-300 text-xs">{host.ip_address}</TableCell>
                            <TableCell><FirewallBadge backend={host.firewall_backend} /></TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}

                <div className="flex items-center justify-between pt-2">
                  <span className="text-slate-400 text-sm">
                    {addHostsSelected.size} host{addHostsSelected.size !== 1 ? "s" : ""} selected
                  </span>
                  <Button
                    disabled={addHostsSelected.size === 0 || addHostsMutation.isPending}
                    onClick={() => addHostsMutation.mutate({ host_ids: Array.from(addHostsSelected) })}
                  >
                    {addHostsMutation.isPending ? "Adding..." : "Add to Group"}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </>
      )}

      {activeTab === "rules" && <GroupRulesPage embedded />}
      {activeTab === "services" && <GroupServicesPage embedded />}
      {activeTab === "hosts-file" && <GroupHostsEntriesPage embedded />}
      {activeTab === "users" && <GroupUsersPage embedded />}
      {activeTab === "cron-jobs" && <GroupCronJobsPage embedded />}
      {activeTab === "packages" && <GroupPackagesPage embedded />}
      {activeTab === "dns" && <GroupResolverPage embedded />}
      {activeTab === "sync" && <GroupSyncPage embedded />}
      {activeTab === "workflow" && <WorkflowConfigPage embedded />}

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Disable"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}

    </div>
  )
}
