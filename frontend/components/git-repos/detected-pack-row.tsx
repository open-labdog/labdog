"use client"

import { AlertTriangleIcon, FolderIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Tooltip } from "@/components/ui/tooltip"
import type { DetectedPack, KeyOwner, PackRole } from "@/lib/types"

export type PackSelection = { checked: boolean; role: PackRole }

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

  const borderClass = inUnresolvedConflict
    ? "border-red-500/60"
    : hasErrors
    ? "border-amber-500/40"
    : "border-slate-700"
  const dimClass = hasErrors ? "opacity-60" : ""

  return (
    <div
      data-testid="detected-pack-row"
      data-path={pack.path}
      data-conflict={inUnresolvedConflict ? "true" : "false"}
      data-has-errors={hasErrors ? "true" : "false"}
      className={`rounded-lg border ${borderClass} bg-slate-900 p-4 ${dimClass}`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          aria-label={`Activate pack ${pack.name}`}
          className="mt-1 h-4 w-4 rounded border-slate-600 bg-slate-800 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
          checked={selection.checked}
          disabled={hasErrors}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <FolderIcon className="h-3.5 w-3.5 text-slate-500" />
            <span className="text-sm font-medium text-white truncate">{pack.name}</span>
            <span className="font-mono text-xs text-slate-500" title={pack.path || "(repo root)"}>
              {pack.path || "(repo root)"}
            </span>
            <Tooltip
              content={
                selection.role === "override"
                  ? "Same-key match: this pack will override the existing action."
                  : "Novel key: this pack contributes new actions."
              }
            >
              <Badge
                variant="outline"
                className={
                  selection.role === "override"
                    ? "border-amber-500/60 text-amber-300"
                    : "border-slate-600 text-slate-300"
                }
              >
                {selection.role}
              </Badge>
            </Tooltip>
            {!pack.pack_yml_present && (
              <Tooltip content="No pack.yml found at this path; treating the repo root as a single pack.">
                <Badge variant="outline" className="border-slate-600 text-slate-400">
                  no pack.yml
                </Badge>
              </Tooltip>
            )}
            {inUnresolvedConflict && (
              <Tooltip content="Two packs in this repo contribute the same action key. Uncheck one to resolve.">
                <Badge className="bg-red-600 text-white">conflict</Badge>
              </Tooltip>
            )}
          </div>

          {pack.contributed_keys.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {pack.contributed_keys.map((key) => {
                const winner = existingWinners[key]
                const isConflict = conflictKeys.has(key)
                return (
                  <Tooltip
                    key={key}
                    content={
                      isConflict
                        ? `Another pack in this repo also contributes "${key}".`
                        : winner
                        ? `Currently provided by ${winner.source === "bundled" ? "the bundled pack" : `pack "${winner.pack_name}"`}.`
                        : `Action key contributed by this pack.`
                    }
                  >
                    <Badge
                      variant="outline"
                      className={
                        isConflict
                          ? "border-red-500/70 text-red-300"
                          : winner
                          ? "border-amber-500/60 text-amber-300"
                          : "border-slate-600 text-slate-300"
                      }
                    >
                      {key}
                    </Badge>
                  </Tooltip>
                )
              })}
            </div>
          )}

          {sameKeyMatches.length > 0 && !selection.checked && (
            <p className="mt-2 text-xs text-slate-500">
              {sameKeyMatches.length === 1
                ? `If unchecked, the existing "${sameKeyMatches[0]}" action will be used.`
                : `If unchecked, the existing actions for ${sameKeyMatches.map((k) => `"${k}"`).join(", ")} will be used.`}
            </p>
          )}

          {hasErrors && (
            <ul className="mt-2 space-y-1">
              {pack.errors.map((err, idx) => (
                <li key={idx} className="flex items-start gap-1.5 text-xs text-amber-300">
                  <AlertTriangleIcon className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <span>
                    <span className="font-mono text-amber-200">{err.file}</span> — {err.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

      </div>
    </div>
  )
}
