"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess, showError, showInfo } from "@/lib/toast"
import { scanRunAnnouncement, type ScanRunQueued, type ScanRunStatus } from "@/lib/scan-run"
import { plural, shortAgo } from "@/lib/fleet"
import { Confirm, Table, Tag, Toolbar } from "@/components/ld"
import { ScanConfigDialog } from "@/components/scans/scan-config-dialog"
import type { ScanConfig } from "@/lib/types"

function formatSchedule(scan: ScanConfig): string {
  if (scan.interval_minutes != null) {
    const m = scan.interval_minutes
    if (m % (60 * 24) === 0) return `every ${m / (60 * 24)}d`
    if (m % 60 === 0) return `every ${m / 60}h`
    return `every ${m} min`
  }
  if (scan.cron_expression) return scan.cron_expression
  return "—"
}

/** A triggered run is polled every 2 s; after two minutes (a large sweep, or
 *  a wait for one of the four scan slots) the operator is told it is still
 *  running and polling slows down. After half an hour the watch gives up:
 *  Celery reports a lost task as pending for ever. */
const RUN_WATCH_POLL_MS = 2000
const RUN_WATCH_SLOW_POLL_MS = 10_000
const RUN_WATCH_SLOW_AFTER_MS = 120_000
const RUN_WATCH_GIVE_UP_MS = 30 * 60_000

interface RunWatch {
  taskId: string
  configId: number
  name: string
}

function runStatus(scan: ScanConfig): "ok" | "error" | "running" | "never" {
  if (scan.last_run_status === "running") return "running"
  if (!scan.last_run_at) return "never"
  if (scan.last_run_status === "error") return "error"
  return "ok"
}

/** Polls one "run now" by its task id and announces the outcome in a toast.
 *  The schedule's last-run fields can't be used for this: they don't say
 *  which run wrote them, and a run that fails like the previous one leaves
 *  them unchanged. Renders nothing. */
function ScanRunWatcher({ run, onDone }: { run: RunWatch; onDone: (taskId: string) => void }) {
  const queryClient = useQueryClient()
  const router = useRouter()
  const [slow, setSlow] = useState(false)
  const { data } = useQuery<ScanRunStatus>({
    queryKey: ["scan-run", run.taskId],
    queryFn: () => apiFetch<ScanRunStatus>(`/api/scans/${run.configId}/runs/${run.taskId}`),
    refetchInterval: (q) =>
      q.state.data && q.state.data.status !== "pending" ? false : slow ? RUN_WATCH_SLOW_POLL_MS : RUN_WATCH_POLL_MS,
  })

  // Timers, not the poll result, so they fire even while every poll comes
  // back unchanged.
  useEffect(() => {
    const slowTimer = setTimeout(() => {
      setSlow(true)
      showInfo(`Scan "${run.name}" is still running; you'll get a toast when it finishes`)
    }, RUN_WATCH_SLOW_AFTER_MS)
    const giveUpTimer = setTimeout(() => {
      showInfo(`Stopped waiting for scan "${run.name}"; the last-run column shows its result`)
      onDone(run.taskId)
    }, RUN_WATCH_GIVE_UP_MS)
    return () => {
      clearTimeout(slowTimer)
      clearTimeout(giveUpTimer)
    }
  }, [run.taskId, run.name, onDone])

  useEffect(() => {
    if (!data) return
    const announcement = scanRunAnnouncement(run.name, data)
    if (!announcement) return
    onDone(run.taskId)
    // The list's last-run column, and whatever the run added or queued.
    queryClient.invalidateQueries({ queryKey: ["scans"], exact: true })
    if (announcement.tone === "error") {
      showError(announcement.message)
      return
    }
    if (announcement.tone === "info") {
      showInfo(announcement.message)
      return
    }
    const { added, pending } = announcement
    const review = { label: "Review", onClick: () => router.push(`/discovery?tab=pending&scan=${run.configId}`) }
    showSuccess(announcement.message, {
      duration: 10_000,
      action: added > 0 ? { label: "View hosts", onClick: () => router.push("/hosts") } : review,
      cancel: added > 0 && pending > 0 ? review : undefined,
    })
    if (pending > 0) {
      for (const key of ["pending-summary", "pending", "pending-hosts"]) {
        queryClient.invalidateQueries({ queryKey: ["scans", key] })
      }
      queryClient.invalidateQueries({ queryKey: ["scans", run.configId] })
    }
    if (added > 0) {
      queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
      queryClient.invalidateQueries({ queryKey: ["hosts"] })
    }
  }, [data, run, onDone, router, queryClient])

  return null
}

/** The recurring scan-schedule list, embedded in the Discovery screen's
 *  Scan schedules tab (no route of its own). */
