"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useSyncTray, operationCounts, operationDone, type SyncJob, type SyncOperation } from "@/lib/sync-tray"
import { statusDef } from "@/lib/fleet"
import { JOB_STATUS, def } from "@/lib/status"
import { syncModuleLabel } from "@/lib/modules"
import { Dot, Meter, Panel, type Tone } from "@/components/ld"
import type { Host } from "@/lib/types"

function opTone(op: SyncOperation, jobs: Record<number, SyncJob>): Tone {
  const { failed, running, pending } = operationCounts(op, jobs)
  if (failed > 0) return "danger"
  if (running > 0 || pending > 0) return "sync"
  return "ok"
}

function HostRow({ jid, job, hostName }: { jid: number; job?: SyncJob; hostName: (id: number) => string }) {
  const [open, setOpen] = useState(false)
  const modules = job?.modules ?? []
  const canExpand = modules.length > 0
  const st = def(JOB_STATUS, job?.status ?? "pending")

  return (
    <div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!canExpand}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-1.5 border-0 bg-transparent p-0 text-left disabled:cursor-default"
        >
          {canExpand && <span className="tt text-[8px] text-text-faint">{open ? "▼" : "▶"}</span>}
          <span className="mono trunc text-[11.5px] text-text-2" title={job ? hostName(job.host_id) : ""}>
            {job ? hostName(job.host_id) : `job ${jid}`}
          </span>
        </button>
        <span
          className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium"
          style={{ color: `var(--${st.tone})` }}
          title={job?.pending_reason || job?.error_message || undefined}
        >
          <Dot tone={st.tone} pulse={st.tone === "sync"} />
          {st.label}
        </span>
      </div>
      {open && canExpand && (
        <div className="ml-4 mt-0.5 flex flex-col gap-0.5">
          {modules.map((m) => {
            const ms = m.sync_status === "running" ? { label: "running", tone: "sync" as Tone } : statusDef(m.sync_status)
            return (
              <div key={m.module_type} className="flex items-center justify-between gap-2">
                <span className="trunc text-[11px] text-text-3">{syncModuleLabel(m.module_type)}</span>
                <span className="text-[11px]" style={{ color: `var(--${ms.tone})` }} title={m.error_message ?? ""}>
                  {ms.label}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function OperationRow({
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
  const summary = failed > 0 ? `${done}/${total} done · ${failed} failed` : running > 0 ? `${done}/${total} done · ${running} running` : `${done}/${total} done`

  return (
    <div className="flex flex-col gap-1.5 border-b border-line-faint px-[11px] py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <button type="button" className="min-w-0 flex-1 border-0 bg-transparent p-0 text-left" onClick={() => setOpen((v) => !v)}>
          <span className="trunc block text-xs font-medium text-text" title={op.label}>
            {op.label}
          </span>
        </button>
        {finished ? (
          <button type="button" className="btn btn-sm btn-ghost" onClick={onDismiss} aria-label="Dismiss">
            dismiss
          </button>
        ) : (
          <Dot tone="sync" pulse />
        )}
      </div>
      <Meter pct={pct} tone={opTone(op, jobs)} label={summary} value={`${pct}%`} h={3} />
      {open && (
        <div className="scroll mt-1 flex max-h-56 flex-col gap-1">
          {op.jobIds.map((jid) => (
            <HostRow key={jid} jid={jid} job={jobs[jid]} hostName={hostName} />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The floating sync tray: every apply the operator started, its
 * progress, and a per-host drill-down. Bottom-right, above the toaster,
 * below any modal.
 */
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
    <div className="fixed bottom-4 right-4 z-40 w-[360px] max-w-[calc(100vw-2rem)]">
      <Panel
        className="shadow-ld"
        title="sync activity"
        meta={activeCount > 0 ? `${activeCount} running` : `${finishedCount} finished`}
        actions={
          <>
            {finishedCount > 0 && (
              <button type="button" className="btn btn-sm btn-ghost" onClick={clearFinished}>
                clear finished
              </button>
            )}
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setCollapsed((v) => !v)} aria-label={collapsed ? "Expand" : "Collapse"}>
              {collapsed ? "▲" : "▼"}
            </button>
          </>
        }
      >
        {!collapsed && (
          <div className="scroll max-h-[60vh]">
            {operations.map((op) => (
              <OperationRow key={op.id} op={op} jobs={jobs} hostName={hostName} onDismiss={() => dismiss(op.id)} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}
