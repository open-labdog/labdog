"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ChevronDown, ChevronRight, Lock } from "lucide-react"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { DataTable } from "@/components/ui/data-table"
import type {
  ActionDefinition,
  ActionPack,
  ActionPackSyncResponse,
  ClaimAllKeysResponse,
  ContestedActionKey,
  GitRepository,
  PackSourceType,
} from "@/lib/types"

interface PackFormState {
  name: string
  source_type: PackSourceType
  git_repository_id: number | null
  path: string
  local_path: string
  enabled: boolean
}

const emptyForm: PackFormState = {
  name: "",
  source_type: "git",
  git_repository_id: null,
  path: "",
  local_path: "",
  enabled: true,
}

// Synthetic row for the always-present bundled pack. The bundled pack
// has no ``ActionPack`` DB row but it IS a candidate for every key it
// contributes — surfacing it in the Pack Sources table makes that
// reality discoverable.
interface BundledPackRow {
  id: number
  name: string
  isBundled: true
}
type PackRow = ActionPack | BundledPackRow

function isBundledRow(p: PackRow): p is BundledPackRow {
  return "isBundled" in p && p.isBundled === true
}

const BUNDLED_PACK_ROW: BundledPackRow = {
  id: -1,
  name: "bundled",
  isBundled: true,
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

function packLabel(pack: { pack_id: number | null; pack_name: string }): string {
  if (pack.pack_id === null) return `${pack.pack_name} (bundled)`
  return pack.pack_name
}

export default function ActionPacksPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ActionPack | null>(null)
  const [form, setForm] = useState<PackFormState | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [claimDialog, setClaimDialog] = useState<{
    pack: ActionPack
    pinnedHere: number
    pinnedElsewhere: number
    uncontested: number
    contested: number
  } | null>(null)
  const [claiming, setClaiming] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const queryClient = useQueryClient()

  const {
    data: packs,
    isLoading,
    error,
  } = useQuery<ActionPack[]>({
    queryKey: ["action-packs"],
    queryFn: () => apiFetch<ActionPack[]>("/api/action-packs"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const { data: gitRepos } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })

  const { data: contested } = useQuery<ContestedActionKey[]>({
    queryKey: ["action-resolutions"],
    queryFn: () => apiFetch<ContestedActionKey[]>("/api/action-resolutions"),
  })

  const { data: catalogActions, isLoading: catalogLoading } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 30_000,
  })

  const orderedPacks = useMemo(() => {
    return [...(packs ?? [])].sort((a, b) => a.name.localeCompare(b.name))
  }, [packs])

  const contestedByKey = useMemo(() => {
    const m: Record<string, ContestedActionKey> = {}
    for (const c of contested ?? []) m[c.action_key] = c
    return m
  }, [contested])

  const upsertResolution = useApiMutation<
    unknown,
    { action_key: string; pack_id: number | null }
  >({
    mutationFn: ({ action_key, pack_id }) =>
      apiFetch(`/api/action-resolutions/${encodeURIComponent(action_key)}`, {
        method: "PUT",
        json: { pack_id },
      }),
    invalidateKeys: [["action-resolutions"], ["actions-catalog"], ["action-packs"]],
  })

  const deleteMutation = useApiMutation<unknown, number, ActionPack>({
    mutationFn: (packId) =>
      apiFetch(`/api/action-packs/${packId}`, { method: "DELETE" }),
    invalidateKeys: [["action-packs"], ["actions-catalog"], ["action-resolutions"]],
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
      enabled: pack.enabled,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  function buildPayload(state: PackFormState): Record<string, unknown> {
    const p: Record<string, unknown> = {
      name: state.name,
      source_type: state.source_type,
      enabled: state.enabled,
    }
    if (state.source_type === "git") {
      p.git_repository_id = state.git_repository_id
      p.path = state.path ?? ""
    } else {
      p.local_path = state.local_path
    }
    return p
  }

  async function handleSave() {
    if (!form) return
    setFormSaving(true)
    setFormError(null)
    try {
      if (editing) {
        await apiFetch(`/api/action-packs/${editing.id}`, {
          method: "PUT",
          json: buildPayload(form),
        })
        showSuccess("Action pack updated")
      } else {
        await apiFetch("/api/action-packs", {
          method: "POST",
          json: buildPayload(form),
        })
        showSuccess("Action pack created")
      }
      await queryClient.invalidateQueries({ queryKey: ["action-packs"] })
      await queryClient.invalidateQueries({ queryKey: ["actions-catalog"] })
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
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
      description: `Delete "${pack.name}"? The pack's checkout will be removed, any actions it provided will disappear from the registry, and any keys pinned to this pack become unresolved (action unrunnable until you pick a new winner). The linked Git repository (if any) is not affected.`,
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
        { method: "POST" },
      )
      if (result.success) {
        showSuccess(
          result.current_sha
            ? `Synced @ ${result.current_sha.slice(0, 8)}`
            : "Sync successful",
        )
      } else {
        showError(`Sync failed: ${result.message}`)
      }
      await queryClient.invalidateQueries({ queryKey: ["action-packs"] })
      await queryClient.invalidateQueries({ queryKey: ["actions-catalog"] })
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
    } catch (err) {
      showError(err instanceof Error ? err.message : "Sync failed")
    } finally {
      setSyncingId(null)
    }
  }

  function openClaimDialog(pack: ActionPack) {
    // Pre-compute the diff for the confirmation dialog.
    const keysThisPackContributes = (catalogActions ?? []).filter((a) =>
      a.pack_name === pack.name ||
      a.overridden_from.includes(pack.name),
    )
    let pinnedHere = 0
    let pinnedElsewhere = 0
    let uncontested = 0
    let contested = 0
    for (const action of keysThisPackContributes) {
      const c = contestedByKey[action.key]
      if (!c) {
        // Uncontested — this pack is the sole contributor and the
        // claim is a no-op for the resolver (but still pins
        // explicitly for future contestants).
        uncontested += 1
        continue
      }
      contested += 1
      if (c.resolution?.pack_id === pack.id) {
        pinnedHere += 1
      } else if (c.resolution !== null) {
        pinnedElsewhere += 1
      }
    }
    setClaimDialog({ pack, pinnedHere, pinnedElsewhere, uncontested, contested })
  }

  async function handleClaim() {
    if (!claimDialog) return
    setClaiming(true)
    try {
      const result = await apiFetch<ClaimAllKeysResponse>(
        `/api/action-packs/${claimDialog.pack.id}/claim-all-keys`,
        { method: "POST" },
      )
      showSuccess(
        `Pinned ${claimDialog.pack.name}: ${result.created} new, ${result.updated} updated, ${result.skipped} unchanged.`,
      )
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
      await queryClient.invalidateQueries({ queryKey: ["actions-catalog"] })
      setClaimDialog(null)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Claim failed")
    } finally {
      setClaiming(false)
    }
  }

  const hasGitRepos = (gitRepos?.length ?? 0) > 0

  // Build the action registry view. One row per action key.
  // Built-in pseudo-actions (_builtin.*) are management-page noise —
  // they're never pack-supplied and never contested. Skip them here.
  const registryRows = useMemo(() => {
    const rows = (catalogActions ?? [])
      .filter((a) => !a.key.startsWith("_builtin."))
      .map((a) => {
        const contestedRow = contestedByKey[a.key] ?? null
        return { action: a, contested: contestedRow }
      })
    rows.sort((x, y) => x.action.key.localeCompare(y.action.key))
    return rows
  }, [catalogActions, contestedByKey])

  const unresolvedRows = registryRows.filter((r) => r.action.unresolved)

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Action Packs" }]} />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Action Packs</h1>
          <p className="text-slate-400 text-sm mt-1">
            Each action key has at most one source pack. When multiple packs
            declare the same key, pick a winner per key below. There is no
            global pack ordering — pack rows are unranked. To bulk-add
            several packs from one repo, use the scan wizard from the{" "}
            <Link href="/git-repos" className="underline hover:text-slate-200">
              Git Repos
            </Link>{" "}
            page.
          </p>
        </div>
        <Button onClick={openCreate}>Add Pack</Button>
      </div>

      {unresolvedRows.length > 0 && (
        <div className="flex items-start gap-3 p-3 rounded-lg bg-amber-950/40 border border-amber-800">
          <AlertTriangle className="h-5 w-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="text-amber-200 font-medium">
              {unresolvedRows.length} action{" "}
              {unresolvedRows.length === 1 ? "key needs" : "keys need"} a
              decision
            </p>
            <p className="text-amber-300/80 mt-0.5">
              Pick a winning pack below for each unresolved key. Until then
              these actions are blocked.
            </p>
            <p className="mt-2 text-amber-200/90 font-mono text-xs">
              {unresolvedRows.map((r) => r.action.key).join(", ")}
            </p>
          </div>
        </div>
      )}

      {/* ---- Action Registry — the primary surface ---- */}
      <section className="rounded-lg border border-slate-700 bg-slate-900">
        <div className="px-4 py-3 border-b border-slate-700">
          <h2 className="text-sm font-semibold text-white">Action Registry</h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Every action key the live registry knows about, and which pack
            owns it. Uncontested keys win automatically; contested keys
            require a per-key pin.
          </p>
        </div>

        {catalogLoading && (
          <p className="text-slate-400 text-sm px-4 py-6 text-center">
            Loading…
          </p>
        )}

        {!catalogLoading && registryRows.length === 0 && (
          <p className="text-slate-400 text-sm px-4 py-6 text-center">
            No actions in the registry yet. Add and sync an action pack to
            populate this list.
          </p>
        )}

        {!catalogLoading && registryRows.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead className="text-slate-400 text-xs font-medium w-8" />
                <TableHead className="text-slate-400 text-xs font-medium">
                  Action Key
                </TableHead>
                <TableHead className="text-slate-400 text-xs font-medium">
                  Winner
                </TableHead>
                <TableHead className="text-slate-400 text-xs font-medium">
                  Status
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {registryRows.flatMap((row) => {
                const { action, contested } = row
                const isContested = contested !== null
                const isUnresolved = action.unresolved
                const isExpanded = expandedRow === action.key
                const rowClass = isUnresolved
                  ? "border-slate-700 bg-amber-950/20"
                  : "border-slate-700"

                const main = (
                  <TableRow key={action.key} className={rowClass}>
                    <TableCell className="align-top">
                      {isContested ? (
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedRow(isExpanded ? null : action.key)
                          }
                          className="text-slate-400 hover:text-slate-200"
                          aria-label={isExpanded ? "Collapse" : "Expand"}
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                      ) : null}
                    </TableCell>
                    <TableCell className="align-top">
                      <span className="font-mono text-slate-300 text-xs">
                        {action.key}
                      </span>
                    </TableCell>
                    <TableCell className="align-top">
                      {isUnresolved ? (
                        <span className="text-amber-300 text-xs italic">
                          (no winner pinned)
                        </span>
                      ) : (
                        <span className="text-slate-200 text-xs">
                          {action.pack_name}
                          {action.winning_pack_id === null &&
                          action.pack_name === "bundled" ? (
                            <span className="ml-1 text-[10px] text-slate-500">
                              (bundled)
                            </span>
                          ) : null}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      {isUnresolved ? (
                        <span className="text-amber-300 text-xs font-medium">
                          Pick winner
                        </span>
                      ) : isContested && contested?.is_frozen ? (
                        <span className="text-amber-300 text-xs">Frozen</span>
                      ) : isContested ? (
                        <span className="text-slate-500 text-xs">Pinned</span>
                      ) : (
                        <span className="text-emerald-400 text-xs">OK</span>
                      )}
                    </TableCell>
                  </TableRow>
                )

                if (!isContested || !isExpanded || contested === null) {
                  return [main]
                }

                // Inline radio group for contested rows.
                const expansion = (
                  <TableRow
                    key={`${action.key}-detail`}
                    className="border-slate-700 bg-slate-950/40"
                  >
                    <TableCell />
                    <TableCell colSpan={3} className="py-3">
                      <div className="space-y-1.5">
                        {contested.candidates.map((c) => {
                          const checked =
                            contested.resolution?.pack_id === c.pack_id
                          return (
                            <label
                              key={`${action.key}-${c.pack_id ?? "bundled"}`}
                              className="flex items-center gap-2 text-sm cursor-pointer rounded px-2 py-1 hover:bg-slate-800"
                            >
                              <input
                                type="radio"
                                name={`winner-${action.key}`}
                                checked={checked}
                                disabled={upsertResolution.isPending}
                                onChange={() =>
                                  upsertResolution.mutate({
                                    action_key: action.key,
                                    pack_id: c.pack_id,
                                  })
                                }
                              />
                              <span className="text-slate-200 flex-1">
                                {packLabel(c)}
                              </span>
                            </label>
                          )
                        })}
                      </div>
                    </TableCell>
                  </TableRow>
                )

                return [main, expansion]
              })}
            </TableBody>
          </Table>
        )}
      </section>

      {/* ---- Pack Sources — management-only ---- */}
      <section>
        <h2 className="text-sm font-semibold text-white">Pack Sources</h2>
        <p className="text-xs text-slate-400 mt-0.5 mb-3">
          Where the packs come from. Add, sync, edit, or delete here. Use
          &ldquo;Make winner for all keys&rdquo; to pin every key a pack
          contributes to that pack in one click.
        </p>

        {showLoading && <TableSkeleton rows={3} columns={5} />}

        {error && (
          <div className="text-red-400 py-8 text-center">
            Failed to load action packs
          </div>
        )}

        {!isLoading && !error && (
          <DataTable<PackRow>
            tableId="action-packs"
            data={[BUNDLED_PACK_ROW, ...orderedPacks]}
            emptyMessage={
              <>
                No action packs configured. Click <strong>Add Pack</strong>{" "}
                to add one.
              </>
            }
            getRowKey={(p) => p.id}
            columns={[
              {
                key: "name",
                label: "Name",
                cell: (p) => {
                  const bundled = isBundledRow(p)
                  return (
                    <div className="flex items-center gap-2">
                      {bundled ? <Lock className="h-3 w-3 text-slate-500" /> : null}
                      <span className="font-medium text-white">{p.name}</span>
                      {bundled ? (
                        <span className="ml-1 text-[10px] rounded border border-slate-700 bg-slate-800 px-1 py-0.5 text-slate-400">
                          built-in
                        </span>
                      ) : null}
                    </div>
                  )
                },
                defaultWidth: 200,
                sortable: false,
              },
              {
                key: "source",
                label: "Source",
                cell: (p) => {
                  if (isBundledRow(p)) {
                    return (
                      <span className="text-xs text-slate-500">
                        baked into the container image
                      </span>
                    )
                  }
                  if (p.source_type === "local") {
                    return (
                      <div className="flex flex-col">
                        <span className="font-mono text-slate-300 text-sm truncate">
                          {p.local_path}
                        </span>
                        <span className="text-xs text-slate-500">
                          local directory
                        </span>
                      </div>
                    )
                  }
                  return (
                    <div className="flex flex-col">
                      <span className="text-slate-300 text-sm truncate">
                        {p.git_repository_name ?? "(missing)"}
                        {p.path ? (
                          <span className="font-mono text-slate-500">
                            {" "}
                            / {p.path}
                          </span>
                        ) : null}
                      </span>
                      <span className="text-xs text-slate-500">git repo</span>
                    </div>
                  )
                },
                defaultWidth: 320,
                sortable: false,
              },
              {
                key: "enabled",
                label: "Enabled",
                cell: (p) => {
                  if (isBundledRow(p)) {
                    return <span className="text-slate-500 text-sm">—</span>
                  }
                  return p.enabled ? (
                    <span className="text-green-400 text-sm">Yes</span>
                  ) : (
                    <span className="text-yellow-400 text-sm">No</span>
                  )
                },
                defaultWidth: 100,
                sortable: false,
              },
              {
                key: "status",
                label: "Last Sync",
                cell: (p) => {
                  if (isBundledRow(p)) {
                    return (
                      <span className="text-xs text-slate-500">at build</span>
                    )
                  }
                  return (
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
                  )
                },
                defaultWidth: 180,
                sortable: false,
              },
              {
                key: "actions",
                label: "Actions",
                cell: (pack) => {
                  if (isBundledRow(pack)) {
                    return (
                      <span className="text-xs text-slate-600">
                        immutable
                      </span>
                    )
                  }
                  return (
                    <div className="flex gap-1 flex-wrap">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={syncingId === pack.id || !pack.enabled}
                        onClick={() => handleSync(pack)}
                      >
                        {syncingId === pack.id ? "Syncing..." : "Sync"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => openClaimDialog(pack)}
                        title="Pin every key this pack contributes to this pack"
                      >
                        Make winner for all keys
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => openEdit(pack)}
                      >
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
                  )
                },
                defaultWidth: 360,
                resizable: false,
                sortable: false,
              },
            ]}
          />
        )}
      </section>

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
          {form && (
            <div className="space-y-4 mt-2">
              <div className="space-y-2">
                <Label htmlFor="pack-name">Name</Label>
                <Input
                  id="pack-name"
                  placeholder="labdog-default"
                  value={form.name}
                  onChange={(e) =>
                    setForm((p) => (p ? { ...p, name: e.target.value } : p))
                  }
                />
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
                          setForm((p) =>
                            p
                              ? {
                                  ...p,
                                  git_repository_id: e.target.value
                                    ? Number(e.target.value)
                                    : null,
                                }
                              : p,
                          )
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
                      onChange={(e) =>
                        setForm((p) => (p ? { ...p, path: e.target.value } : p))
                      }
                      className="font-mono"
                    />
                    <p className="text-xs text-slate-400">
                      LabDog looks for <code>actions/*.manifest.yml</code>{" "}
                      under this subpath.
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
                      setForm((p) =>
                        p ? { ...p, local_path: e.target.value } : p,
                      )
                    }
                    className="font-mono"
                  />
                  <p className="text-xs text-slate-400">
                    Absolute path on the LabDog host. Nothing is cloned; the
                    directory is read in place.
                  </p>
                </div>
              )}

              <div className="flex items-center gap-2">
                <input
                  id="pack-enabled"
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) =>
                    setForm((p) =>
                      p ? { ...p, enabled: e.target.checked } : p,
                    )
                  }
                  className="rounded border-input"
                />
                <Label htmlFor="pack-enabled">Enabled</Label>
              </div>

              {formError && (
                <p className="text-sm text-red-400">{formError}</p>
              )}

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
          )}
        </DialogContent>
      </Dialog>

      {claimDialog && (
        <Dialog
          open
          onOpenChange={(o) => !o && setClaimDialog(null)}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>
                Make {claimDialog.pack.name} winner for all its keys
              </DialogTitle>
            </DialogHeader>
            <div className="text-sm text-slate-300 space-y-2">
              <p>This pack contributes to <strong>{claimDialog.contested + claimDialog.uncontested}</strong> action key{claimDialog.contested + claimDialog.uncontested === 1 ? "" : "s"}.</p>
              <ul className="ml-4 list-disc text-slate-400 text-xs space-y-0.5">
                <li>{claimDialog.uncontested} uncontested (no-op for the resolver, but pinned explicitly so future contestants don&apos;t auto-claim them)</li>
                <li>{claimDialog.contested} contested:
                  {" "}
                  <span className="text-emerald-300">{claimDialog.pinnedHere} already pinned here</span>,
                  {" "}
                  <span className="text-amber-300">{claimDialog.pinnedElsewhere} pinned elsewhere (will be overwritten)</span>,
                  {" "}
                  <span className="text-slate-300">
                    {claimDialog.contested - claimDialog.pinnedHere - claimDialog.pinnedElsewhere} unpinned (will become pinned here)
                  </span>
                </li>
              </ul>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setClaimDialog(null)} disabled={claiming}>
                Cancel
              </Button>
              <Button onClick={handleClaim} disabled={claiming}>
                {claiming ? "Pinning…" : "Pin all keys"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

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
