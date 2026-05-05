"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { GripVertical, Info, AlertTriangle } from "lucide-react"
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
import { TableRow } from "@/components/ui/table"
import { DataTable } from "@/components/ui/data-table"
import { ConflictResolutionDialog } from "@/components/action-packs/conflict-resolution-dialog"
import type {
  ActionPack,
  ActionPackSyncResponse,
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

function SortableRow({
  pack,
  children,
}: {
  pack: ActionPack
  children: React.ReactNode
}) {
  const { attributes, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: pack.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    position: "relative" as const,
    zIndex: isDragging ? 10 : undefined,
    background: isDragging ? "rgba(59, 130, 246, 0.08)" : undefined,
    outline: isDragging ? "1px solid rgba(59, 130, 246, 0.3)" : undefined,
    borderRadius: isDragging ? "6px" : undefined,
  }

  return (
    <TableRow
      ref={setNodeRef}
      style={style}
      className="border-slate-700"
      {...attributes}
    >
      {children}
    </TableRow>
  )
}

function DragHandleCell({ pack }: { pack: ActionPack }) {
  const { attributes, listeners } = useSortable({ id: pack.id })
  return (
    <button
      {...attributes}
      {...listeners}
      className="cursor-grab active:cursor-grabbing text-slate-500 hover:text-slate-300 p-0.5 rounded transition-colors"
      aria-label="Drag to reorder"
    >
      <GripVertical className="h-4 w-4" />
    </button>
  )
}

export default function ActionPacksPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ActionPack | null>(null)
  const [form, setForm] = useState<PackFormState | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [conflictOpen, setConflictOpen] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const queryClient = useQueryClient()
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

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

  // Top-to-bottom display order: highest position first.
  const orderedPacks = useMemo(() => {
    return [...(packs ?? [])].sort((a, b) => b.position - a.position)
  }, [packs])
  const sortableIds = useMemo(() => orderedPacks.map((p) => p.id), [orderedPacks])

  const reorderMutation = useApiMutation<unknown, number[]>({
    mutationFn: (packIds) =>
      apiFetch("/api/action-packs/reorder", {
        method: "POST",
        json: { pack_ids: packIds },
      }),
    invalidateKeys: [["action-packs"], ["actions"], ["action-resolutions"]],
  })

  const deleteMutation = useApiMutation<unknown, number, ActionPack>({
    mutationFn: (packId) =>
      apiFetch(`/api/action-packs/${packId}`, { method: "DELETE" }),
    invalidateKeys: [["action-packs"], ["actions"], ["action-resolutions"]],
    successMessage: "Action pack deleted",
    optimisticUpdate: {
      queryKey: ["action-packs"],
      updater: (old, packId) => old.filter((p) => p.id !== packId),
    },
  })

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = orderedPacks.findIndex((p) => p.id === active.id)
    const newIndex = orderedPacks.findIndex((p) => p.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    const next = arrayMove(orderedPacks, oldIndex, newIndex)
    reorderMutation.mutate(next.map((p) => p.id))
  }

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
      await queryClient.invalidateQueries({ queryKey: ["actions"] })
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
      await queryClient.invalidateQueries({ queryKey: ["actions"] })
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
    } catch (err) {
      showError(err instanceof Error ? err.message : "Sync failed")
    } finally {
      setSyncingId(null)
    }
  }

  const hasGitRepos = (gitRepos?.length ?? 0) > 0
  const frozenCount = (contested ?? []).filter((c) => c.is_frozen).length
  const contestedCount = contested?.length ?? 0

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Action Packs" }]} />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Action Packs</h1>
          <p className="text-slate-400 text-sm mt-1">
            Collections of playbooks that supply actions to every host and
            group. To bulk-add several packs from the same repo, use the scan
            wizard from the{" "}
            <Link href="/git-repos" className="underline hover:text-slate-200">
              Git Repos
            </Link>{" "}
            page.
          </p>
        </div>
        <Button onClick={openCreate}>Add Pack</Button>
      </div>

      <div className="flex items-start gap-3 p-3 rounded-lg bg-slate-900 border border-slate-700">
        <Info className="h-5 w-5 text-slate-400 flex-shrink-0 mt-0.5" />
        <div className="text-sm text-slate-300">
          <strong>Pack precedence:</strong> drag rows to reorder. The pack at
          the <em>top</em> wins on action-key collisions. The bundled pack
          ships with LabDog and is implicit at the bottom — it loses to any
          listed pack contributing the same key.
        </div>
      </div>

      {contestedCount > 0 && (
        <button
          type="button"
          onClick={() => setConflictOpen(true)}
          className="flex w-full items-start gap-3 p-3 rounded-lg bg-amber-950/40 border border-amber-800 hover:bg-amber-950/60 text-left"
        >
          <AlertTriangle className="h-5 w-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-amber-200 font-medium text-sm">
              {frozenCount > 0
                ? `${frozenCount} action ${frozenCount === 1 ? "key needs" : "keys need"} your decision`
                : `${contestedCount} contested action ${contestedCount === 1 ? "key" : "keys"}`}
            </p>
            <p className="text-amber-300/80 text-xs mt-0.5">
              {frozenCount > 0
                ? "LabDog is using the previous winner until you confirm. Click to review."
                : "Multiple packs contribute the same action keys. Click to review the assignment."}
            </p>
          </div>
        </button>
      )}

      {showLoading && <TableSkeleton rows={3} columns={6} />}

      {error && (
        <div className="text-red-400 py-8 text-center">
          Failed to load action packs
        </div>
      )}

      {reorderMutation.error && (
        <div className="text-red-400 text-sm">
          {reorderMutation.error.message}
        </div>
      )}

      {!isLoading && !error && (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={sortableIds}
            strategy={verticalListSortingStrategy}
          >
            <DataTable<ActionPack>
              tableId="action-packs"
              data={orderedPacks}
              emptyMessage={
                <>
                  No action packs configured. Click <strong>Add Pack</strong>{" "}
                  to get started.
                </>
              }
              getRowKey={(p) => p.id}
              renderRow={(pack, _idx, defaultCells) => (
                <SortableRow key={pack.id} pack={pack}>
                  {defaultCells}
                </SortableRow>
              )}
              columns={[
                {
                  key: "drag",
                  label: "",
                  cell: (pack) => <DragHandleCell pack={pack} />,
                  defaultWidth: 40,
                  resizable: false,
                  sortable: false,
                },
                {
                  key: "name",
                  label: "Name",
                  accessor: (p) => p.name,
                  cell: (p) => (
                    <span className="font-medium text-white">{p.name}</span>
                  ),
                  defaultWidth: 180,
                  sortable: false,
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
                          <span className="text-xs text-slate-500">
                            local directory
                          </span>
                        </>
                      ) : (
                        <>
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
                        </>
                      )}
                    </div>
                  ),
                  defaultWidth: 320,
                  sortable: false,
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
                  sortable: false,
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
                  sortable: false,
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
                  ),
                  defaultWidth: 240,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          </SortableContext>
        </DndContext>
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

      <ConflictResolutionDialog
        open={conflictOpen}
        onClose={() => setConflictOpen(false)}
      />

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
