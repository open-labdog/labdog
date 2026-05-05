"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { XIcon } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { RunStatusBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
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

  // The query returns at most RUN_LIMIT rows. If exactly that many came
  // back there are probably more — flag it so operators don't audit
  // against silently-truncated history.
  const possiblyTruncated = (runs?.length ?? 0) >= RUN_LIMIT

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogTitle>
                {scheduledAction.action_name ?? scheduledAction.action_key}
              </DialogTitle>
              <p className="mt-1 text-sm text-slate-400">
                Recent runs for this schedule
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-slate-500 hover:text-slate-300"
              aria-label="Close"
            >
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        </DialogHeader>

        <div className="mt-4 space-y-2">
          {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
          {!isLoading && runs && runs.length === 0 && (
            <p className="text-sm text-slate-500">No runs recorded yet.</p>
          )}
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
                  <RunStatusBadge status={run.status} />
                  <span className="text-slate-400">
                    {formatRelativeTime(run.started_at ?? run.created_at)}
                  </span>
                </div>
                <span className="text-xs text-slate-500">#{run.id}</span>
              </Link>
            )
          })}
        </div>

        {possiblyTruncated && (
          <p className="mt-3 text-xs text-slate-500">
            Showing the latest {RUN_LIMIT} runs. Older runs are still in the
            audit log.
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}
