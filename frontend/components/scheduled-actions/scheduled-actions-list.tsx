"use client"

import { useState } from "react"
import Link from "next/link"
import {
  CameraIcon,
  EyeIcon,
  GlobeIcon,
  MoreHorizontalIcon,
  PlayIcon,
  RotateCcwIcon,
  TrashIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Tooltip } from "@/components/ui/tooltip"
import { EnabledBadge, RunStatusBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import { cronToHuman } from "@/lib/cron"
import { formatRelativeTime, cn } from "@/lib/utils"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { RunHistoryDrawer } from "@/components/scheduled-actions/run-history-drawer"
import type { ScheduledAction } from "@/lib/types"

const STATUS_BORDER: Record<string, string> = {
  succeeded: "border-l-2 border-l-green-500/60",
  failed: "border-l-2 border-l-red-500/60",
  partial: "border-l-2 border-l-amber-500/60",
  running: "border-l-2 border-l-blue-500/60",
  queued: "border-l-2 border-l-blue-500/40",
}

interface ScheduledActionsListProps {
  rows: ScheduledAction[]
  /** When set, hides columns that are redundant in scope (e.g. the
   *  Target column is always "this host" on a host detail page). */
  hideColumns?: ("target" | "category")[]
  /** Optional custom empty-state — used by /schedules to surface a CTA. */
  emptyState?: React.ReactNode
}

export function ScheduledActionsList({
  rows,
  hideColumns = [],
  emptyState,
}: ScheduledActionsListProps) {
  const showTarget = !hideColumns.includes("target")
  const [editing, setEditing] = useState<ScheduledAction | null>(null)
  const [historyFor, setHistoryFor] = useState<ScheduledAction | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<ScheduledAction | null>(null)
  const [openMenu, setOpenMenu] = useState<number | null>(null)

  const deleteMutation = useApiMutation<unknown, number>({
    mutationFn: (id) =>
      apiFetch(`/api/scheduled-actions/${id}`, { method: "DELETE" }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"]],
    successMessage: "Schedule deleted",
    onSuccess: () => setConfirmDelete(null),
  })

  const runNowMutation = useApiMutation<unknown, number>({
    mutationFn: (id) =>
      apiFetch(`/api/scheduled-actions/${id}/run-now`, { method: "POST" }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"]],
    onSuccess: () => {
      showSuccess("Action started")
      setOpenMenu(null)
    },
  })

  if (rows.length === 0) {
    return (
      <>
        {emptyState ?? (
          <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 text-center text-sm text-slate-400">
            No schedules yet.
          </div>
        )}
      </>
    )
  }

  return (
    <>
      <div className="overflow-x-auto rounded-lg border border-slate-700 bg-slate-900">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-700 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">Action</th>
              {showTarget && <th className="px-3 py-2 font-medium">Target</th>}
              <th className="px-3 py-2 font-medium">Schedule</th>
              <th className="px-3 py-2 font-medium">Enabled</th>
              <th className="px-3 py-2 font-medium">Last run</th>
              <th className="px-3 py-2 font-medium">Options</th>
              <th className="px-3 py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <ScheduledActionRow
                key={row.id}
                row={row}
                showTarget={showTarget}
                openMenu={openMenu === row.id}
                onOpenMenu={(v) => setOpenMenu(v ? row.id : null)}
                onEdit={() => {
                  setOpenMenu(null)
                  setEditing(row)
                }}
                onRunNow={() => runNowMutation.mutate(row.id)}
                onDelete={() => {
                  setOpenMenu(null)
                  setConfirmDelete(row)
                }}
                onViewRuns={() => {
                  setOpenMenu(null)
                  setHistoryFor(row)
                }}
              />
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <ScheduleActionDialog
          open
          onOpenChange={(o) => !o && setEditing(null)}
          scheduledAction={editing}
        />
      )}

      {historyFor && (
        <RunHistoryDrawer
          scheduledAction={historyFor}
          open
          onClose={() => setHistoryFor(null)}
        />
      )}

      {confirmDelete && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setConfirmDelete(null)}
          title="Delete scheduled action?"
          description={`Removes the schedule for "${
            confirmDelete.action_name ?? confirmDelete.action_key
          }". Run history is preserved.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={async () => {
            await deleteMutation.mutateAsync(confirmDelete.id)
          }}
        />
      )}
    </>
  )
}

function ScheduledActionRow({
  row,
  showTarget,
  openMenu,
  onOpenMenu,
  onEdit,
  onRunNow,
  onDelete,
  onViewRuns,
}: {
  row: ScheduledAction
  showTarget: boolean
  openMenu: boolean
  onOpenMenu: (v: boolean) => void
  onEdit: () => void
  onRunNow: () => void
  onDelete: () => void
  onViewRuns: () => void
}) {
  const status = row.last_run?.status
  const borderClass = status ? STATUS_BORDER[status] ?? "" : ""

  return (
    <tr
      className={cn(
        "border-b border-slate-800 hover:bg-slate-800/40",
        borderClass,
      )}
      data-testid="scheduled-action-row"
      data-action-key={row.action_key}
    >
      <td className="px-3 py-2 align-top">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-100">
            {row.action_name ?? row.action_key}
          </span>
          {row.action_key.startsWith("_builtin.") && (
            <Badge className="bg-blue-700/40 text-blue-300 border border-blue-700/60">
              built-in
            </Badge>
          )}
        </div>
        {row.pack_name && row.pack_name !== "_builtin" && (
          <span className="text-xs text-slate-500">from {row.pack_name}</span>
        )}
      </td>
      {showTarget && (
        <td className="px-3 py-2 align-top">
          <TargetCell row={row} />
        </td>
      )}
      <td className="px-3 py-2 align-top">
        {row.schedule_cron ? (
          <>
            <span className="font-mono text-xs text-slate-300">
              {row.schedule_cron}
            </span>
            <div className="text-xs text-slate-500">
              {cronToHuman(row.schedule_cron)}
            </div>
          </>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
      <td className="px-3 py-2 align-top">
        <EnabledBadge enabled={row.enabled} />
      </td>
      <td className="px-3 py-2 align-top">
        {row.last_run ? (
          <div className="flex items-center gap-2">
            <RunStatusBadge status={row.last_run.status} />
            <span className="text-xs text-slate-400">
              {formatRelativeTime(
                row.last_run.started_at ?? row.last_run.created_at,
              )}
            </span>
          </div>
        ) : (
          <span className="text-slate-600">Never</span>
        )}
      </td>
      <td className="px-3 py-2 align-top">
        {row.destructive ? (
          <div className="flex items-center gap-1.5">
            <Tooltip
              content={`Snapshot: ${row.snapshot_enabled ? "on" : "off"}`}
            >
              <CameraIcon
                className={`h-3.5 w-3.5 ${
                  row.snapshot_enabled ? "text-green-400" : "text-slate-700"
                }`}
              />
            </Tooltip>
            <Tooltip content={`Verify: ${row.verify_enabled ? "on" : "off"}`}>
              <EyeIcon
                className={`h-3.5 w-3.5 ${
                  row.verify_enabled ? "text-green-400" : "text-slate-700"
                }`}
              />
            </Tooltip>
            <Tooltip
              content={`Auto-rollback: ${row.auto_rollback ? "on" : "off"}`}
            >
              <RotateCcwIcon
                className={`h-3.5 w-3.5 ${
                  row.auto_rollback ? "text-green-400" : "text-slate-700"
                }`}
              />
            </Tooltip>
          </div>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
      <td className="px-3 py-2 align-top text-right">
        <div className="relative inline-block">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onOpenMenu(!openMenu)}
            aria-label="Row actions"
          >
            <MoreHorizontalIcon className="h-4 w-4" />
          </Button>
          {openMenu && (
            <div className="absolute right-0 z-10 mt-1 w-44 rounded-md border border-slate-700 bg-slate-800 shadow-lg">
              <MenuButton onClick={onEdit}>Edit</MenuButton>
              <MenuButton onClick={onRunNow} icon={<PlayIcon className="h-3.5 w-3.5" />}>
                Run now
              </MenuButton>
              <MenuButton onClick={onViewRuns}>View runs</MenuButton>
              <MenuButton
                onClick={onDelete}
                icon={<TrashIcon className="h-3.5 w-3.5" />}
                className="text-red-400 hover:bg-red-950"
              >
                Delete
              </MenuButton>
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}

function TargetCell({ row }: { row: ScheduledAction }) {
  if (row.target_kind === "fleet") {
    return (
      <span className="flex items-center gap-1.5 text-slate-300">
        <GlobeIcon className="h-3.5 w-3.5 text-slate-500" />
        All hosts
      </span>
    )
  }
  if (row.target_kind === "host") {
    return (
      <Link
        href={`/hosts/${row.target_id}`}
        className="text-slate-200 hover:text-white"
      >
        {row.target_name ?? `Host #${row.target_id}`}
      </Link>
    )
  }
  return (
    <Link
      href={`/groups/${row.target_id}`}
      className="text-slate-200 hover:text-white"
    >
      {row.target_name ?? `Group #${row.target_id}`}
    </Link>
  )
}

function MenuButton({
  onClick,
  children,
  icon,
  className,
}: {
  onClick: () => void
  children: React.ReactNode
  icon?: React.ReactNode
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-slate-700",
        className,
      )}
    >
      {icon}
      {children}
    </button>
  )
}
