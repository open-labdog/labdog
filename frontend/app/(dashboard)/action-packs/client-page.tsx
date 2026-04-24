"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
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
  ActionPackTestResponse,
  PackAuthType,
  PackRole,
  PackSourceType,
} from "@/lib/types"

interface PackFormState {
  name: string
  source_type: PackSourceType
  repo_url: string
  ref: string
  role: PackRole
  enabled: boolean
  auth_type: PackAuthType
  ssh_private_key: string
  ssh_known_hosts: string
  token: string
}

const emptyForm: PackFormState = {
  name: "",
  source_type: "git",
  repo_url: "",
  ref: "main",
  role: "override",
  enabled: true,
  auth_type: "none",
  ssh_private_key: "",
  ssh_known_hosts: "",
  token: "",
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
  const [testingModal, setTestingModal] = useState(false)
  const [modalTestResult, setModalTestResult] = useState<string | null>(null)
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
    setModalTestResult(null)
    setDialogOpen(true)
  }

  function openEdit(pack: ActionPack) {
    setEditing(pack)
    setForm({
      name: pack.name,
      source_type: pack.source_type,
      repo_url: pack.repo_url,
      ref: pack.ref,
      role: pack.role,
      enabled: pack.enabled,
      auth_type: pack.auth_type,
      ssh_private_key: "",
      ssh_known_hosts: pack.ssh_known_hosts ?? "",
      token: "",
    })
    setFormError(null)
    setModalTestResult(null)
    setDialogOpen(true)
  }

  function buildPayload(): Record<string, unknown> {
    const isLocal = form.source_type === "local"
    const p: Record<string, unknown> = {
      name: form.name,
      source_type: form.source_type,
      repo_url: form.repo_url,
      // Backend ignores ref for local packs but still validates the
      // string — keep the default rather than risk an empty value.
      ref: isLocal ? "main" : form.ref,
      role: isLocal ? "override" : form.role,
      enabled: form.enabled,
      auth_type: isLocal ? "none" : form.auth_type,
    }
    if (!isLocal && form.auth_type === "ssh") {
      if (form.ssh_private_key) p.ssh_private_key = form.ssh_private_key
      p.ssh_known_hosts = form.ssh_known_hosts
    }
    if (!isLocal && form.auth_type === "https_token") {
      if (form.token) p.token = form.token
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

  async function handleTestInModal() {
    setTestingModal(true)
    setModalTestResult(null)
    try {
      const payload = buildPayload() as Record<string, unknown>
      // /test takes the same shape minus priority/enabled/name — but
      // the backend tolerates those via extra="forbid"? No, it forbids.
      // Strip down:
      const testPayload: Record<string, unknown> = {
        source_type: payload.source_type,
        repo_url: payload.repo_url,
        ref: payload.ref,
        auth_type: payload.auth_type,
      }
      if (payload.ssh_private_key) testPayload.ssh_private_key = payload.ssh_private_key
      if (payload.ssh_known_hosts) testPayload.ssh_known_hosts = payload.ssh_known_hosts
      if (payload.token) testPayload.token = payload.token

      const result = await apiFetch<ActionPackTestResponse>(
        "/api/action-packs/test",
        { method: "POST", json: testPayload }
      )
      if (result.success) {
        setModalTestResult(
          `✓ ${result.message}${result.commit_sha ? ` (${result.commit_sha.slice(0, 8)})` : ""}`
        )
      } else {
        setModalTestResult(`✗ ${result.message}`)
      }
    } catch (err) {
      setModalTestResult(`✗ ${err instanceof Error ? err.message : "Test failed"}`)
    } finally {
      setTestingModal(false)
    }
  }

  function handleDelete(pack: ActionPack) {
    setConfirmState({
      open: true,
      title: "Delete Action Pack",
      description: `Delete "${pack.name}"? Its checkout will be removed and any actions it provided will disappear from the registry.`,
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

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Action Packs" }]} />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Action Packs</h1>
          <p className="text-slate-400 text-sm mt-1">
            Git-backed bundles of playbooks and manifests. Added actions show up
            in the registry for every host and group.
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
          emptyMessage={<>No action packs configured. Click <strong>Add Pack</strong> to get started.</>}
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
              key: "repo_url",
              label: "Source",
              accessor: (p) => p.repo_url,
              cell: (p) => (
                <div className="flex flex-col">
                  <span className="font-mono text-slate-300 text-sm truncate">
                    {p.repo_url}
                  </span>
                  <span className="text-xs text-slate-500">
                    {p.source_type === "local" ? "local directory" : "git remote"}
                  </span>
                </div>
              ),
              defaultWidth: 320,
              filter: { type: "text" },
            },
            {
              key: "ref",
              label: "Ref",
              accessor: (p) => p.ref,
              cell: (p) =>
                p.source_type === "local" ? (
                  <span className="text-slate-500 text-sm">—</span>
                ) : (
                  <span className="font-mono text-slate-300 text-sm">{p.ref}</span>
                ),
              defaultWidth: 120,
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
              cell: (p) => (p.enabled ? <span className="text-green-400 text-sm">Yes</span> : <span className="text-yellow-400 text-sm">No</span>),
              defaultWidth: 100,
            },
            {
              key: "status",
              label: "Last Sync",
              accessor: (p) => p.last_synced_at ?? "",
              cell: (p) => (
                <div className="flex flex-col gap-0.5">
                  {statusChip(p)}
                  <span className="text-xs text-slate-500">{formatDate(p.last_synced_at)}</span>
                  {p.current_sha && (
                    <span className="text-xs text-slate-600 font-mono">{p.current_sha.slice(0, 8)}</span>
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
            setModalTestResult(null)
          }
        }}
      >
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Action Pack" : "Add Action Pack"}</DialogTitle>
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
                      {st === "git" && "Git remote"}
                      {st === "local" && "Local directory"}
                    </span>
                  </label>
                ))}
              </div>
              {form.source_type === "local" && (
                <p className="text-xs text-slate-400">
                  LabDog reads manifests from the path in place — nothing is
                  cloned. Useful for BYO playbooks you maintain outside git.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="pack-repo">
                {form.source_type === "local" ? "Filesystem path" : "Repo URL"}
              </Label>
              <Input
                id="pack-repo"
                placeholder={
                  form.source_type === "local"
                    ? "/var/lib/labdog/my-pack"
                    : "git@github.com:me/labdog-playbooks.git"
                }
                value={form.repo_url}
                onChange={(e) => setForm((p) => ({ ...p, repo_url: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              {form.source_type === "git" && (
                <div className="space-y-2">
                  <Label htmlFor="pack-ref">Ref (branch or tag)</Label>
                  <Input
                    id="pack-ref"
                    placeholder="main"
                    value={form.ref}
                    onChange={(e) => setForm((p) => ({ ...p, ref: e.target.value }))}
                    className="font-mono"
                  />
                </div>
              )}
              <div className="flex items-center gap-2 pt-6">
                <input
                  id="pack-enabled"
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm((p) => ({ ...p, enabled: e.target.checked }))}
                  className="rounded border-input"
                />
                <Label htmlFor="pack-enabled">Enabled</Label>
              </div>
            </div>

            {form.source_type === "git" && (
              <div className="space-y-2">
                <Label>Role</Label>
                <div className="flex gap-4 text-sm">
                  {(["default", "override"] as const).map((r) => (
                    <label key={r} className="flex items-center gap-1 cursor-pointer">
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

            {form.source_type === "git" && (
              <div className="space-y-2">
                <Label>Authentication</Label>
                <div className="flex gap-4 text-sm">
                  {(["none", "ssh", "https_token"] as const).map((at) => (
                    <label key={at} className="flex items-center gap-1 cursor-pointer">
                      <input
                        type="radio"
                        name="auth_type"
                        checked={form.auth_type === at}
                        onChange={() => setForm((p) => ({ ...p, auth_type: at }))}
                      />
                      <span>
                        {at === "none" && "None (public repo)"}
                        {at === "ssh" && "SSH deploy key"}
                        {at === "https_token" && "HTTPS token (PAT)"}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {form.source_type === "git" && form.auth_type === "ssh" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="pack-ssh-key">
                    SSH private key
                    {editing && editing.has_ssh_key && " (leave blank to keep current)"}
                  </Label>
                  <Textarea
                    id="pack-ssh-key"
                    placeholder={
                      editing && editing.has_ssh_key
                        ? "Already set — paste new key to replace"
                        : "-----BEGIN OPENSSH PRIVATE KEY-----"
                    }
                    value={form.ssh_private_key}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, ssh_private_key: e.target.value }))
                    }
                    rows={4}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="pack-known-hosts">
                    Known hosts (required)
                  </Label>
                  <p className="text-xs text-slate-400">
                    Paste the server's host keys — one line per key.
                    Look up canonical values from your provider's docs. LabDog
                    does NOT fall back to TOFU.
                  </p>
                  <Textarea
                    id="pack-known-hosts"
                    placeholder="github.com ssh-ed25519 AAAA..."
                    value={form.ssh_known_hosts}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, ssh_known_hosts: e.target.value }))
                    }
                    rows={3}
                    className="font-mono text-xs"
                  />
                </div>
              </>
            )}

            {form.source_type === "git" && form.auth_type === "https_token" && (
              <div className="space-y-2">
                <Label htmlFor="pack-token">
                  Personal access token
                  {editing && editing.has_token && " (leave blank to keep current)"}
                </Label>
                <Input
                  id="pack-token"
                  type="password"
                  placeholder={
                    editing && editing.has_token
                      ? "Leave blank to keep current"
                      : "ghp_…"
                  }
                  value={form.token}
                  onChange={(e) => setForm((p) => ({ ...p, token: e.target.value }))}
                  className="font-mono"
                />
              </div>
            )}

            {modalTestResult && (
              <p
                className={`text-sm ${
                  modalTestResult.startsWith("✓") ? "text-green-400" : "text-red-400"
                }`}
              >
                {modalTestResult}
              </p>
            )}
            {formError && <p className="text-sm text-red-400">{formError}</p>}

            <DialogFooter>
              <Button
                variant="outline"
                onClick={handleTestInModal}
                disabled={testingModal || !form.repo_url}
              >
                {testingModal ? "Testing..." : "Test"}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setDialogOpen(false)
                  setFormError(null)
                  setModalTestResult(null)
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
