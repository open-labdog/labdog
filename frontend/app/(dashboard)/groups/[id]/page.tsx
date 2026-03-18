"use client"

import { useState } from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { HostGroup, Host, GitRepository } from "@/lib/types"
import { SyncStatusBadge, FirewallBadge, GitOpsStatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
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
import { cn } from "@/lib/utils"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export default function GroupDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()
  const [enableDialogOpen, setEnableDialogOpen] = useState(false)
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(null)
  const [filePath, setFilePath] = useState("")
  const [gitopsLoading, setGitopsLoading] = useState(false)
  const [gitopsError, setGitopsError] = useState<string | null>(null)

  const { data: groups, isLoading: groupsLoading } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  const { data: hosts, isLoading: hostsLoading } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })

  const { data: gitRepos } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })

  const group = groups?.find((g) => g.id === id)

  // Filter hosts that belong to this group
  // The API doesn't expose group membership directly on Host, so we show all hosts
  // and note that group membership is managed server-side
  const groupHosts = hosts ?? []

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

  async function handleEnableGitOps(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedRepoId) return
    setGitopsError(null)
    setGitopsLoading(true)
    try {
      await apiFetch(`/api/groups/${id}/gitops/enable`, {
        method: "POST",
        body: JSON.stringify({ git_repository_id: selectedRepoId, file_path: filePath }),
      })
      await queryClient.invalidateQueries({ queryKey: ["groups"] })
      setEnableDialogOpen(false)
      setSelectedRepoId(null)
      setFilePath("")
    } catch (err) {
      setGitopsError(err instanceof Error ? err.message : "Failed to enable GitOps")
    } finally {
      setGitopsLoading(false)
    }
  }

  async function handleDisableGitOps() {
    if (!confirm("Are you sure you want to disable GitOps for this group? Rules will remain but will no longer sync from Git.")) return
    setGitopsLoading(true)
    try {
      await apiFetch(`/api/groups/${id}/gitops/disable`, { method: "POST" })
      await queryClient.invalidateQueries({ queryKey: ["groups"] })
    } catch {
      alert("Failed to disable GitOps")
    } finally {
      setGitopsLoading(false)
    }
  }

  if (groupsLoading) {
    return <div className="text-slate-400 py-8 text-center">Loading group…</div>
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/groups"
              className="text-slate-400 hover:text-white text-sm transition-colors"
            >
              Groups
            </Link>
            <span className="text-slate-600">/</span>
            <span className="text-white text-sm">{group?.name}</span>
          </div>
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
        <div className="flex gap-3">
          <Link
            href={`/groups/${id}/rules`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Manage Rules
          </Link>
          <Link
            href={`/groups/${id}/services`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Manage Services
          </Link>
          <Link
            href={`/groups/${id}/hosts-entries`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Manage Hosts File
          </Link>
          <Link
            href={`/groups/${id}/users`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Manage Users
          </Link>
          <Link
            href={`/groups/${id}/cron-jobs`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Manage Cron Jobs
          </Link>
          <Link
            href={`/groups/${id}/sync`}
            className={cn(buttonVariants())}
          >
            Sync
          </Link>
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
                    <div className="text-xs text-slate-500 font-mono truncate" title={group.gitops_file_path}>
                      {group.gitops_file_path}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Quick actions */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h2 className="text-base font-semibold text-white mb-3">Quick Actions</h2>
        <div className="flex gap-3 flex-wrap">
          <Link
            href={`/groups/${id}/rules`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            View &amp; Edit Rules
          </Link>
          <Link
            href={`/groups/${id}/services`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            View &amp; Edit Services
          </Link>
          <Link
            href={`/groups/${id}/hosts-entries`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            View &amp; Edit Hosts File
          </Link>
          <Link
            href={`/groups/${id}/users`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            View &amp; Edit Users
          </Link>
          <Link
            href={`/groups/${id}/cron-jobs`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            View &amp; Edit Cron Jobs
          </Link>
          <Link
            href={`/groups/${id}/sync`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            Preview &amp; Apply Sync
          </Link>
        </div>
      </div>

      {/* GitOps Settings */}
      {group && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <h2 className="text-base font-semibold text-white mb-3">GitOps</h2>
          {!group.gitops_enabled ? (
            <div className="flex items-center justify-between">
              <p className="text-slate-400 text-sm">
                Enable GitOps to manage this group&apos;s rules from a Git repository.
              </p>
              <Dialog open={enableDialogOpen} onOpenChange={setEnableDialogOpen}>
                <DialogTrigger>
                  <Button variant="outline" size="sm">Enable GitOps</Button>
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
                    {gitopsError && (
                      <p className="text-sm text-red-400">{gitopsError}</p>
                    )}
                    <div className="flex gap-3 pt-2">
                      <Button type="submit" disabled={gitopsLoading}>
                        {gitopsLoading ? "Enabling..." : "Enable"}
                      </Button>
                      <Button type="button" variant="outline" onClick={() => setEnableDialogOpen(false)}>
                        Cancel
                      </Button>
                    </div>
                  </form>
                </DialogContent>
              </Dialog>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <div className="text-xs text-slate-500 mb-1">Status</div>
                  <GitOpsStatusBadge status={group.gitops_status!} />
                </div>
                <div>
                  <div className="text-xs text-slate-500 mb-1">Repository</div>
                  <div className="text-sm text-slate-300">
                    {gitRepos?.find((r) => r.id === group.git_repository_id)?.name ?? "Unknown"}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-slate-500 mb-1">File Path</div>
                  <div className="text-sm text-slate-300 font-mono">{group.gitops_file_path ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500 mb-1">Last Import</div>
                  <div className="text-sm text-slate-300">{relativeTime(group.gitops_last_import_at)}</div>
                </div>
              </div>
              {group.gitops_status === "error" && group.gitops_error_message && (
                <div className="text-sm text-red-400 bg-red-950/30 border border-red-900/50 rounded-md px-3 py-2">
                  {group.gitops_error_message}
                </div>
              )}
              <p className="text-xs text-slate-500">
                Push changes to your Git repository or configure a webhook to trigger automatic imports.
              </p>
              <div className="pt-1">
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={handleDisableGitOps}
                  disabled={gitopsLoading}
                >
                  {gitopsLoading ? "Disabling..." : "Disable GitOps"}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Hosts section */}
      <div>
        <h2 className="text-lg font-semibold text-white mb-3">Hosts</h2>
        <p className="text-slate-400 text-sm mb-4">
          All hosts that may be affected by this group&apos;s rules.
        </p>

        {hostsLoading && (
          <div className="text-slate-400 py-4 text-center">Loading hosts…</div>
        )}

        {!hostsLoading && groupHosts.length === 0 && (
          <div className="text-slate-400 py-4 text-center">
            No hosts configured.{" "}
            <Link href="/hosts/new" className="underline hover:text-white">
              Add a host
            </Link>
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
                    <TableCell className="font-medium text-white">
                      {host.hostname}
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
    </div>
  )
}
