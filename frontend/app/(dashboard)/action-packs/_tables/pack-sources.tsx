"use client"

import { shortAgo } from "@/lib/fleet"
import type { ActionPack } from "@/lib/types"
import { Table, Tag } from "@/components/ld"

function syncTag(pack: ActionPack) {
  if (pack.last_sync_status === "ok") return <Tag tone="ok">synced</Tag>
  if (pack.last_sync_status === "failed") return <Tag tone="danger">failed</Tag>
  return <Tag>never synced</Tag>
}

/** The configured pack sources, each with sync, win-all-keys, edit and delete. */
export function PackSourcesTable({
  packs,
  loading,
  packKeyCounts,
  syncingId,
  deletePending,
  onSync,
  onClaim,
  onEdit,
  onDelete,
}: {
  packs: ActionPack[]
  loading: boolean
  packKeyCounts: Record<string, number>
  syncingId: number | null
  deletePending: boolean
  onSync: (pack: ActionPack) => void
  onClaim: (pack: ActionPack) => void
  onEdit: (pack: ActionPack) => void
  onDelete: (pack: ActionPack) => void
}) {
  return (
    <Table<ActionPack>
      cols={[
        {
          k: "name",
          label: "pack",
          w: "minmax(140px,1fr)",
          sortable: false,
          cell: (p) => <span className="mono trunc font-medium text-text">{p.name}</span>,
        },
        {
          k: "source",
          label: "source",
          w: "minmax(220px,1.8fr)",
          sortable: false,
          cell: (p) => {
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
          cell: (p) => (p.enabled ? <Tag tone="ok">enabled</Tag> : <Tag tone="warn">disabled</Tag>),
        },
        {
          k: "sync",
          label: "last sync",
          w: "170px",
          sortable: false,
          cell: (p) => (
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
            const packKeyCount = packKeyCounts[pack.name] ?? 0
            return (
              <span className="flex flex-wrap justify-end gap-0.5">
                <button type="button" className="btn btn-sm btn-ghost" disabled={syncingId === pack.id || !pack.enabled} onClick={() => onSync(pack)}>
                  {syncingId === pack.id ? "syncing…" : "sync"}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  disabled={packKeyCount === 0}
                  onClick={() => onClaim(pack)}
                  title={packKeyCount === 0 ? "this pack hasn't contributed any actions yet — sync it first, or check that its manifest is valid" : "pin every key this pack contributes to this pack"}
                >
                  win all keys
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => onEdit(pack)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => onDelete(pack)} disabled={deletePending}>
                  delete
                </button>
              </span>
            )
          },
        },
      ]}
      rows={packs}
      keyOf={(p) => p.id}
      loading={loading}
      empty="No action packs, so only the built-in actions are available. Add one to bring actions into the library."
    />
  )
}
