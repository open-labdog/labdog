"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import Link from "next/link"
import {
  CameraIcon,
  EyeIcon,
  GlobeIcon,
  MoreHorizontalIcon,
  RotateCcwIcon,
  TrashIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable, type ColumnDef } from "@/components/ui/data-table"
import { Tooltip } from "@/components/ui/tooltip"
import { EnabledBadge, RunStatusBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { cronToHuman } from "@/lib/cron"
import { formatRelativeTime } from "@/lib/utils"
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
  /** DataTable identity for column-width persistence. Per-context so
   *  the host-detail compact view doesn't share widths with /schedules. */
  tableId?: string
}

export function ScheduledActionsList({
  rows,
  hideColumns = [],
  emptyState,
  tableId = "scheduled-actions-v1",
}: ScheduledActionsListProps) {
  const showTarget = !hideColumns.includes("target")
  const [editing, setEditing] = useState<ScheduledAction | null>(null)
  const [historyFor, setHistoryFor] = useState<ScheduledAction | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<ScheduledAction | null>(
    null,
  )
  const [openMenu, setOpenMenu] = useState<number | null>(null)

  const deleteMutation = useApiMutation<unknown, number>({
    mutationFn: (id) =>
      apiFetch(`/api/scheduled-actions/${id}`, { method: "DELETE" }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"]],
    successMessage: "Schedule deleted",
    onSuccess: () => setConfirmDelete(null),
  })

  const columns: ColumnDef<ScheduledAction>[] = [
    {
      key: "action",
      label: "Action",
      accessor: (r) => r.action_name ?? r.action_key,
      cell: (r) => (
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-slate-100">
              {r.action_name ?? r.action_key}
            </span>
            {r.action_key.startsWith("_builtin.") && (
              <Badge className="bg-blue-700/40 text-blue-300 border border-blue-700/60">
                built-in
              </Badge>
            )}
          </div>
          {r.pack_name && r.pack_name !== "_builtin" && (
            <span className="text-xs text-slate-500">from {r.pack_name}</span>
          )}
        </div>
      ),
      defaultWidth: 220,
      filter: { type: "text" },
    },
    ...(showTarget
      ? [
          {
            key: "target",
            label: "Target",
            accessor: (r: ScheduledAction) => r.target_kind,
            cell: (r: ScheduledAction) => <TargetCell row={r} />,
            defaultWidth: 180,
            filter: {
              type: "enum" as const,
              options: [
                { label: "Host", value: "host" },
                { label: "Group", value: "group" },
                { label: "Fleet", value: "fleet" },
              ],
            },
          },
        ]
      : []),
    {
      key: "schedule",
      label: "Schedule",
      accessor: (r) => r.schedule_cron ?? "",
      cell: (r) =>
        r.schedule_cron ? (
          <div>
            <span className="font-mono text-xs text-slate-300">
              {r.schedule_cron}
            </span>
            <div className="text-xs text-slate-500">
              {cronToHuman(r.schedule_cron)}
            </div>
          </div>
        ) : (
          <span className="text-slate-600">—</span>
        ),
      defaultWidth: 200,
      filter: { type: "text", placeholder: "e.g. 0 3" },
    },
    {
      key: "enabled",
      label: "Enabled",
      accessor: (r) => r.enabled,
      cell: (r) => <EnabledBadge enabled={r.enabled} />,
      defaultWidth: 100,
      filter: { type: "boolean", trueLabel: "Enabled", falseLabel: "Disabled" },
    },
    {
      key: "last_run",
      label: "Last run",
      accessor: (r) => r.last_run?.status ?? "",
      cell: (r) =>
        r.last_run ? (
          <div className="flex items-center gap-2">
            <RunStatusBadge status={r.last_run.status} />
            <span className="text-xs text-slate-400">
              {formatRelativeTime(
                r.last_run.started_at ?? r.last_run.created_at,
              )}
            </span>
          </div>
        ) : (
          <span className="text-slate-600">Never</span>
        ),
      defaultWidth: 180,
      filter: {
        type: "enum",
        options: [
          { label: "Succeeded", value: "succeeded" },
          { label: "Failed", value: "failed" },
          { label: "Partial", value: "partial" },
          { label: "Running", value: "running" },
          { label: "Queued", value: "queued" },
        ],
      },
    },
    {
      key: "options",
      label: "Options",
      cell: (r) =>
        r.destructive ? (
          <div className="flex items-center gap-1.5">
            <Tooltip content={`Snapshot: ${r.snapshot_enabled ? "on" : "off"}`}>
              <CameraIcon
                className={`h-3.5 w-3.5 ${
                  r.snapshot_enabled ? "text-green-400" : "text-slate-700"
                }`}
              />
            </Tooltip>
            <Tooltip content={`Verify: ${r.verify_enabled ? "on" : "off"}`}>
              <EyeIcon
                className={`h-3.5 w-3.5 ${
                  r.verify_enabled ? "text-green-400" : "text-slate-700"
                }`}
              />
            </Tooltip>
            <Tooltip content={`Auto-rollback: ${r.auto_rollback ? "on" : "off"}`}>
              <RotateCcwIcon
                className={`h-3.5 w-3.5 ${
                  r.auto_rollback ? "text-green-400" : "text-slate-700"
                }`}
              />
            </Tooltip>
          </div>
        ) : (
          <span className="text-slate-600">—</span>
        ),
      defaultWidth: 90,
      sortable: false,
    },
    {
      key: "row_actions",
      label: "",
      cell: (r) => (
        <RowActions
          row={r}
          isOpen={openMenu === r.id}
          onOpenChange={(v) => setOpenMenu(v ? r.id : null)}
          onEdit={() => {
            setOpenMenu(null)
            setEditing(r)
          }}
          onViewRuns={() => {
            setOpenMenu(null)
            setHistoryFor(r)
          }}
          onDelete={() => {
            setOpenMenu(null)
            setConfirmDelete(r)
          }}
        />
      ),
      defaultWidth: 60,
      resizable: false,
      sortable: false,
      align: "right",
    },
  ]

  return (
    <>
      <DataTable<ScheduledAction>
        tableId={tableId}
        columns={columns}
        data={rows}
        getRowKey={(r) => r.id}
        rowClassName={(r) =>
          r.last_run?.status ? STATUS_BORDER[r.last_run.status] ?? "" : ""
        }
        emptyMessage={
          emptyState ?? (
            <div className="py-8 text-center text-sm text-slate-400">
              No schedules yet.
            </div>
          )
        }
      />

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
          title="Delete schedule?"
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

function RowActions({
  row,
  isOpen,
  onOpenChange,
  onEdit,
  onViewRuns,
  onDelete,
}: {
  row: ScheduledAction
  isOpen: boolean
  onOpenChange: (v: boolean) => void
  onEdit: () => void
  onViewRuns: () => void
  onDelete: () => void
}) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })

  const computePos = useCallback(() => {
    const btn = btnRef.current
    const menu = menuRef.current
    if (!btn) return
    const rect = btn.getBoundingClientRect()
    const menuW = menu?.offsetWidth ?? 176
    const menuH = menu?.offsetHeight ?? 120
    const left = rect.right - menuW < 8 ? 8 : rect.right - menuW
    const top = rect.bottom + menuH > window.innerHeight
      ? Math.max(8, rect.top - menuH - 4)
      : rect.bottom + 4
    setMenuPos({ top, left })
  }, [])

  useEffect(() => {
    if (!isOpen) return
    const raf = requestAnimationFrame(computePos)
    window.addEventListener("resize", computePos)
    window.addEventListener("scroll", computePos, true)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", computePos)
      window.removeEventListener("scroll", computePos, true)
    }
  }, [isOpen, computePos])

  useEffect(() => {
    if (!isOpen) return
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Node
      if (menuRef.current?.contains(target) || btnRef.current?.contains(target)) return
      onOpenChange(false)
    }
    document.addEventListener("mousedown", onMouseDown)
    return () => document.removeEventListener("mousedown", onMouseDown)
  }, [isOpen, onOpenChange])

  return (
    <div
      className="inline-block"
      data-testid="scheduled-action-row"
      data-action-key={row.action_key}
    >
      <Button
        ref={btnRef}
        variant="ghost"
        size="sm"
        onClick={() => onOpenChange(!isOpen)}
        aria-label="Row actions"
      >
        <MoreHorizontalIcon className="h-4 w-4" />
      </Button>
      {isOpen && (
        <div
          ref={menuRef}
          style={{ position: "fixed", top: menuPos.top, left: menuPos.left }}
          className="z-50 w-44 rounded-md border border-slate-700 bg-slate-800 shadow-lg"
        >
          <MenuItem onClick={onEdit}>Edit</MenuItem>
          <MenuItem onClick={onViewRuns}>View runs</MenuItem>
          <MenuItem
            onClick={onDelete}
            className="text-red-400 hover:bg-red-950"
            icon={<TrashIcon className="h-3.5 w-3.5" />}
          >
            Delete
          </MenuItem>
        </div>
      )}
    </div>
  )
}

function MenuItem({
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
  const base =
    "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-slate-700"
  return (
    <button
      type="button"
      onClick={onClick}
      className={`${base} ${className ?? ""}`}
    >
      {icon}
      {children}
    </button>
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
