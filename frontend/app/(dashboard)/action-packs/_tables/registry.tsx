"use client"

import { apiFetch } from "@/lib/api"
import { shortAgo } from "@/lib/fleet"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import type { ActionPack } from "@/lib/types"
import { Table, Tag } from "@/components/ld"
import type { RegistryRow } from "../_hooks/use-registry-rows"

/** Where a pack's content comes from, in one line. */
function packSource(pack: ActionPack): string {
  if (pack.source_type === "local") return pack.local_path ?? ""
  const repo = pack.git_repository_name ?? "(missing repository)"
  return pack.path ? `${repo} / ${pack.path}` : repo
}

/**
 * Every action key the registry knows and which pack owns it. A contested
 * row expands into the winner picker.
 */
export function RegistryTable({
  rows,
  packsById,
  expandedRow,
  setExpandedRow,
  loading,
}: {
  rows: RegistryRow[]
  packsById: Record<number, ActionPack>
  expandedRow: string | null
  setExpandedRow: (key: string | null) => void
  loading: boolean
}) {
  const upsertResolution = useApiMutation<
    unknown,
    { action_key: string; pack_id: number; pack_name: string }
  >({
    mutationFn: ({ action_key, pack_id }) =>
      apiFetch(`/api/action-resolutions/${encodeURIComponent(action_key)}`, {
        method: "PUT",
        json: { pack_id },
      }),
    invalidateKeys: [["action-resolutions"], ["actions-catalog"], ["action-packs"]],
    onSuccess: (_data, { action_key, pack_name }) => showSuccess(`${action_key} now runs from ${pack_name}`),
  })

  /** The winner picker, rendered right under an expanded registry row. */
  function renderPicker({ action, contested: c }: RegistryRow) {
    if (!c) return null
    const pin = c.resolution
    const pending = upsertResolution.isPending
    return (
      <div className="flex flex-col gap-1.5 pl-[15px]">
        {c.is_frozen && pin ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-text-3">
            <span className="min-w-0 flex-1">
              Pinned to <span className="mono text-text">{pin.pack_name}</span> automatically when another pack started declaring this action, so what runs didn&apos;t change on its own. Keep it, or
              pick another pack.
            </span>
            <button
              type="button"
              className="btn btn-sm"
              disabled={pending}
              onClick={() => upsertResolution.mutate({ action_key: action.key, pack_id: pin.pack_id, pack_name: pin.pack_name })}
            >
              Keep {pin.pack_name}
            </button>
          </div>
        ) : c.is_unresolved ? (
          <div className="text-[11.5px] text-warn">Several packs declare this action. Pick the one that runs it; until then it can&apos;t run.</div>
        ) : (
          <div className="text-[11.5px] text-text-3">Several packs declare this action. The one picked here runs it.</div>
        )}
        <div role="radiogroup" aria-label={`Pack that runs ${action.key}`} className="flex flex-col">
          {c.candidates.map((cand) => {
            const pack = packsById[cand.pack_id]
            const checked = pin?.pack_id === cand.pack_id
            return (
              <label key={cand.pack_id} className="row-hover flex cursor-pointer items-center gap-2.5 rounded-r px-1.5 py-1 text-xs">
                <input
                  type="radio"
                  name={`winner-${action.key}`}
                  checked={checked}
                  disabled={pending}
                  onChange={() => upsertResolution.mutate({ action_key: action.key, pack_id: cand.pack_id, pack_name: cand.pack_name })}
                />
                <span className="mono w-[170px] shrink-0 trunc text-text">{cand.pack_name}</span>
                <span className="min-w-0 flex-1 trunc text-text-3">{pack ? packSource(pack) : ""}</span>
                {pack?.current_sha && <span className="mono shrink-0 text-[10.5px] text-text-faint">{pack.current_sha.slice(0, 8)}</span>}
                {pack?.last_synced_at && (
                  <span className="mono num w-[64px] shrink-0 text-right text-[11px] text-text-3" title={new Date(pack.last_synced_at).toLocaleString()}>
                    {shortAgo(pack.last_synced_at)} ago
                  </span>
                )}
                <span className="w-[52px] shrink-0 text-right">{checked && <Tag tone="ok">winner</Tag>}</span>
              </label>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <Table<RegistryRow>
      cols={[
        {
          k: "key",
          label: "action key",
          w: "minmax(200px,1.2fr)",
          sortable: false,
          cell: ({ action, contested }) => (
            <span className="flex items-center gap-1.5" title={contested ? "several packs declare this action — click to choose which one runs it" : undefined}>
              {/* Fixed gutter on every row so the keys line up whether or not they expand. */}
              <span aria-hidden className="tt w-[9px] shrink-0 text-[8px] text-text-faint">
                {contested ? (expandedRow === action.key ? "▼" : "▶") : null}
              </span>
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
            action.unresolved ? <span className="italic text-warn">no winner pinned</span> : <span className="text-text-2">{action.pack_name}</span>,
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
      ]}
      rows={rows}
      keyOf={(r) => r.action.key}
      activeKey={expandedRow ?? undefined}
      expandedKey={expandedRow ?? undefined}
      renderExpanded={renderPicker}
      onRowClick={(r) => r.contested && setExpandedRow(expandedRow === r.action.key ? null : r.action.key)}
      rowTone={(r) => (r.action.unresolved ? "warn" : undefined)}
      loading={loading}
      empty="No actions in the registry yet. Add and sync an action pack to populate this list."
    />
  )
}