export default function ScansPage() {
  // "run now" clicks not yet reported back, one per queued task.
  const [runs, setRuns] = useState<RunWatch[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingScan, setEditingScan] = useState<ScanConfig | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ScanConfig | null>(null)

  const { data: scans, isLoading, error } = useQuery<ScanConfig[]>({
    queryKey: ["scans"],
    queryFn: () => apiFetch<ScanConfig[]>("/api/scans"),
    refetchInterval: 10000,
  })

  const finishRun = useCallback((taskId: string) => setRuns((rs) => rs.filter((r) => r.taskId !== taskId)), [])

  const toggleMutation = useApiMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => apiFetch(`/api/scans/${id}`, { method: "PUT", body: JSON.stringify({ enabled }) }),
    invalidateKeys: [["scans"]],
  })
  const deleteMutation = useApiMutation({
    mutationFn: (id: number) => apiFetch(`/api/scans/${id}`, { method: "DELETE" }),
    invalidateKeys: [["scans"]],
    successMessage: "Scan config deleted",
    onSuccess: () => setDeleteTarget(null),
  })

  async function handleRun(scan: ScanConfig) {
    try {
      const { task_id } = await apiFetch<ScanRunQueued>(`/api/scans/${scan.id}/run`, { method: "POST" })
      showSuccess(`Run triggered for "${scan.name}"`)
      setRuns((rs) => [...rs, { taskId: task_id, configId: scan.id, name: scan.name }])
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to trigger run")
    }
  }

  function openCreate() {
    setEditingScan(null)
    setDialogOpen(true)
  }
  function openEdit(scan: ScanConfig) {
    setEditingScan(scan)
    setDialogOpen(true)
  }

  const rows = scans ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar actions={<button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>add scan schedule</button>}>
        <span className="tt">{plural(rows.length, "schedule")} · recurring network scans</span>
      </Toolbar>

      {error && <span className="p-3.5 text-[11.5px] text-danger">Failed to load scan schedules</span>}

      <Table<ScanConfig>
        cols={[
          { k: "name", label: "name", w: "minmax(130px,1fr)", sortable: false, cell: (r) => <span className="font-medium text-text">{r.name}</span> },
          { k: "cidrs", label: "cidrs", w: "minmax(120px,1fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px] text-text-2">{r.cidrs.join(", ") || "—"}</span> },
          { k: "schedule", label: "schedule", w: "110px", sortable: false, cell: (r) => <span className="mono text-[11px] text-text-2">{formatSchedule(r)}</span> },
          { k: "mode", label: "mode", w: "90px", sortable: false, cell: (r) => <Tag tone={r.auto_add ? "ok" : undefined}>{r.auto_add ? "auto" : "pending"}</Tag> },
          {
            k: "last_run", label: "last run", w: "minmax(100px,1fr)", sortable: false,
            cell: (r) => {
              const s = runStatus(r)
              const tone = s === "ok" ? "ok" : s === "error" ? "danger" : s === "running" ? "sync" : undefined
              return (
                <span className="flex flex-col gap-0.5">
                  <Tag tone={tone}>{s === "running" ? "running…" : s}</Tag>
                  {s !== "never" && r.last_run_at && <span className="text-[10px] text-text-faint">{shortAgo(r.last_run_at)} ago</span>}
                </span>
              )
            },
          },
          { k: "enabled", label: "enabled", w: "70px", sortable: false, cell: (r) => <input type="checkbox" checked={r.enabled} disabled={toggleMutation.isPending} onChange={() => toggleMutation.mutate({ id: r.id, enabled: !r.enabled })} style={{ accentColor: "var(--accent)" }} aria-label={`${r.enabled ? "disable" : "enable"} ${r.name}`} /> },
          {
            k: "actions", label: "", w: "180px", right: true, sortable: false,
            cell: (r) => (
              <span className="flex flex-wrap justify-end gap-0.5">
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(r)}>edit</button>
                <button type="button" className="btn btn-sm btn-ghost" disabled={!r.enabled} title={r.enabled ? undefined : "Enable the schedule to run it"} onClick={() => handleRun(r)}>run now</button>
                {(r.last_run_hosts_pending ?? 0) > 0 && <Link href={`/discovery?tab=pending&scan=${r.id}`} className="btn btn-sm btn-ghost hover:no-underline">pending</Link>}
                <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setDeleteTarget(r)}>delete</button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(r) => r.id}
        loading={isLoading}
        empty="No scan schedules yet. Add one to automatically discover hosts on your network."
      />

      {runs.map((run) => <ScanRunWatcher key={run.taskId} run={run} onDone={finishRun} />)}

      {dialogOpen && (
        <ScanConfigDialog
          open={dialogOpen}
          onOpenChange={(open) => { setDialogOpen(open); if (!open) setEditingScan(null) }}
          config={editingScan ?? undefined}
        />
      )}

      {deleteTarget && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          title="Delete scan schedule"
          description={`Delete "${deleteTarget.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
        />
      )}
    </div>
  )
}
