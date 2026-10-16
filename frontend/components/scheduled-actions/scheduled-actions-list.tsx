"use client"

import { useState } from "react"
import Link from "next/link"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { cronToHuman } from "@/lib/cron"
import { shortAgo } from "@/lib/fleet"
import { Confirm, RunStatus, Table, Tag } from "@/components/ld"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { RunHistoryModal } from "@/components/scheduled-actions/run-history-drawer"
import type { ScheduledAction } from "@/lib/types"

interface ScheduledActionsListProps {
  rows: ScheduledAction[]
  /** When set, hides columns that are redundant in scope (e.g. the
   *  Target column is always "this host" on a host detail page). */
  hideColumns?: ("target" | "category")[]
  /** Optional custom empty-state — used by the Schedules tab to surface a CTA. */
  emptyState?: React.ReactNode
  loading?: boolean
}

function TargetCell({ row }: { row: ScheduledAction }) {
  if (row.target_kind === "fleet") return <span className="text-text-2">all hosts</span>
  const href = row.target_kind === "host" ? `/hosts/${row.target_id}` : `/groups/${row.target_id}`
  const fallback = row.target_kind === "host" ? `host #${row.target_id}` : `group #${row.target_id}`
  return <Link href={href} className="text-text-2 hover:text-text">{row.target_name ?? fallback}</Link>
}

export function ScheduledActionsList({ rows, hideColumns = [], emptyState, loading }: ScheduledActionsListProps) {
  const showTarget = !hideColumns.includes("target")
  const [editing, setEditing] = useState<ScheduledAction | null>(null)
  const [historyFor, setHistoryFor] = useState<ScheduledAction | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<ScheduledAction | null>(null)

  const deleteMutation = useApiMutation<unknown, number>({
    mutationFn: (id) => apiFetch(`/api/scheduled-actions/${id}`, { method: "DELETE" }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"]],
    successMessage: "Schedule deleted",
    onSuccess: () => setConfirmDelete(null),
  })

  return (
    <>
      <Table<ScheduledAction>
        cols={[
          {
            k: "action", label: "action", w: "minmax(160px,1.2fr)", sortable: false,
            cell: (r) => (
              <span className="flex min-w-0 flex-col">
                <span className="flex items-center gap-1.5">
                  <span className="font-medium text-text">{r.action_name ?? r.action_key}</span>
                  {r.action_key.startsWith("_builtin.") && <Tag tone="accent">built-in</Tag>}
                </span>
                {r.pack_name && r.pack_name !== "_builtin" && <span className="text-[10.5px] text-text-faint">from {r.pack_name}</span>}
              </span>
            ),
          },
          ...(showTarget ? [{ k: "target", label: "target", w: "minmax(110px,1fr)", sortable: false, cell: (r: ScheduledAction) => <TargetCell row={r} /> }] : []),
          {
            k: "schedule", label: "schedule", w: "minmax(120px,1fr)", sortable: false,
            cell: (r) => r.schedule_cron ? (
              <span className="flex min-w-0 flex-col">
                <span className="mono text-[11px] text-text-2">{r.schedule_cron}</span>
                <span className="text-[10.5px] text-text-faint">{cronToHuman(r.schedule_cron)}</span>
              </span>
            ) : <span className="text-text-faint">—</span>,
          },
          { k: "enabled", label: "enabled", w: "80px", sortable: false, cell: (r) => <Tag tone={r.enabled ? "ok" : undefined}>{r.enabled ? "enabled" : "disabled"}</Tag> },
          {
            k: "last_run", label: "last run", w: "minmax(120px,1fr)", sortable: false,
            cell: (r) => r.last_run ? (
              <span className="flex items-center gap-1.5">
                <RunStatus s={r.last_run.status} />
                <span className="text-[10.5px] text-text-faint">{shortAgo(r.last_run.started_at ?? r.last_run.created_at)} ago</span>
              </span>
            ) : <span className="text-text-faint">never</span>,
          },
          {
            k: "options", label: "options", w: "100px", sortable: false,
            cell: (r) => r.destructive ? (
              <span className="flex gap-1">
                <Tag tone={r.snapshot_enabled ? "ok" : undefined} title={`snapshot: ${r.snapshot_enabled ? "on" : "off"}`}>snap</Tag>
                <Tag tone={r.verify_enabled ? "ok" : undefined} title={`verify: ${r.verify_enabled ? "on" : "off"}`}>verify</Tag>
                <Tag tone={r.auto_rollback ? "ok" : undefined} title={`auto-rollback: ${r.auto_rollback ? "on" : "off"}`}>rollback</Tag>
              </span>
            ) : <span className="text-text-faint">—</span>,
          },
          {
            k: "actions", label: "", w: "140px", right: true, sortable: false,
            cell: (r) => (
              <span className="flex gap-0.5" data-testid="scheduled-action-row" data-action-key={r.action_key}>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEditing(r)}>edit</button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setHistoryFor(r)}>runs</button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setConfirmDelete(r)}>delete</button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(r) => r.id}
        loading={loading}
        empty={emptyState ?? "No schedules yet."}
      />

      {editing && <ScheduleActionDialog open onOpenChange={(o) => !o && setEditing(null)} scheduledAction={editing} />}
      {historyFor && <RunHistoryModal scheduledAction={historyFor} open onClose={() => setHistoryFor(null)} />}

      {confirmDelete && (
        <Confirm
          open
          onOpenChange={(o) => !o && setConfirmDelete(null)}
          title="Delete schedule?"
          description={`Removes the schedule for "${confirmDelete.action_name ?? confirmDelete.action_key}". Run history is preserved.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(confirmDelete.id)}
        />
      )}
    </>
  )
}
