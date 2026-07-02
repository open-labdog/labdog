"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronDown, ChevronUp, X } from "lucide-react"
import { apiFetch } from "@/lib/api"
import {
  useSyncTray,
  operationCounts,
  operationDone,
  type SyncJob,
  type SyncOperation,
} from "@/lib/sync-tray"
import { RunStatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import type { Host } from "@/lib/types"

// Backend SyncJob uses "success"; RunStatusBadge renders "succeeded"/"completed" green.
function badgeStatus(s: string): string {
  return s === "success" ? "succeeded" : s
}

function barColor(op: SyncOperation, jobs: Record<number, SyncJob>): string {
  const { failed, running, pending } = operationCounts(op, jobs)
  if (failed > 0) return "bg-red-500"
  if (running > 0 || pending > 0) return "bg-blue-500"
  return "bg-green-500"
}

function OperationCard({
  op,
  jobs,
  hostName,
  onDismiss,
}: {
  op: SyncOperation
  jobs: Record<number, SyncJob>
  hostName: (id: number) => string
  onDismiss: () => void
}) {
  const [open, setOpen] = useState(false)
  const { total, done, failed, running } = operationCounts(op, jobs)
  const finished = operationDone(op, jobs)
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  const summary = failed > 0 ? `${done}/${total} done · ${failed} failed` : `${done}/${total} done`

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex-1 min-w-0 text-left"
          onClick={() => setOpen((v) => !v)}
        >
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-white" title={op.label}>
              {op.label}
            </span>
            {open ? (
              <ChevronUp className="h-3.5 w-3.5 shrink-0 text-slate-400" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-400">
            {!finished && (
              <svg className="h-3 w-3 animate-spin text-blue-400" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
            )}
            <span>{running > 0 ? `${summary} · ${running} running` : summary}</span>
          </div>
        </button>
        {finished && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss"
            className="shrink-0 rounded p-1 text-slate-500 hover:text-white hover:bg-slate-800"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="h-1 w-full bg-slate-800">
        <div className={`h-full ${barColor(op, jobs)} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      {open && (
        <div className="max-h-56 overflow-y-auto border-t border-slate-800 p-2 space-y-1">
          {op.jobIds.map((jid) => {
            const j = jobs[jid]
            return (
              <div key={jid} className="flex items-center justify-between gap-2 px-1">
                <span className="truncate text-xs text-slate-300" title={j ? hostName(j.host_id) : ""}>
                  {j ? hostName(j.host_id) : `job ${jid}`}
                </span>
                <RunStatusBadge
                  status={badgeStatus(j?.status ?? "pending")}
                  reason={j?.pending_reason || j?.error_message || null}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function SyncTray() {
  const { operations, jobs, dismiss, clearFinished } = useSyncTray()
  const [collapsed, setCollapsed] = useState(false)

  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    enabled: operations.length > 0,
  })
  const hostName = (id: number) => hosts?.find((h) => h.id === id)?.hostname ?? `host ${id}`

  if (operations.length === 0) return null

  const activeCount = operations.filter((o) => !operationDone(o, jobs)).length
  const finishedCount = operations.length - activeCount

  return (
    // Sits above the bottom-right toaster; below dialogs (z-50).
    <div className="fixed bottom-4 right-4 z-40 w-[360px] max-w-[calc(100vw-2rem)]">
      <div className="rounded-lg border border-slate-700 bg-slate-950 shadow-xl">
        <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-800">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            Sync activity
            {activeCount > 0 && (
              <span className="rounded-full bg-blue-600 px-2 py-0.5 text-xs font-medium text-white">
                {activeCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {finishedCount > 0 && (
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={clearFinished}>
                Clear finished
              </Button>
            )}
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={collapsed ? "Expand" : "Collapse"}
              className="rounded p-1 text-slate-400 hover:text-white hover:bg-slate-800"
            >
              {collapsed ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
        </div>
        {!collapsed && (
          <div className="max-h-[60vh] overflow-y-auto p-2 space-y-2">
            {operations.map((op) => (
              <OperationCard
                key={op.id}
                op={op}
                jobs={jobs}
                hostName={hostName}
                onDismiss={() => dismiss(op.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
