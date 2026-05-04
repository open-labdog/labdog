"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/data-table"
import type {
  ActionPack,
  ActionPackSyncResponse,
  GitRepository,
  PackRole,
  PackSourceType,
} from "@/lib/types"

interface PackFormState {
  name: string
  source_type: PackSourceType
  git_repository_id: number | null
  path: string
  local_path: string
  role: PackRole
  enabled: boolean
}

const emptyForm: PackFormState = {
  name: "",
  source_type: "git",
  git_repository_id: null,
  path: "",
  local_path: "",
  role: "override",
  enabled: true,
}

function statusChip(pack: ActionPack) {
  if (pack.last_sync_status === "ok") {
    return <span className="text-green-400 text-xs">OK</span>
  }
  if (pack.last_sync_status === "failed") {
    return <span className="text-red-400 text-xs">Failed</span>
  }
  return <span className="text-slate-500 text-xs">Never</span>
}

function formatDate(iso: string | null) {
  if (!iso) return "—"
  return new Date(iso).toLocaleString()
}

export default function ActionPacksPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ActionPack | null>(null)
  const [form, setForm] = useState<PackFormState>(emptyForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const queryClient = useQueryClient()

  const { data: packs, isLoading, error } = useQuery<ActionPack[]>({
    queryKey: ["action-packs"],
    queryFn: () => apiFetch<ActionPack[]>("/api/action-packs"),
  })
  const showLoading = useDelayedLoading(isLoading)

  // GitRepositories power the "pick a repo" dropdown. Fetched on mount
  // and whenever the dialog opens so newly-added repos show up without
  // a hard refresh.
  const { data: gitRepos } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })

  const deleteMutation = useApiMutation<unknown, number, ActionPack>({
    mutationFn: (packId) =>
      apiFetch(`/api/action-packs/${packId}`, { method: "DELETE" }),
    invalidateKeys: [["action-packs"], ["actions"]],
    successMessage: "Action pack deleted",
    optimisticUpdate: {
      queryKey: ["action-packs"],
      updater: (old, packId) => old.filter((p) => p.id !== packId),
    },
  })

  function openCreate() {
    setEditing(null)
    setForm(emptyForm)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEdit(pack: ActionPack) {
    setEditing(pack)
    setForm({
      name: pack.name,
      source_type: pack.source_type,
      git_repository_id: pack.git_repository_id,
      path: pack.path ?? "",
      local_path: pack.local_path ?? "",
      role: pack.role,
      enabled: pack.enabled,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  function buildPayload(): Record<string, unknown> {
    const p: Record<string, unknown> = {
      name: form.name,
      source_type: form.source_type,
      role: form.role,
      enabled: form.enabled,
    }
    if (form.source_type === "git") {
      p.git_repository_id = form.git_repository_id
      p.path = form.path ?? ""
    } else {
      p.local_path = form.local_path
    }
    return p
  }

  async function handleSave() {
    setFormSaving(true)
    setFormError(null)
    try {
      if (editing) {
        await apiFetch(`/api/action-packs/${editing.id}`, {
          method: "PUT",
          json: buildPayload(),
        })
        showSuccess("Action pack updated")
      } else {
        await apiFetch("/api/action-packs", {
          method: "POST",
          json: buildPayload(),
        })
        showSuccess("Action pack created")
      }
      await queryClient.invalidateQueries({ queryKey: ["action-packs"] })
      await queryClient.invalidateQueries({ queryKey: ["actions"] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save")
    } finally {
      setFormSaving(false)
    }
  }

  function handleDelete(pack: ActionPack) {
    setConfirmState({
      open: true,
      title: "Delete Action Pack",
      description: `Delete "${pack.name}"? The pack's checkout will be removed and any actions it provided will disappear from the registry. The linked Git repository (if any) is not affected.`,
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(pack.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  async function handleSync(pack: ActionPack) {
    setSyncingId(pack.id)
    try {
      const result = await apiFetch<ActionPackSyncResponse>(
        `/api/action-packs/${pack.id}/sync`,
        { method: "POST" }
      )
      if (result.success) {
        showSuccess(
          result.current_sha
            ? `Synced @ ${result.current_sha.slice(0, 8)}`
            : "Sync successful"
        )
      } else {
        showError(`Sync failed: ${result.message}`)
      }
      await queryClient.invalidateQueries({ queryKey: ["action-packs"] })
      await queryClient.invalidateQueries({ queryKey: ["actions"] })
    } catch (err) {
      showError(err instanceof Error ? err.message : "Sync failed")
    } finally {
      setSyncingId(null)
    }
  }

  const hasGitRepos = (gitRepos?.length ?? 0) > 0

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Action Packs" }]} />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Action Packs</h1>
          <p className="text-slate-400 text-sm mt-1">
            Collections of playbooks that supply actions to every host and
            group. Git packs reference a repository configured under{" "}
            <Link href="/git-repos" className="underline hover:text-slate-200">
              Git Repos
            </Link>
            ; local packs point at a directory on the LabDog host.
          </p>
        </div>
        <Button onClick={openCreate}>Add Pack</Button>
      </div>

      {showLoading && <TableSkeleton rows={3} columns={6} />}

      {error && (
        <div className="text-red-400 py-8 text-center">
          Failed to load action packs
        </div>
      )}

      {!isLoading && !error && (
        <DataTable<ActionPack>
          tableId="action-packs"
          data={packs}
          emptyMessage={
            <>No action packs configured. Click <strong>Add Pack</strong> to get started.</>
          }
          getRowKey={(p) => p.id}
          columns={[
            {
              key: "name",
              label: "Name",
              accessor: (p) => p.name,
              cell: (p) => <span className="font-medium text-white">{p.name}</span>,
              defaultWidth: 180,
              filter: { type: "text" },
            },
            {
              key: "source",
              label: "Source",
              accessor: (p) =>
                p.source_type === "local"
                  ? p.local_path ?? ""
                  : p.git_repository_name ?? "",
              cell: (p) => (
                <div className="flex flex-col">
                  {p.source_type === "local" ? (
                    <>
                      <span className="font-mono text-slate-300 text-sm truncate">
                        {p.local_path}
                      </span>
                      <span className="text-xs text-slate-500">local directory</span>
                    </>
                  ) : (
                    <>
                      <span className="text-slate-300 text-sm truncate">
                        {p.git_repository_name ?? "(missing)"}
                        {p.path ? (
                          <span className="font-mono text-slate-500"> / {p.path}</span>
                        ) : null}
                      </span>
                      <span className="text-xs text-slate-500">git repo</span>
                    </>
                  )}
                </div>
              ),
              defaultWidth: 320,
              filter: { type: "text" },
            },
            {
              key: "role",
              label: "Role",
              accessor: (p) => p.role,
              cell: (p) =>
                p.source_type === "local" ? (
                  <span className="text-slate-300 text-sm">local</span>
                ) : (
                  <span className="text-slate-300 text-sm capitalize">{p.role}</span>
                ),
              defaultWidth: 110,
            },
            {
              key: "enabled",
              label: "Enabled",
              accessor: (p) => p.enabled,
              cell: (p) =>
                p.enabled ? (
                  <span className="text-green-400 text-sm">Yes</span>
                ) : (
                  <span className="text-yellow-400 text-sm">No</span>
                ),
              defaultWidth: 100,
            },
            {
              key: "status",
              label: "Last Sync",
              accessor: (p) => p.last_synced_at ?? "",
              cell: (p) => (
                <div className="flex flex-col gap-0.5">
                  {statusChip(p)}
                  <span className="text-xs text-slate-500">
                    {formatDate(p.last_synced_at)}
                  </span>
                  {p.current_sha && (
                    <span className="text-xs text-slate-600 font-mono">
                      {p.current_sha.slice(0, 8)}
                    </span>
                  )}
                </div>
              ),
              defaultWidth: 180,
            },
            {
              key: "actions",
              label: "Actions",
              cell: (pack) => (
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={syncingId === pack.id || !pack.enabled}
                    onClick={() => handleSync(pack)}
                  >
                    {syncingId === pack.id ? "Syncing..." : "Sync"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => openEdit(pack)}>
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-red-400 hover:text-red-300 hover:bg-red-950"
                    onClick={() => handleDelete(pack)}
                    disabled={deleteMutation.isPending}
                  >
                    Delete
                  </Button>
                </div>
              ),
              defaultWidth: 240,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDialogOpen(false)
            setFormError(null)
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {editing ? "Edit Action Pack" : "Add Action Pack"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="pack-name">Name</Label>
              <Input
                id="pack-name"
                placeholder="labdog-default"
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              />
            </div>

            <div className="space-y-2">
              <Label>Source</Label>
              <div className="flex gap-4 text-sm">
                {(["git", "local"] as const).map((st) => (
                  <label key={st} className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="radio"
                      name="source_type"
                      checked={form.source_type === st}
                      onChange={() => setForm((p) => ({ ...p, source_type: st }))}
                    />
                    <span>
                      {st === "git" && "Git repository"}
                      {st === "local" && "Local directory"}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {form.source_type === "git" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="pack-repo">Git repository</Label>
                  {!hasGitRepos ? (
                    <p className="text-sm text-yellow-400">
                      No git repositories configured yet. Add one under{" "}
                      <Link
                        href="/git-repos"
                        className="underline hover:text-yellow-200"
                      >
                        Git Repos
                      </Link>{" "}
                      first.
                    </p>
                  ) : (
                    <select
                      id="pack-repo"
                      value={form.git_repository_id ?? ""}
                      onChange={(e) =>
                        setForm((p) => ({
                          ...p,
                          git_repository_id: e.target.value
                            ? Number(e.target.value)
                            : null,
                        }))
                      }
                      className="w-full rounded border border-input bg-background px-3 py-2 text-sm"
                    >
                      <option value="">— Select a repository —</option>
                      {gitRepos!.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name} ({r.url} @ {r.branch})
                        </option>
                      ))}
                    </select>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pack-path">Path inside the repo</Label>
                  <Input
                    id="pack-path"
                    placeholder="(leave empty if the pack is at the repo root)"
                    value={form.path}
                    onChange={(e) => setForm((p) => ({ ...p, path: e.target.value }))}
                    className="font-mono"
                  />
                  <p className="text-xs text-slate-400">
                    LabDog looks for <code>actions/*.manifest.yml</code> under
                    this subpath.
                  </p>
                </div>
              </>
            )}

            {form.source_type === "local" && (
              <div className="space-y-2">
                <Label htmlFor="pack-local-path">Filesystem path</Label>
                <Input
                  id="pack-local-path"
                  placeholder="/var/lib/labdog/my-pack"
                  value={form.local_path}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, local_path: e.target.value }))
                  }
                  className="font-mono"
                />
                <p className="text-xs text-slate-400">
                  Absolute path on the LabDog host. Nothing is cloned; the
                  directory is read in place. Useful for BYO playbooks you
                  maintain outside a git workflow.
                </p>
              </div>
            )}

            {form.source_type === "git" && (
              <div className="space-y-2">
                <Label>Role</Label>
                <div className="flex gap-4 text-sm">
                  {(["default", "override"] as const).map((r) => (
                    <label
                      key={r}
                      className="flex items-center gap-1 cursor-pointer"
                    >
                      <input
                        type="radio"
                        name="role"
                        checked={form.role === r}
                        onChange={() => setForm((p) => ({ ...p, role: r }))}
                      />
                      <span>
                        {r === "default" && "Default (canonical baseline)"}
                        {r === "override" && "Override (customises the default)"}
                      </span>
                    </label>
                  ))}
                </div>
                <p className="text-xs text-slate-400">
                  Overrides win over defaults on action-key collisions. Pick
                  Default for your main source-of-truth pack, Override for
                  layered customisations.
                </p>
              </div>
            )}

            <div className="flex items-center gap-2">
              <input
                id="pack-enabled"
                type="checkbox"
                checked={form.enabled}
                onChange={(e) =>
                  setForm((p) => ({ ...p, enabled: e.target.checked }))
                }
                className="rounded border-input"
              />
              <Label htmlFor="pack-enabled">Enabled</Label>
            </div>

            {formError && <p className="text-sm text-red-400">{formError}</p>}

            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setDialogOpen(false)
                  setFormError(null)
                }}
              >
                Cancel
              </Button>
              <Button onClick={handleSave} disabled={formSaving}>
                {formSaving
                  ? "Saving..."
                  : editing
                    ? "Save Changes"
                    : "Add Pack"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Delete"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </div>
  )
}
