"use client"

import { useState, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { SearchIcon, XIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiFetch } from "@/lib/api"
import { showError } from "@/lib/toast"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
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

export default function GitReposPage() {
  const queryClient = useQueryClient()
  const [searchQuery, setSearchQuery] = useState("")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRepo, setEditingRepo] = useState<GitRepository | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [showWebhooks, setShowWebhooks] = useState(false)
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)

  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [branch, setBranch] = useState("main")
  const [authType, setAuthType] = useState<"ssh_key" | "https_token">("ssh_key")
  const [sshKeyId, setSshKeyId] = useState<number | null>(null)
  const [httpsToken, setHttpsToken] = useState("")
  const [webhookSecret, setWebhookSecret] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)
  const [deletingId, setDeletingId] = useState<number | null>(null)

  const { data: repos, isLoading, error } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const filteredRepos = repos?.filter(r => {
    const q = searchQuery.toLowerCase()
    return r.name.toLowerCase().includes(q) || r.url.toLowerCase().includes(q)
  }) ?? []

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  function resetForm() {
    setName("")
    setUrl("")
    setBranch("main")
    setAuthType("ssh_key")
    setSshKeyId(null)
    setHttpsToken("")
    setWebhookSecret("")
    setFormError(null)
    setEditingRepo(null)
  }

  function openCreateDialog() {
    resetForm()
    setShowWebhooks(false)
    setDialogOpen(true)
  }

  function openEditDialog(repo: GitRepository) {
    setEditingRepo(repo)
    setName(repo.name)
    setUrl(repo.url)
    setBranch(repo.branch)
    setAuthType(repo.auth_type)
    setSshKeyId(repo.ssh_key_id)
    setHttpsToken("")
    setWebhookSecret(repo.webhook_secret || "")
    setFormError(null)
    setShowWebhooks(false)
    setDialogOpen(true)
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFormError(null)
    setFormLoading(true)

    try {
      if (editingRepo) {
        const body: GitRepoUpdate = {
          name,
          url,
          branch,
          auth_type: authType,
          ssh_key_id: authType === "ssh_key" ? sshKeyId : null,
          webhook_secret: webhookSecret || null,
        }
        if (authType === "https_token" && httpsToken) {
          body.https_token = httpsToken
        }
        await apiFetch(`/api/git-repos/${editingRepo.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        })
      } else {
        const body: GitRepoCreate = {
          name,
          url,
          branch,
          auth_type: authType,
          ssh_key_id: authType === "ssh_key" ? sshKeyId : null,
          webhook_secret: webhookSecret || null,
        }
        if (authType === "https_token" && httpsToken) {
          body.https_token = httpsToken
        }
        await apiFetch("/api/git-repos", {
          method: "POST",
          body: JSON.stringify(body),
        })
        setShowWebhooks(true)
      }
      await queryClient.invalidateQueries({ queryKey: ["git-repos"] })
      if (!showWebhooks || editingRepo) {
        setDialogOpen(false)
        resetForm()
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save repository")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDelete(id: number) {
    setDeletingId(id)
    try {
      await apiFetch(`/api/git-repos/${id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["git-repos"] })
    } catch {
      showError("Failed to delete repository")
    } finally {
      setDeletingId(null)
      setDeleteConfirmId(null)
    }
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
          if (!open) { resetForm(); setShowWebhooks(false) }
        }}>
          <DialogTrigger>
            <Button onClick={openCreateDialog}>Add Repository</Button>
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
                  <Button onClick={() => { setDialogOpen(false); resetForm(); setShowWebhooks(false) }}>
                    Done
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="repo-name">Name</Label>
                  <Input
                    id="repo-name"
                    type="text"
                    placeholder="e.g. infra-config"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-url">URL</Label>
                  <Input
                    id="repo-url"
                    type="text"
                    placeholder="git@github.com:org/repo.git"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-branch">Branch</Label>
                  <Input
                    id="repo-branch"
                    type="text"
                    placeholder="main"
                    value={branch}
                    onChange={(e) => setBranch(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="auth-type">Auth Type</Label>
                  <select
                    id="auth-type"
                    value={authType}
                    onChange={(e) => setAuthType(e.target.value as "ssh_key" | "https_token")}
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
                      value={sshKeyId ?? ""}
                      onChange={(e) => setSshKeyId(e.target.value ? Number(e.target.value) : null)}
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
                      value={httpsToken}
                      onChange={(e) => setHttpsToken(e.target.value)}
                      required={!editingRepo}
                    />
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="webhook-secret">Webhook Secret (optional)</Label>
                  <Input
                    id="webhook-secret"
                    type="text"
                    placeholder="Optional webhook secret"
                    value={webhookSecret}
                    onChange={(e) => setWebhookSecret(e.target.value)}
                  />
                </div>

                {formError && (
                  <p className="text-sm text-red-400">{formError}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={formLoading}>
                    {formLoading ? "Saving..." : editingRepo ? "Update Repository" : "Add Repository"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => { setDialogOpen(false); resetForm() }}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            )}
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            placeholder="Search by name or URL..."
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
            Showing {filteredRepos.length} of {repos?.length ?? 0} repos
          </span>
        )}
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load repositories</div>
      )}

      {!isLoading && !error && filteredRepos.length === 0 && searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No results matching &apos;{searchQuery}&apos;
        </div>
      )}

      {!isLoading && !error && repos?.length === 0 && !searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No git repositories yet. Add your first repository to get started.
        </div>
      )}

      {!isLoading && !error && filteredRepos.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Name</TableHead>
                <TableHead>URL</TableHead>
                <TableHead>Branch</TableHead>
                <TableHead>Auth Type</TableHead>
                <TableHead>Last Sync</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredRepos.map((repo) => (
                <TableRow key={repo.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">{repo.name}</TableCell>
                  <TableCell className="font-mono text-sm text-slate-300 max-w-[250px] truncate">
                    {repo.url}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="border-slate-600 text-slate-300">
                      {repo.branch}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge className={repo.auth_type === "ssh_key" ? "bg-blue-600 text-white" : "bg-amber-600 text-white"}>
                      {repo.auth_type === "ssh_key" ? "SSH Key" : "HTTPS"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-slate-400">
                    {relativeTime(repo.last_sync_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEditDialog(repo)}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => setDeleteConfirmId(repo.id)}
                        disabled={deletingId === repo.id}
                      >
                        {deletingId === repo.id ? "Deleting..." : "Delete"}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
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
          <div className="flex gap-3 pt-4">
            <Button
              variant="destructive"
              onClick={() => deleteConfirmId && handleDelete(deleteConfirmId)}
              disabled={deletingId !== null}
            >
              {deletingId !== null ? "Deleting..." : "Delete"}
            </Button>
            <Button
              variant="outline"
              onClick={() => setDeleteConfirmId(null)}
            >
              Cancel
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
