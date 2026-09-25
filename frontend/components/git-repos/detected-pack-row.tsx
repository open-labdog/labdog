"use client"

import { Tag } from "@/components/ld"
import type { DetectedPack, KeyOwner } from "@/lib/types"

export type PackSelection = { checked: boolean }

/**
 * One action pack the scan found: a checkbox to activate it, its path,
 * the action keys it contributes (toned by whether another pack already
 * owns them), and the manifest errors that make it unpickable.
 */
export function DetectedPackRow({
  pack,
  selection,
  existingWinners,
  conflictKeys,
  inUnresolvedConflict,
  onToggle,
}: {
  pack: DetectedPack
  selection: PackSelection
  existingWinners: Record<string, KeyOwner>
  conflictKeys: Set<string>
  inUnresolvedConflict: boolean
  onToggle: (checked: boolean) => void
}) {
  const hasErrors = pack.errors.length > 0
  const sameKeyMatches = pack.contributed_keys.filter((k) => k in existingWinners)
  const border = inUnresolvedConflict ? "var(--danger)" : hasErrors ? "var(--warn)" : "var(--border)"

  return (
    <div
      data-testid="detected-pack-row"
      data-path={pack.path}
      data-conflict={inUnresolvedConflict ? "true" : "false"}
      data-has-errors={hasErrors ? "true" : "false"}
      className="rounded-r border bg-surface-2 px-2.5 py-2"
      style={{ borderColor: border, opacity: hasErrors ? 0.7 : 1 }}
    >
      <label className="flex items-start gap-2.5">
        <input type="checkbox" aria-label={`Activate pack ${pack.name}`} className="mt-0.5" checked={selection.checked} disabled={hasErrors} onChange={(e) => onToggle(e.target.checked)} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-medium text-text">{pack.name}</span>
            <span className="mono text-[10.5px] text-text-faint" title={pack.path || "(repo root)"}>
              {pack.path || "(repo root)"}
            </span>
            {sameKeyMatches.length > 0 && (
              <Tag tone="warn" title={sameKeyMatches.length === 1 ? `action key "${sameKeyMatches[0]}" already has an owner — you choose the winner below` : `${sameKeyMatches.length} action keys already have owners — you choose the winners below`}>
                contested
              </Tag>
            )}
            {!pack.pack_yml_present && <Tag title="no pack.yml at this path; the repo root is treated as one pack">no pack.yml</Tag>}
            {inUnresolvedConflict && <Tag tone="danger" title="two packs in this repo contribute the same action key — uncheck one">conflict</Tag>}
          </div>

          {pack.contributed_keys.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {pack.contributed_keys.map((key) => {
                const winner = existingWinners[key]
                const isConflict = conflictKeys.has(key)
                return (
                  <Tag
                    key={key}
                    tone={isConflict ? "danger" : winner ? "warn" : undefined}
                    title={
                      isConflict
                        ? `another pack in this repo also contributes "${key}"`
                        : winner
                          ? `currently provided by ${winner.source === "bundled" ? "the bundled pack" : `pack "${winner.pack_name}"`}`
                          : "action key contributed by this pack"
                    }
                  >
                    {key}
                  </Tag>
                )
              })}
            </div>
          )}

          {hasErrors && (
            <ul className="m-0 mt-1.5 flex list-none flex-col gap-0.5 p-0 text-[11px] text-warn">
              {pack.errors.map((err, idx) => (
                <li key={idx}>
                  <span className="mono">{err.file}</span> — {err.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      </label>
    </div>
  )
}
