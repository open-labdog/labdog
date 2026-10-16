"use client"

import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import { shortAgo } from "@/lib/fleet"
import { Modal, RunStatus, Table } from "@/components/ld"
import type { ActionRun, ScheduledAction } from "@/lib/types"

const RUN_LIMIT = 20

interface RunHistoryModalProps {
  scheduledAction: ScheduledAction
  open: boolean
  onClose: () => void
}

function runHref(run: ActionRun): string {
  if (run.host_id) return `/hosts/${run.host_id}/actions/runs/${run.id}`
  if (run.group_id) return `/groups/${run.group_id}/actions/runs/${run.id}`
  return `/actions/runs/${run.id}`
}

export function RunHistoryModal({ scheduledAction, open, onClose }: RunHistoryModalProps) {
  const router = useRouter()
  const { data: runs, isLoading } = useQuery<ActionRun[]>({
    queryKey: ["scheduled-action-runs", scheduledAction.id],
    queryFn: () => apiFetch<ActionRun[]>(`/api/scheduled-actions/${scheduledAction.id}/runs?limit=${RUN_LIMIT}`),
    enabled: open,
    refetchInterval: (query) => {
      const data = query.state.data
      return data?.some((r) => r.status === "running" || r.status === "queued") ? 3000 : false
    },
  })

  const runNowMutation = useApiMutation<unknown, number>({
    mutationFn: (id) => apiFetch(`/api/scheduled-actions/${id}/run-now`, { method: "POST" }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"], ["scheduled-action-runs", scheduledAction.id]],
    onSuccess: () => showSuccess("Action started"),
  })

  if (!open) return null

  const inFlight = runs?.some((r) => r.status === "running" || r.status === "queued") ?? false
  const possiblyTruncated = (runs?.length ?? 0) >= RUN_LIMIT

  return (
    <Modal
      title={scheduledAction.action_name ?? scheduledAction.action_key}
      meta={`${scheduledAction.target_name ?? "—"} · run history`}
      w={520}
      onClose={onClose}
      footer={
        <>
          {possiblyTruncated && <span className="tt mr-auto">showing the latest {RUN_LIMIT} runs — older runs remain in the audit log</span>}
          <button type="button" className={possiblyTruncated ? "btn" : "btn ml-auto"} disabled={runNowMutation.isPending || inFlight} title={inFlight ? "A run is already in flight" : "Trigger a run now"} data-testid="run-now-button" onClick={() => runNowMutation.mutate(scheduledAction.id)}>
            {runNowMutation.isPending ? "Starting…" : "Run now"}
          </button>
        </>
      }
    >
      <Table<ActionRun>
        cols={[
          { k: "status", label: "status", w: "110px", sortable: false, cell: (r) => <RunStatus s={r.status} reason={r.pending_reason} /> },
          { k: "when", label: "when", w: "minmax(120px,1fr)", sortable: false, cell: (r) => <span className="text-text-2">{shortAgo(r.started_at ?? r.created_at)} ago</span> },
          { k: "id", label: "", w: "60px", right: true, sortable: false, cell: (r) => <span className="mono num text-[10.5px] text-text-faint">#{r.id}</span> },
          { k: "go", label: "", w: "70px", right: true, sortable: false, cell: () => <span className="tt text-ld-accent">open →</span> },
        ]}
        rows={runs ?? []}
        keyOf={(r) => r.id}
        onRowClick={(r) => router.push(runHref(r))}
        loading={isLoading}
        empty="No runs recorded yet."
      />
    </Modal>
  )
}
