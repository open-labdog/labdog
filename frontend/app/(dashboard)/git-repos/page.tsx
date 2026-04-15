"use client"

import { useState, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
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
  DialogTrigger,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { gitRepoSchema, type GitRepoInput } from "@/lib/schemas"
import type { GitRepository, GitRepoCreate, GitRepoUpdate, SSHKey } from "@/lib/types"

function relativeTime(dateStr: string | null): string {
  if (!dateStr) return "Never"
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const seconds = Math.floor((now - then) / 1000)
  if (seconds < 60) return "just now"
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`
  const days = Math.floor(hours / 24)
  return `${days} day${days === 1 ? "" : "s"} ago`
}

const defaultFormValues: GitRepoInput = {
  name: "",
  url: "",
  branch: "main",
  auth_type: "ssh_key",
  ssh_key_id: "",
  https_token: "",
  webhook_secret: "",
}

export default function GitReposPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRepo, setEditingRepo] = useState<GitRepository | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [showWebhooks, setShowWebhooks] = useState(false)
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)

  const form = useForm<GitRepoInput>({
    resolver: zodResolver(gitRepoSchema),
    defaultValues: defaultFormValues,
    mode: "onSubmit",
  })

  const authType = form.watch("auth_type")

  const { data: repos, isLoading, error } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const saveMutation = useApiMutation({
    mutationFn: ({ editId, data }: { editId: number | null; data: GitRepoInput }) => {
      if (editId) {
        const body: GitRepoUpdate = {
          name: data.name,
          url: data.url,
          branch: data.branch,
          auth_type: data.auth_type,
          ssh_key_id: data.auth_type === "ssh_key" && data.ssh_key_id ? Number(data.ssh_key_id) : null,
          webhook_secret: data.webhook_secret || null,
        }
        if (data.auth_type === "https_token" && data.https_token) {
          body.https_token = data.https_token
        }
        return apiFetch(`/api/git-repos/${editId}`, { method: "PUT", body: JSON.stringify(body) })
      } else {
        const body: GitRepoCreate = {
          name: data.name,
          url: data.url,
          branch: data.branch,
          auth_type: data.auth_type,
          ssh_key_id: data.auth_type === "ssh_key" && data.ssh_key_id ? Number(data.ssh_key_id) : null,
          webhook_secret: data.webhook_secret || null,
        }
        if (data.auth_type === "https_token" && data.https_token) {
          body.https_token = data.https_token
        }
        return apiFetch("/api/git-repos", { method: "POST", body: JSON.stringify(body) })
      }
    },
    invalidateKeys: [["git-repos"]],
    onSuccess: (_data, variables) => {
      if (variables.editId) {
        setDialogOpen(false)
        form.reset(defaultFormValues)
        setEditingRepo(null)
      } else {
        setShowWebhooks(true)
      }
    },
  })

  const deleteMutation = useApiMutation({
    mutationFn: (id: number) =>
      apiFetch(`/api/git-repos/${id}`, { method: "DELETE" }),
    invalidateKeys: [["git-repos"]],
    onSuccess: () => setDeleteConfirmId(null),
  })

  function openCreateDialog() {
    form.reset(defaultFormValues)
    setEditingRepo(null)
    saveMutation.reset()
    setShowWebhooks(false)
    setDialogOpen(true)
  }

  function openEditDialog(repo: GitRepository) {
    setEditingRepo(repo)
    form.reset({
      name: repo.name,
      url: repo.url,
      branch: repo.branch,
      auth_type: repo.auth_type,
      ssh_key_id: repo.ssh_key_id ? String(repo.ssh_key_id) : "",
      https_token: "",
      webhook_secret: repo.webhook_secret || "",
    })
    saveMutation.reset()
    setShowWebhooks(false)
    setDialogOpen(true)
  }

  const onSubmit = form.handleSubmit((data) => {
    saveMutation.mutate({ editId: editingRepo?.id ?? null, data })
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
        <Dialog open={dialogOpen} onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) { form.reset(defaultFormValues); setEditingRepo(null); setShowWebhooks(false); saveMutation.reset() }
        }}>
          <DialogTrigger render={<Button />} onClick={openCreateDialog}>
            Add Repository
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{editingRepo ? "Edit Repository" : "Add Repository"}</DialogTitle>
            </DialogHeader>

            {showWebhooks && !editingRepo ? (
              <div className="space-y-4 mt-2">
                <p className="text-sm text-slate-400">
                  Repository created. Configure a webhook in your git provider using one of these URLs:
                </p>
                <div className="space-y-3">
                  {webhookUrls.map((wh) => (
                    <div key={wh.label} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 px-3 py-2">
                      <div>
                        <span className="text-xs text-slate-400">{wh.label}</span>
                        <p className="text-sm font-mono text-white break-all">{wh.url}</p>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => copyToClipboard(wh.url)}
                      >
                        {copiedUrl === wh.url ? "Copied!" : "Copy"}
                      </Button>
                    </div>
                  ))}
                </div>
                <div className="flex gap-3 pt-2">
                  <Button onClick={() => { setDialogOpen(false); form.reset(defaultFormValues); setEditingRepo(null); setShowWebhooks(false) }}>
                    Done
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={onSubmit} noValidate className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="repo-name">Name</Label>
                  <Input id="repo-name" type="text" placeholder="e.g. infra-config" {...form.register("name")} />
                  {form.formState.errors.name && <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-url">URL</Label>
                  <Input id="repo-url" type="text" placeholder="git@github.com:org/repo.git" {...form.register("url")} />
                  {form.formState.errors.url && <p className="text-sm text-red-400">{form.formState.errors.url.message}</p>}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-branch">Branch</Label>
                  <Input id="repo-branch" type="text" placeholder="main" {...form.register("branch")} />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="auth-type">Auth Type</Label>
                  <select
                    id="auth-type"
                    {...form.register("auth_type")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="ssh_key">SSH Key</option>
                    <option value="https_token">HTTPS Token</option>
                  </select>
                </div>

                {authType === "ssh_key" && (
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
                          {key.name}{key.is_default ? " (default)" : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {authType === "https_token" && (
                  <div className="space-y-2">
                    <Label htmlFor="https-token">HTTPS Token</Label>
                    <Input
                      id="https-token"
                      type="password"
                      placeholder={editingRepo ? "Leave blank to keep existing token" : "Personal access token"}
                      {...form.register("https_token")}
                    />
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
                    onClick={() => { setDialogOpen(false); form.reset(defaultFormValues); setEditingRepo(null) }}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={saveMutation.isPending}>
                    {saveMutation.isPending ? "Saving..." : editingRepo ? "Update Repository" : "Add Repository"}
                  </Button>
                </DialogFooter>
              </form>
            )}
          </DialogContent>
        </Dialog>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}
      {error && <div className="text-red-400 py-8 text-center">Failed to load repositories</div>}

      {!isLoading && !error && (
        <DataTable<GitRepository>
          tableId="git-repos"
          data={repos}
          emptyMessage="No git repositories yet. Add your first repository to get started."
          getRowKey={(r) => r.id}
          columns={[
            {
              key: "name",
              label: "Name",
              accessor: (r) => r.name,
              cell: (r) => <span className="font-medium text-white">{r.name}</span>,
              defaultWidth: 160,
              filter: { type: "text" },
            },
            {
              key: "url",
              label: "URL",
              accessor: (r) => r.url,
              cell: (r) => (
                <span className="font-mono text-sm text-slate-300 truncate block max-w-[250px]">{r.url}</span>
              ),
              defaultWidth: 280,
              filter: { type: "text", placeholder: "e.g. github.com" },
            },
            {
              key: "branch",
              label: "Branch",
              accessor: (r) => r.branch,
              cell: (r) => (
                <Badge variant="outline" className="border-slate-600 text-slate-300">{r.branch}</Badge>
              ),
              defaultWidth: 120,
              filter: { type: "text" },
            },
            {
              key: "auth_type",
              label: "Auth Type",
              accessor: (r) => r.auth_type,
              cell: (r) => (
                <Badge className={r.auth_type === "ssh_key" ? "bg-blue-600 text-white" : "bg-amber-600 text-white"}>
                  {r.auth_type === "ssh_key" ? "SSH Key" : "HTTPS"}
                </Badge>
              ),
              defaultWidth: 130,
              filter: { type: "enum", from: "accessor" },
            },
            {
              key: "last_sync",
              label: "Last Sync",
              accessor: (r) => r.last_sync_at ?? "",
              cell: (r) => <span className="text-slate-400">{relativeTime(r.last_sync_at)}</span>,
              defaultWidth: 160,
              filter: { type: "dateRange" },
            },
            {
              key: "actions",
              label: "Actions",
              cell: (r) => (
                <div className="flex gap-2">
                  <Button variant="ghost" size="sm" onClick={() => openEditDialog(r)}>Edit</Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => setDeleteConfirmId(r.id)}
                    disabled={deleteMutation.isPending}
                  >
                    {deleteMutation.isPending ? "Deleting..." : "Delete"}
                  </Button>
                </div>
              ),
              defaultWidth: 160,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

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
