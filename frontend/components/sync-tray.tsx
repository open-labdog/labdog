"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useSyncTray, operationCounts, operationDone, type SyncJob, type SyncOperation } from "@/lib/sync-tray"
import { useActionTray, runActive, runHostCounts, type TrackedRun } from "@/lib/action-tray"
import { statusDef } from "@/lib/fleet"
import { JOB_STATUS, RUN_STATUS, def } from "@/lib/status"
import { syncModuleLabel } from "@/lib/modules"
import { actionLabel, durationLabel } from "@/lib/overview"
import { Dot, Meter, Panel, type Tone } from "@/components/ld"
import type { ActionDefinition, ActionRun, Host } from "@/lib/types"

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

function runTone(r: ActionRun): Tone {
  if (runActive(r)) return r.status === "pending" ? "warn" : "sync"
  return def(RUN_STATUS, r.status).tone
}

/** Ticks once a second while mounted, for elapsed-time labels. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

function RunHosts({ run }: { run: ActionRun }) {
  // The list endpoint leaves host_runs empty; read them only while the
  // row is open, and keep reading while the run is still going.
  const active = runActive(run)
  const { data, refetch } = useQuery<ActionRun>({
    queryKey: ["action-run", run.id, "tray"],
    queryFn: () => apiFetch<ActionRun>(`/api/actions/runs/${run.id}`),
    refetchInterval: active ? 4000 : false,
  })
  // BUG-133: polling stops the moment the list query sees the run finish,
  // which leaves the rows at whatever the last poll saw. A group-dispatch
  // run finishes all its hosts within the same instant, so that poll had
  // them all still running. Fetch once more when the run goes inactive.
  const wasActive = useRef(active)
  useEffect(() => {
    if (wasActive.current && !active) void refetch()
    wasActive.current = active
  }, [active, refetch])
  const hostRuns = data?.host_runs ?? []
  return (
    <div className="scroll mt-1 flex max-h-56 flex-col gap-0.5">
      {hostRuns.length === 0 && <span className="text-[11px] text-text-faint">{data ? "no hosts yet" : "loading…"}</span>}
      {hostRuns.map((hr) => {
        const st = def(RUN_STATUS, hr.status)
        return (
          <div key={hr.id} className="flex items-center gap-2">
            <span className="mono trunc min-w-0 flex-1 text-[11.5px] text-text-2" title={hr.hostname ?? ""}>
              {hr.hostname ?? `host ${hr.host_id}`}
            </span>
            <span
              className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium"
              style={{ color: `var(--${st.tone})` }}
              title={hr.pending_reason || hr.error_message || undefined}
            >
              <Dot tone={st.tone} pulse={st.tone === "sync"} />
              {st.label}
            </span>
          </div>
        )
      })}
      <Link href={`/actions/runs/${run.id}`} className="mt-1 text-[11px] text-ld-accent hover:no-underline">
        open run →
      </Link>
    </div>
  )
}

function RunRow({ t, name, onDismiss }: { t: TrackedRun; name: string; onDismiss: () => void }) {
  const [open, setOpen] = useState(false)
  const { run } = t
  const active = runActive(run)
  const now = useNow(active)
  const st = def(RUN_STATUS, run.status)
  const { total, done, failed, running } = runHostCounts(run)
  const since = run.started_at ?? run.created_at
  const elapsed = durationLabel((active ? now : new Date(run.finished_at ?? now).getTime()) - new Date(since).getTime())
  const origin = run.scheduled_action_id != null ? "scheduled" : run.alert_event_id != null ? "alert" : null

  let summary: string
  if (run.status === "pending") summary = run.pending_reason ? `waiting: ${run.pending_reason}` : "waiting for a busy host"
  else if (run.status === "queued") summary = "queued"
  else if (total > 1) summary = `${done}/${total} hosts${failed ? ` · ${failed} failed` : running ? ` · ${running} running` : ""}`
  else summary = st.label
  const pct = total > 0 ? Math.round((done / total) * 100) : active ? 0 : 100

  return (
    <div className="flex flex-col gap-1.5 border-b border-line-faint px-[11px] py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <button type="button" className="min-w-0 flex-1 border-0 bg-transparent p-0 text-left" onClick={() => setOpen((v) => !v)}>
          <span className="trunc block text-xs font-medium text-text" title={`${name} on ${run.target_label}`}>
            {name} <span className="font-normal text-text-3">on {run.target_label}</span>
          </span>
        </button>
        {origin && <span className="tt text-[9px] text-text-faint">{origin}</span>}
        {active ? (
          <Dot tone={runTone(run)} pulse={run.status === "running"} />
        ) : (
          <button type="button" className="btn btn-sm btn-ghost" onClick={onDismiss} aria-label="Dismiss">
            dismiss
          </button>
        )}
      </div>
      <Meter pct={pct} tone={runTone(run)} label={summary} value={elapsed} h={3} />
      {open && <RunHosts run={run} />}
    </div>
  )
}

/**
 * The floating activity tray: every apply the operator started, every
 * action run in progress anywhere in the fleet, their progress, and a
 * per-host drill-down. Bottom-right, above the toaster, below any modal.
 */
