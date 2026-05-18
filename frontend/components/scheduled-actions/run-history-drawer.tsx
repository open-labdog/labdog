"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { PlayIcon, XIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog"
import { RunStatusBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import { formatRelativeTime } from "@/lib/utils"
import type { ActionRun, ScheduledAction } from "@/lib/types"

const RUN_LIMIT = 20

interface RunHistoryDrawerProps {
  scheduledAction: ScheduledAction
  open: boolean
  onClose: () => void
}

export function RunHistoryDrawer({
  scheduledAction,
  open,
  onClose,
}: RunHistoryDrawerProps) {
  const { data: runs, isLoading } = useQuery<ActionRun[]>({
    queryKey: ["scheduled-action-runs", scheduledAction.id],
    queryFn: () =>
      apiFetch<ActionRun[]>(
        `/api/scheduled-actions/${scheduledAction.id}/runs?limit=${RUN_LIMIT}`,
      ),
    enabled: open,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.some((r) => r.status === "running" || r.status === "queued")) {
        return 3000
      }
      return false
    },
  })

  const runNowMutation = useApiMutation<unknown, number>({
    mutationFn: (id) =>
      apiFetch(`/api/scheduled-actions/${id}/run-now`, { method: "POST" }),
    invalidateKeys: [
      ["scheduled-actions"],
      ["scheduled-actions-by-target"],
      ["scheduled-action-runs", scheduledAction.id],
    ],
    onSuccess: () => showSuccess("Action started"),
  })

  const possiblyTruncated = (runs?.length ?? 0) >= RUN_LIMIT
  const inFlight =
    runs?.some((r) => r.status === "running" || r.status === "queued") ?? false

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogPortal>
        <DialogOverlay />
        <DialogPrimitive.Popup
          data-slot="dialog-content"
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col gap-0 overflow-hidden bg-background ring-1 ring-foreground/10 outline-none data-open:animate-in data-open:slide-in-from-right data-closed:animate-out data-closed:slide-out-to-right duration-150"
        >
          <div className="flex items-start justify-between gap-3 border-b border-slate-800 p-4">
            <div className="min-w-0 flex-1">
              <DialogTitle className="truncate">
                {scheduledAction.action_name ?? scheduledAction.action_key}
              </DialogTitle>
              <p className="mt-1 text-xs text-slate-400 truncate">
                {scheduledAction.target_name ?? "—"} · run history
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                size="sm"
                onClick={() => runNowMutation.mutate(scheduledAction.id)}
                disabled={runNowMutation.isPending || inFlight}
                className="gap-1.5"
                title={
                  inFlight ? "A run is already in flight" : "Trigger a run now"
                }
                data-testid="run-now-button"
              >
                <PlayIcon className="h-3.5 w-3.5" />
                {runNowMutation.isPending ? "Starting…" : "Run now"}
              </Button>
              <button
                type="button"
                onClick={onClose}
                className="rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
                aria-label="Close"
              >
                <XIcon className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
            {!isLoading && runs && runs.length === 0 && (
              <p className="text-sm text-slate-500">No runs recorded yet.</p>
            )}
            <div className="space-y-2">
              {runs?.map((run) => {
                const detailHref = run.host_id
                  ? `/hosts/${run.host_id}/actions/runs/${run.id}`
                  : run.group_id
                    ? `/groups/${run.group_id}/actions/runs/${run.id}`
                    : `/actions/runs/${run.id}`
                return (
                  <Link
                    key={run.id}
                    href={detailHref}
                    className="flex items-center justify-between gap-2 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm hover:border-slate-600"
                  >
                    <div className="flex items-center gap-2">
                      <RunStatusBadge status={run.status} reason={run.pending_reason} />
                      <span className="text-slate-400">
                        {formatRelativeTime(run.started_at ?? run.created_at)}
                      </span>
                    </div>
                    <span className="text-xs text-slate-500">#{run.id}</span>
                  </Link>
                )
              })}
            </div>
          </div>

          {possiblyTruncated && (
            <p className="border-t border-slate-800 px-4 py-3 text-xs text-slate-500">
              Showing the latest {RUN_LIMIT} runs. Older runs remain in the
              audit log.
            </p>
          )}
        </DialogPrimitive.Popup>
      </DialogPortal>
    </Dialog>
  )
}
