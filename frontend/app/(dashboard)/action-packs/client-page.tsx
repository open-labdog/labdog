"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { shortAgo } from "@/lib/fleet"
import { showSuccess, showError } from "@/lib/toast"
import { Banner, Confirm, Field, Modal, Panel, Table, Tag } from "@/components/ld"
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
  trusted: boolean
}

const emptyForm: PackFormState = {
  name: "",
  source_type: "git",
  git_repository_id: null,
  path: "",
  local_path: "",
  enabled: true,
  trusted: false,
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

function syncTag(pack: ActionPack) {
  if (pack.last_sync_status === "ok") return <Tag tone="ok">synced</Tag>
  if (pack.last_sync_status === "failed") return <Tag tone="danger">failed</Tag>
  return <Tag>never synced</Tag>
}

function packLabel(pack: { pack_id: number | null; pack_name: string }): string {
  if (pack.pack_id === null) return `${pack.pack_name} (bundled)`
  return pack.pack_name
}

/**
 * Action packs — the body of the Actions screen's Packs tab. The registry
 * (which pack owns each action key) is the primary surface; the sources
 * table underneath is where packs are added, synced and removed.
 */
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

  // How many action keys does each pack contribute (as either the
  // winner or an overridden contestant)? Used to disable the bulk-pin
  // button on packs that aren't a candidate for anything yet.
  const packKeyCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const a of catalogActions ?? []) {
      if (a.pack_name) m[a.pack_name] = (m[a.pack_name] ?? 0) + 1
      for (const overridden of a.overridden_from) {
        m[overridden] = (m[overridden] ?? 0) + 1
      }
    }
    return m
  }, [catalogActions])

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
      trusted: pack.trusted,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  function buildPayload(state: PackFormState): Record<string, unknown> {
    const p: Record<string, unknown> = {
      name: state.name,
      source_type: state.source_type,
      enabled: state.enabled,
      trusted: state.trusted,
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
      title: "Delete action pack",
      description: `${pack.name}'s checkout is removed, the actions it provided disappear from the registry, and keys pinned to it become unresolved — unrunnable until you pick a new winner. The linked Git repository, if any, is not affected.`,
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

  type RegistryRow = (typeof registryRows)[number]
  const setF = <K extends keyof PackFormState>(k: K, v: PackFormState[K]) => setForm((prev) => (prev ? { ...prev, [k]: v } : prev))
  const closeDialog = () => {
    setDialogOpen(false)
    setFormError(null)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="text-[11.5px] text-text-3">
        Each action key has at most one source pack. When several packs declare the same key, pick a winner per key — there is no global pack ordering. To bulk-add packs from one repository, use the scan wizard under{" "}
        <Link href="/git-repos" className="text-ld-accent">
          Settings › Git repositories
        </Link>
        .
      </div>

      {unresolvedRows.length > 0 && (
        <Banner tone="warn">
          <span className="font-medium">
            {unresolvedRows.length} action {unresolvedRows.length === 1 ? "key needs" : "keys need"} a decision
          </span>{" "}
          — pick a winning pack for each; until then these actions are blocked: <span className="mono">{unresolvedRows.map((r) => r.action.key).join(", ")}</span>
        </Banner>
      )}

      {/* ---- Action Registry — the primary surface ---- */}
      <Panel title="action registry" meta="every action key the live registry knows, and which pack owns it">
        <Table<RegistryRow>
          cols={[
            {
              k: "key",
              label: "action key",
              w: "minmax(200px,1.2fr)",
              sortable: false,
              cell: ({ action, contested }) => (
                <span className="flex items-center gap-1.5">
                  {contested !== null && <span className="tt text-[8px] text-text-faint">{expandedRow === action.key ? "▼" : "▶"}</span>}
                  <span className="mono text-[11.5px] text-text">{action.key}</span>
                </span>
              ),
            },
            {
              k: "winner",
              label: "winner",
              w: "minmax(140px,1fr)",
              sortable: false,
              cell: ({ action }) =>
                action.unresolved ? (
                  <span className="italic text-warn">no winner pinned</span>
                ) : (
                  <span className="text-text-2">
                    {action.pack_name}
                    {action.winning_pack_id === null && action.pack_name === "bundled" ? <span className="ml-1 text-[10px] text-text-faint">(bundled)</span> : null}
                  </span>
                ),
            },
            {
              k: "status",
              label: "status",
              w: "110px",
              sortable: false,
              cell: ({ action, contested }) =>
                action.unresolved ? (
                  <Tag tone="warn">pick winner</Tag>
                ) : contested?.is_frozen ? (
                  <Tag tone="hold">frozen</Tag>
                ) : contested ? (
                  <Tag>pinned</Tag>
                ) : (
                  <Tag tone="ok">ok</Tag>
                ),
            },
            {
              k: "go",
              label: "",
              w: "84px",
              right: true,
              sortable: false,
              cell: ({ action, contested }) => (contested ? <span className="tt text-ld-accent">{expandedRow === action.key ? "close" : "choose →"}</span> : null),
            },
          ]}
          rows={registryRows}
          keyOf={(r) => r.action.key}
          activeKey={expandedRow ?? undefined}
          onRowClick={(r) => r.contested && setExpandedRow(expandedRow === r.action.key ? null : r.action.key)}
          rowTone={(r) => (r.action.unresolved ? "warn" : undefined)}
          loading={catalogLoading}
          empty="No actions in the registry yet. Add and sync an action pack to populate this list."
        />
        {expandedRow && (() => {
          const row = registryRows.find((r) => r.action.key === expandedRow)
          if (!row?.contested) return null
          return (
            <div className="border-t border-line bg-surface-2 px-[11px] py-2">
              <div className="tt mb-1.5">
                <span className="mono normal-case tracking-normal text-text">{row.action.key}</span> — which pack wins
              </div>
              <div className="flex flex-col gap-0.5">
                {row.contested.candidates.map((c) => {
                  const checked = row.contested?.resolution?.pack_id === c.pack_id
                  return (
                    <label key={`${row.action.key}-${c.pack_id ?? "bundled"}`} className="row-hover flex cursor-pointer items-center gap-2 rounded-r px-1.5 py-1 text-xs">
                      <input type="radio" name={`winner-${row.action.key}`} checked={checked} disabled={upsertResolution.isPending} onChange={() => upsertResolution.mutate({ action_key: row.action.key, pack_id: c.pack_id })} />
                      <span className="flex-1 text-text">{packLabel(c)}</span>
                    </label>
                  )
                })}
              </div>
            </div>
          )
        })()}
      </Panel>

      {/* ---- Pack Sources — management-only ---- */}
      <Panel
        title="pack sources"
        meta="where the packs come from"
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
            Add pack…
          </button>
        }
      >
        {error && <Banner tone="danger">Could not load action packs: {error.message}</Banner>}
        <Table<PackRow>
          cols={[
            {
              k: "name",
              label: "pack",
              w: "minmax(140px,1fr)",
              sortable: false,
              cell: (p) => (
                <span className="flex items-center gap-1.5">
                  <span className="mono trunc font-medium text-text">{p.name}</span>
                  {isBundledRow(p) && <Tag title="baked into the container image">built-in</Tag>}
                </span>
              ),
            },
            {
              k: "source",
              label: "source",
              w: "minmax(220px,1.8fr)",
              sortable: false,
              cell: (p) => {
                if (isBundledRow(p)) return <span className="text-text-faint">baked into the container image</span>
                if (p.source_type === "local")
                  return (
                    <span className="flex min-w-0 flex-col">
                      <span className="mono trunc text-[11px]">{p.local_path}</span>
                      <span className="text-[10.5px] text-text-faint">local directory</span>
                    </span>
                  )
                return (
                  <span className="flex min-w-0 flex-col">
                    <span className="trunc">
                      {p.git_repository_name ?? "(missing)"}
                      {p.path ? <span className="mono text-text-faint"> / {p.path}</span> : null}
                    </span>
                    <span className="text-[10.5px] text-text-faint">git repository</span>
                  </span>
                )
              },
            },
            {
              k: "enabled",
              label: "enabled",
              w: "90px",
              sortable: false,
              cell: (p) => (isBundledRow(p) ? <span className="text-text-faint">—</span> : p.enabled ? <Tag tone="ok">enabled</Tag> : <Tag tone="warn">disabled</Tag>),
            },
            {
              k: "sync",
              label: "last sync",
              w: "170px",
              sortable: false,
              cell: (p) =>
                isBundledRow(p) ? (
                  <span className="text-text-faint">at build</span>
                ) : (
                  <span className="flex items-center gap-1.5" title={p.last_synced_at ? new Date(p.last_synced_at).toLocaleString() : undefined}>
                    {syncTag(p)}
                    {p.last_synced_at && <span className="mono num text-[11px]">{shortAgo(p.last_synced_at)} ago</span>}
                    {p.current_sha && <span className="mono text-[10.5px] text-text-faint">{p.current_sha.slice(0, 8)}</span>}
                  </span>
                ),
            },
            {
              k: "actions",
              label: "",
              w: "300px",
              right: true,
              sortable: false,
              cell: (pack) => {
                if (isBundledRow(pack)) return <span className="text-text-faint">immutable</span>
                const packKeyCount = packKeyCounts[pack.name] ?? 0
                return (
                  <span className="flex flex-wrap justify-end gap-0.5">
                    <button type="button" className="btn btn-sm btn-ghost" disabled={syncingId === pack.id || !pack.enabled} onClick={() => handleSync(pack)}>
                      {syncingId === pack.id ? "syncing…" : "sync"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      disabled={packKeyCount === 0}
                      onClick={() => openClaimDialog(pack)}
                      title={packKeyCount === 0 ? "this pack hasn't contributed any actions yet — sync it first, or check that its manifest is valid" : "pin every key this pack contributes to this pack"}
                    >
                      win all keys
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(pack)}>
                      edit
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => handleDelete(pack)} disabled={deleteMutation.isPending}>
                      delete
                    </button>
                  </span>
                )
              },
            },
          ]}
          rows={[BUNDLED_PACK_ROW, ...orderedPacks]}
          keyOf={(p) => p.id}
          loading={isLoading}
          empty="No action packs. Add one to bring more actions into the library."
        />
      </Panel>

      {dialogOpen && form && (
        <Modal
          title={editing ? "Edit action pack" : "Add action pack"}
          meta={editing?.name}
          w={560}
          onClose={closeDialog}
          onSubmit={(e) => {
            e.preventDefault()
            void handleSave()
          }}
          footer={
            <>
              <span className="tt mr-auto">LabDog looks for actions/*.manifest.yml</span>
              <button type="button" className="btn" onClick={closeDialog}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={formSaving}>
                {formSaving ? "Saving…" : editing ? "Save changes" : "Add pack"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 160px" }}>
            <Field label="name" htmlFor="pack-name">
              <input id="pack-name" className="inp mono" placeholder="labdog-default" value={form.name} onChange={(e) => setF("name", e.target.value)} />
            </Field>
            <Field label="source" htmlFor="pack-source">
              <select id="pack-source" className="inp" value={form.source_type} onChange={(e) => setF("source_type", e.target.value as PackSourceType)}>
                <option value="git">git repository</option>
                <option value="local">local directory</option>
              </select>
            </Field>
          </div>

          {form.source_type === "git" && (
            <>
              <Field label="git repository" htmlFor="pack-repo">
                {!hasGitRepos ? (
                  <Banner tone="warn">
                    No git repositories yet — add one under{" "}
                    <Link href="/git-repos" className="underline">
                      Settings › Git repositories
                    </Link>{" "}
                    first.
                  </Banner>
                ) : (
                  <select id="pack-repo" className="inp mono" value={form.git_repository_id ?? ""} onChange={(e) => setF("git_repository_id", e.target.value ? Number(e.target.value) : null)}>
                    <option value="">— pick a repository —</option>
                    {gitRepos!.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} ({r.url} @ {r.branch})
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label="path inside the repo" htmlFor="pack-path" hint="blank when the pack is at the repository root">
                <input id="pack-path" className="inp mono" placeholder="packs/ops" value={form.path} onChange={(e) => setF("path", e.target.value)} />
              </Field>
            </>
          )}

          {form.source_type === "local" && (
            <Field label="filesystem path" htmlFor="pack-local-path" hint="absolute, on the LabDog host — read in place, nothing is cloned">
              <input id="pack-local-path" className="inp mono" placeholder="/var/lib/labdog/my-pack" value={form.local_path} onChange={(e) => setF("local_path", e.target.value)} />
            </Field>
          )}

          <label className="flex items-center gap-2 text-xs text-text">
            <input id="pack-enabled" type="checkbox" checked={form.enabled} onChange={(e) => setF("enabled", e.target.checked)} />
            enabled
          </label>
          <label className="flex items-start gap-2 text-xs text-text">
            <input id="pack-trusted" type="checkbox" className="mt-0.5" checked={form.trusted} onChange={(e) => setF("trusted", e.target.checked)} />
            <span>
              allow content that runs on the LabDog host
              <span className="mt-0.5 block text-[11px] text-text-3">
                Ansible plugin directories, and plays targeting <span className="mono">localhost</span>, execute on the LabDog server itself rather than on a managed host. A pack containing either is refused unless this
                is set. Leave it off unless you have read the pack and intend it.
              </span>
            </span>
          </label>

          {formError && <Banner tone="danger">{formError}</Banner>}
        </Modal>
      )}

      {claimDialog && (
        <Modal
          title={`Make ${claimDialog.pack.name} the winner for all its keys`}
          w={480}
          onClose={() => setClaimDialog(null)}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setClaimDialog(null)} disabled={claiming}>
                {claimDialog.contested + claimDialog.uncontested === 0 ? "Close" : "Cancel"}
              </button>
              {claimDialog.contested + claimDialog.uncontested > 0 && (
                <button type="button" className="btn btn-primary" onClick={() => void handleClaim()} disabled={claiming}>
                  {claiming ? "Pinning…" : "Pin all keys"}
                </button>
              )}
            </>
          }
        >
          {claimDialog.contested + claimDialog.uncontested === 0 ? (
            <div className="text-[12.5px] text-text-2">
              <span className="mono text-text">{claimDialog.pack.name}</span> hasn&apos;t contributed any action keys yet, so there is nothing to pin. Sync the pack first, or check that its manifest is valid and
              discoverable.
            </div>
          ) : (
            <>
              <div className="text-[12.5px] text-text-2">
                This pack contributes to <span className="mono num text-text">{claimDialog.contested + claimDialog.uncontested}</span> action key{claimDialog.contested + claimDialog.uncontested === 1 ? "" : "s"}.
              </div>
              <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[11.5px] text-text-3">
                <li>
                  <span className="mono num text-text">{claimDialog.uncontested}</span> uncontested — a no-op for the resolver, but pinned explicitly so future contestants don&apos;t auto-claim them
                </li>
                <li>
                  <span className="mono num text-text">{claimDialog.contested}</span> contested: <span className="text-ok">{claimDialog.pinnedHere} already pinned here</span>,{" "}
                  <span className="text-warn">{claimDialog.pinnedElsewhere} pinned elsewhere (overwritten)</span>, {claimDialog.contested - claimDialog.pinnedHere - claimDialog.pinnedElsewhere} unpinned (pinned here)
                </li>
              </ul>
            </>
          )}
        </Modal>
      )}

      {confirmState && (
        <Confirm
          open
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
