"use client"

import { useState, useMemo } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { GitBranchIcon, CopyIcon, CheckIcon, LinkIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading, formatRelativeTime } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { gitRepoSchema, type GitRepoInput } from "@/lib/schemas"
import { detectAuthFromUrl } from "@/lib/git-repos"
import type { GitRepository, GitRepoUpdate, SSHKey, HostGroup } from "@/lib/types"

function syncHealth(repo: GitRepository): "healthy" | "stale" | "never" {
  if (!repo.last_sync_at) return "never"
  const age = Date.now() - new Date(repo.last_sync_at).getTime()
  return age > 24 * 60 * 60 * 1000 ? "stale" : "healthy"
}

const SYNC_BORDER: Record<string, string> = {
  healthy: "border-l-2 border-l-green-500/60",
  stale: "border-l-2 border-l-amber-500/60",
  never: "border-l-2 border-l-slate-600/60",
}

const defaultFormValues: GitRepoInput = {
  name: "",
  url: "",
  branch: "main",
  ssh_key_id: "",
  https_token: "",
  webhook_secret: "",
}

export default function GitReposPage() {
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingRepo, setEditingRepo] = useState<GitRepository | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [webhookRepoId, setWebhookRepoId] = useState<number | null>(null)
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)

  const form = useForm<GitRepoInput>({
    resolver: zodResolver(gitRepoSchema),
    defaultValues: defaultFormValues,
    mode: "onSubmit",
  })

  const url = form.watch("url")
  const detectedAuth = detectAuthFromUrl(url)

  const { data: repos, isLoading, error } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })
  const groupCountByRepo = useMemo(() => {
    const map = new Map<number, number>()
    groups?.forEach(g => {
      if (g.git_repository_id != null) map.set(g.git_repository_id, (map.get(g.git_repository_id) ?? 0) + 1)
    })
    return map
  }, [groups])

  const saveMutation = useApiMutation({
    mutationFn: ({ editId, data }: { editId: number; data: GitRepoInput }) => {
      const auth = detectAuthFromUrl(data.url)
      const sshKeyId = auth === "ssh_key" && data.ssh_key_id ? Number(data.ssh_key_id) : null
      const token = auth === "https" && data.https_token ? data.https_token : undefined
      const body: GitRepoUpdate = {
        name: data.name,
        url: data.url,
        branch: data.branch,
        ssh_key_id: sshKeyId,
        webhook_secret: data.webhook_secret || null,
      }
      if (token) body.https_token = token
      return apiFetch(`/api/git-repos/${editId}`, { method: "PUT", body: JSON.stringify(body) })
    },
    invalidateKeys: [["git-repos"]],
    onSuccess: () => {
      setEditDialogOpen(false)
      form.reset(defaultFormValues)
      setEditingRepo(null)
    },
  })

  const deleteMutation = useApiMutation({
    mutationFn: (id: number) =>
      apiFetch(`/api/git-repos/${id}`, { method: "DELETE" }),
    invalidateKeys: [["git-repos"]],
    onSuccess: () => setDeleteConfirmId(null),
  })

  function openEditDialog(repo: GitRepository) {
    setEditingRepo(repo)
    form.reset({
      name: repo.name,
      url: repo.url,
      branch: repo.branch,
      ssh_key_id: repo.ssh_key_id ? String(repo.ssh_key_id) : "",
      https_token: "",
      webhook_secret: repo.webhook_secret || "",
    })
    saveMutation.reset()
    setEditDialogOpen(true)
  }

  const onSubmit = form.handleSubmit((data) => {
    if (!editingRepo) return
    saveMutation.mutate({ editId: editingRepo.id, data })
  })

  function handleDelete(id: number) {
    deleteMutation.mutate(id)
  }

  function copyToClipboard(text: string) {
    navigator.clipboard.writeText(text)
    setCopiedUrl(text)
    setTimeout(() => setCopiedUrl(null), 2000)
  }

  const webhookUrls = useMemo(() => {
    const origin = typeof window !== "undefined" ? window.location.origin : ""
    return [
      { label: "GitHub", url: `${origin}/webhooks/github` },
      { label: "GitLab", url: `${origin}/webhooks/gitlab` },
      { label: "Gitea", url: `${origin}/webhooks/gitea` },
    ]
  }, [])

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Git Repos" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Git Repositories</h1>
          <p className="text-slate-400 text-sm mt-1">Manage git repositories for GitOps</p>
        </div>
        <Link href="/git-repos/new">
          <Button>Add Repository</Button>
        </Link>
      </div>

      <Dialog
        open={editDialogOpen}
        onOpenChange={(open) => {
          setEditDialogOpen(open)
          if (!open) {
            form.reset(defaultFormValues)
            setEditingRepo(null)
            saveMutation.reset()
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Repository</DialogTitle>
          </DialogHeader>
          <form onSubmit={onSubmit} noValidate className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="repo-name">Name</Label>
              <Input id="repo-name" type="text" placeholder="e.g. infra-config" {...form.register("name")} />
              {form.formState.errors.name && (
                <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="repo-url">URL</Label>
              <Input id="repo-url" type="text" placeholder="git@github.com:org/repo.git" {...form.register("url")} />
              {form.formState.errors.url && (
                <p className="text-sm text-red-400">{form.formState.errors.url.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="repo-branch">Branch</Label>
              <Input id="repo-branch" type="text" placeholder="main" {...form.register("branch")} />
            </div>

            {detectedAuth === "ssh_key" && (
              <div className="space-y-2">
                <Label htmlFor="ssh-key-select">SSH Key</Label>
                <select
                  id="ssh-key-select"
                  {...form.register("ssh_key_id")}
                  className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                >
                  <option value="">Select an SSH key...</option>
                  {sshKeys?.map((key) => (
                    <option key={key.id} value={key.id}>
                      {key.name}
                      {key.is_default ? " (default)" : ""}
                    </option>
                  ))}
                </select>
                {form.formState.errors.ssh_key_id && (
                  <p className="text-sm text-red-400">{form.formState.errors.ssh_key_id.message}</p>
                )}
                <p className="text-xs text-slate-500">SSH URL detected — pick the deploy key LabDog should use.</p>
              </div>
            )}

            {detectedAuth === "https" && (
              <div className="space-y-2">
                <Label htmlFor="https-token">Personal Access Token (optional)</Label>
                <Input
                  id="https-token"
                  type="password"
                  placeholder="Leave blank to keep existing token"
                  {...form.register("https_token")}
                />
                <p className="text-xs text-slate-500">
                  HTTPS URL detected — leave the token blank for public repos.
                </p>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="webhook-secret">Webhook Secret (optional)</Label>
              <Input id="webhook-secret" type="text" placeholder="Optional webhook secret" {...form.register("webhook_secret")} />
            </div>

            {saveMutation.error && (
              <p className="text-sm text-red-400">{saveMutation.error.message}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setEditDialogOpen(false)
                  form.reset(defaultFormValues)
                  setEditingRepo(null)
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving..." : "Update Repository"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {showLoading && <TableSkeleton rows={3} columns={5} />}
      {error && <div className="text-red-400 py-8 text-center">Failed to load repositories</div>}

      {!isLoading && !error && (
        <DataTable<GitRepository>
          tableId="git-repos-v2"
          data={repos}
          emptyMessage={
            <div className="flex flex-col items-center gap-3 py-4 mx-auto" style={{ maxWidth: "28rem" }}>
              <GitBranchIcon className="w-10 h-10 text-slate-700" />
              <div className="text-center">
                <p className="text-slate-300 font-medium">No repositories connected</p>
                <p className="text-slate-500 text-sm mt-1">
                  Link a git repository to manage group configuration declaratively via YAML.
                  LabDog imports changes automatically when a webhook fires or a manual sync is triggered.
                </p>
              </div>
              <Link href="/git-repos/new" className="mt-2">
                <Button>Add Repository</Button>
              </Link>
            </div>
          }
          getRowKey={(r) => r.id}
          rowClassName={(r) => SYNC_BORDER[syncHealth(r)]}
          columns={[
            {
              key: "name",
              label: "Name",
              accessor: (r) => r.name,
              cell: (r) => (
                <div>
                  <span className="text-sm font-medium text-white">{r.name}</span>
                  {r.last_commit_sha && (
                    <div className="font-mono text-[11px] text-slate-500 mt-0.5" title={r.last_commit_sha}>
                      {r.last_commit_sha.slice(0, 7)}
                    </div>
                  )}
                </div>
              ),
              defaultWidth: 180,
              filter: { type: "text" },
            },
            {
              key: "url",
              label: "URL",
              accessor: (r) => r.url,
              cell: (r) => (
                <span className="font-mono text-sm text-slate-300 truncate block max-w-[250px]" title={r.url}>{r.url}</span>
              ),
              defaultWidth: 260,
              filter: { type: "text", placeholder: "e.g. github.com" },
            },
            {
              key: "branch",
              label: "Branch",
              accessor: (r) => r.branch,
              cell: (r) => (
                <Badge variant="outline" className="border-slate-600 text-slate-300">{r.branch}</Badge>
              ),
              defaultWidth: 100,
            },
            {
              key: "auth_type",
              label: "Auth",
              accessor: (r) => r.auth_type,
              cell: (r) => {
                if (r.auth_type === "ssh_key") return <Badge className="bg-blue-600 text-white">SSH</Badge>
                if (r.auth_type === "https_token") return <Badge className="bg-amber-600 text-white">HTTPS</Badge>
                return <Badge className="bg-slate-600 text-white">Public</Badge>
              },
              defaultWidth: 90,
              filter: { type: "enum", options: [
                {label:"SSH Key",value:"ssh_key"},
                {label:"HTTPS Token",value:"https_token"},
                {label:"Public",value:"none"},
              ] },
            },
            {
              key: "groups",
              label: "Groups",
              accessor: (r) => groupCountByRepo.get(r.id) ?? 0,
              cell: (r) => {
                const count = groupCountByRepo.get(r.id) ?? 0
                return count > 0
                  ? <span className="text-sm tabular-nums text-slate-300">{count}</span>
                  : <span className="text-sm text-slate-600">0</span>
              },
              defaultWidth: 70,
            },
            {
              key: "last_sync",
              label: "Last Sync",
              accessor: (r) => r.last_sync_at ?? "",
              cell: (r) => (
                <span className="text-sm text-slate-300" title={r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : undefined}>
                  {r.last_sync_at ? formatRelativeTime(r.last_sync_at) : <span className="text-slate-600">Never</span>}
                </span>
              ),
              defaultWidth: 110,
            },
            {
              key: "actions",
              label: "",
              cell: (r) => (
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={() => setWebhookRepoId(r.id)} title="Webhook URLs">
                    <LinkIcon className="w-3.5 h-3.5" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => openEditDialog(r)}>Edit</Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setDeleteConfirmId(r.id)}
                    disabled={deleteMutation.isPending}
                    className="text-red-400 hover:text-red-300 hover:bg-red-950"
                  >
                    Delete
                  </Button>
                </div>
              ),
              defaultWidth: 180,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

      {/* Webhook URLs dialog */}
      <Dialog open={webhookRepoId !== null} onOpenChange={(open) => { if (!open) { setWebhookRepoId(null); setCopiedUrl(null) } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Webhook URLs</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-slate-400 mt-1">
            Configure your git provider to send push events to one of these URLs:
          </p>
          <div className="space-y-3 mt-3">
            {webhookUrls.map((wh) => (
              <div key={wh.label} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 px-3 py-2">
                <div className="min-w-0">
                  <span className="text-xs text-slate-400">{wh.label}</span>
                  <p className="text-sm font-mono text-white break-all">{wh.url}</p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="ml-2 shrink-0"
                  onClick={() => copyToClipboard(wh.url)}
                >
                  {copiedUrl === wh.url ? <CheckIcon className="w-3.5 h-3.5" /> : <CopyIcon className="w-3.5 h-3.5" />}
                </Button>
              </div>
            ))}
            {(() => {
              const repo = repos?.find(r => r.id === webhookRepoId)
              if (!repo?.webhook_secret) return null
              return (
                <div className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 px-3 py-2">
                  <div className="min-w-0">
                    <span className="text-xs text-slate-400">Webhook Secret</span>
                    <p className="text-sm font-mono text-white">{"•".repeat(16)}</p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="ml-2 shrink-0"
                    onClick={() => copyToClipboard(repo.webhook_secret!)}
                  >
                    {copiedUrl === repo.webhook_secret ? <CheckIcon className="w-3.5 h-3.5" /> : <CopyIcon className="w-3.5 h-3.5" />}
                  </Button>
                </div>
              )
            })()}
          </div>
          <DialogFooter className="mt-4">
            <Button onClick={() => { setWebhookRepoId(null); setCopiedUrl(null) }}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <Dialog open={deleteConfirmId !== null} onOpenChange={(open) => { if (!open) setDeleteConfirmId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Repository</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-slate-400 mt-2">
            Are you sure you want to delete this repository? This action cannot be undone.
            Any groups using this repository for GitOps will be disconnected.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirmId(null)}>Cancel</Button>
            <Button
              variant="destructive"
              onClick={() => deleteConfirmId && handleDelete(deleteConfirmId)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