export function SyncTray() {
  const { operations, jobs, dismiss, clearFinished } = useSyncTray()
  const actionTray = useActionTray()
  const runs = actionTray.runs
  const [collapsed, setCollapsed] = useState(false)
  // Opened from the background indicator: the card stays up with nothing
  // of the operator's own in it until they close it again.
  const [pinned, setPinned] = useState(false)
  const [bgOpen, setBgOpen] = useState(false)

  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    enabled: operations.length > 0,
  })
  const hostName = (id: number) => hosts?.find((h) => h.id === id)?.hostname ?? `host ${id}`

  const { data: actions } = useQuery<ActionDefinition[]>({
    queryKey: ["actions"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    enabled: runs.length > 0,
    staleTime: 5 * 60_000,
  })
  const actionName = (key: string) => actions?.find((a) => a.key === key)?.name ?? actionLabel(key)

  // The operator's own runs pop the card up; everything else (schedules,
  // alerts, other operators) is "background" and never does on its own.
  // On a fleet with many scheduled runs a day the card would otherwise
  // hardly ever go away.
  const ownRuns = runs.filter((t) => t.own)
  const bgRuns = runs.filter((t) => !t.own)
  const bgActive = bgRuns.filter((t) => t.finishedAt == null).length
  const showCard = operations.length > 0 || ownRuns.length > 0 || pinned

  if (!showCard) {
    if (bgActive === 0) return null
    return (
      <div className="fixed bottom-4 right-4 z-40">
        <button
          type="button"
          className="btn btn-sm inline-flex items-center gap-1.5 shadow-ld"
          onClick={() => {
            setPinned(true)
            setBgOpen(true)
            setCollapsed(false)
          }}
          title="Action runs started by schedules, alerts or other operators"
          data-testid="tray-background-indicator"
        >
          <Dot tone="sync" pulse />
          {bgActive} background run{bgActive === 1 ? "" : "s"}
        </button>
      </div>
    )
  }

  const activeCount =
    operations.filter((o) => !operationDone(o, jobs)).length + ownRuns.filter((t) => t.finishedAt == null).length
  const finishedCount = operations.length + ownRuns.length - activeCount
  const meta =
    activeCount > 0 ? `${activeCount} running` : finishedCount > 0 ? `${finishedCount} finished` : `${bgActive} in background`

  return (
    <div className="fixed bottom-4 right-4 z-40 w-[360px] max-w-[calc(100vw-2rem)]">
      <Panel
        className="shadow-ld"
        title="activity"
        meta={meta}
        actions={
          <>
            {finishedCount > 0 && (
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => {
                  clearFinished()
                  actionTray.clearFinished()
                }}
              >
                clear finished
              </button>
            )}
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setCollapsed((v) => !v)} aria-label={collapsed ? "Expand" : "Collapse"}>
              {collapsed ? "▲" : "▼"}
            </button>
            {pinned && (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPinned(false)} aria-label="Close">
                ✕
              </button>
            )}
          </>
        }
      >
        {!collapsed && (
          <div className="scroll max-h-[60vh]">
            {operations.map((op) => (
              <OperationRow key={op.id} op={op} jobs={jobs} hostName={hostName} onDismiss={() => dismiss(op.id)} />
            ))}
            {ownRuns.map((t) => (
              <RunRow key={t.run.id} t={t} name={actionName(t.run.action_key)} onDismiss={() => actionTray.dismiss(t.run.id)} />
            ))}
            {bgRuns.length > 0 && (
              <div className="border-b border-line-faint last:border-b-0">
                <button
                  type="button"
                  className="flex w-full items-center gap-2 border-0 bg-transparent px-[11px] py-2 text-left"
                  onClick={() => setBgOpen((v) => !v)}
                  data-testid="tray-background-row"
                >
                  <span className="tt text-[8px] text-text-faint">{bgOpen ? "▼" : "▶"}</span>
                  <span className="flex-1 text-xs text-text-2">
                    {bgActive > 0 ? `${bgActive} background run${bgActive === 1 ? "" : "s"}` : "background runs finished"}
                  </span>
                  <span className="tt text-[9px] text-text-faint">scheduled · alerts · others</span>
                  {bgActive > 0 && <Dot tone="sync" pulse />}
                </button>
                {bgOpen &&
                  bgRuns.map((t) => (
                    <RunRow key={t.run.id} t={t} name={actionName(t.run.action_key)} onDismiss={() => actionTray.dismiss(t.run.id)} />
                  ))}
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  )
}
