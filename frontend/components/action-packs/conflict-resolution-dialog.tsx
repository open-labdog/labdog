"use client"

import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, RotateCcw } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import type { ContestedActionKey, ResolutionPack } from "@/lib/types"

interface Props {
  open: boolean
  onClose: () => void
}

/** Render a single candidate identifier — bundled vs DB pack vs no-row */
function packLabel(p: ResolutionPack): string {
  if (p.pack_id === null) return `${p.pack_name} (bundled)`
  return p.pack_name
}

export function ConflictResolutionDialog({ open, onClose }: Props) {
  const { data: rows, isLoading } = useQuery<ContestedActionKey[]>({
    queryKey: ["action-resolutions"],
    queryFn: () => apiFetch<ContestedActionKey[]>("/api/action-resolutions"),
    enabled: open,
  })

  const upsertMutation = useApiMutation<
    unknown,
    { action_key: string; pack_id: number | null }
  >({
    mutationFn: ({ action_key, pack_id }) =>
      apiFetch(
        `/api/action-resolutions/${encodeURIComponent(action_key)}`,
        { method: "PUT", json: { pack_id } },
      ),
    invalidateKeys: [
      ["action-resolutions"],
      ["actions"],
      ["action-packs"],
    ],
  })

  const deleteMutation = useApiMutation<unknown, string>({
    mutationFn: (action_key) =>
      apiFetch(
        `/api/action-resolutions/${encodeURIComponent(action_key)}`,
        { method: "DELETE" },
      ),
    invalidateKeys: [
      ["action-resolutions"],
      ["actions"],
      ["action-packs"],
    ],
  })

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Resolve action-key conflicts</DialogTitle>
        </DialogHeader>

        <p className="text-sm text-slate-400">
          When more than one pack defines the same action key, only one pack
          can win. Frozen rows are using the previous winner because a sync
          introduced a fresh conflict — pick a pack to confirm the choice.
        </p>

        <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1 mt-2">
          {isLoading && (
            <p className="text-sm text-slate-500">Loading…</p>
          )}
          {!isLoading && rows && rows.length === 0 && (
            <p className="text-sm text-slate-500">
              No contested action keys.
            </p>
          )}
          {rows?.map((row) => {
            const pendingForKey =
              upsertMutation.isPending || deleteMutation.isPending
            return (
              <div
                key={row.action_key}
                className="rounded-lg border border-slate-700 bg-slate-900 p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono text-sm text-white truncate">
                      {row.action_key}
                    </span>
                    {row.is_frozen && (
                      <span className="inline-flex items-center gap-1 rounded-md bg-amber-950/60 border border-amber-800 px-1.5 py-0.5 text-[11px] text-amber-300 flex-shrink-0">
                        <AlertTriangle className="h-3 w-3" />
                        frozen
                      </span>
                    )}
                  </div>
                  {row.resolution && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-slate-400 hover:text-slate-200"
                      onClick={() =>
                        deleteMutation.mutate(row.action_key)
                      }
                      disabled={pendingForKey}
                      title="Drop the pin and use position-based default"
                    >
                      <RotateCcw className="h-3.5 w-3.5 mr-1" />
                      Reset
                    </Button>
                  )}
                </div>

                <div className="mt-3 space-y-1.5">
                  {row.candidates
                    .slice()
                    .reverse() // Highest-priority first for display.
                    .map((c) => {
                      const checked =
                        row.current_winner.pack_id === c.pack_id
                      return (
                        <label
                          key={`${row.action_key}-${c.pack_id ?? "bundled"}`}
                          className="flex items-center gap-2 text-sm cursor-pointer rounded px-2 py-1 hover:bg-slate-800"
                        >
                          <input
                            type="radio"
                            name={`winner-${row.action_key}`}
                            checked={checked}
                            disabled={pendingForKey}
                            onChange={() =>
                              upsertMutation.mutate({
                                action_key: row.action_key,
                                pack_id: c.pack_id,
                              })
                            }
                          />
                          <span className="text-slate-200 flex-1">
                            {packLabel(c)}
                          </span>
                          <span className="text-xs text-slate-500">
                            position {c.position}
                          </span>
                        </label>
                      )
                    })}
                </div>
              </div>
            )
          })}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
