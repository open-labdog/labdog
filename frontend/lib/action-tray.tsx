"use client"

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { showError, showSuccess } from "@/lib/toast"
import { actionLabel } from "@/lib/overview"
import type { ActionRun } from "@/lib/types"

/** Run statuses that mean the run has not finished yet. */
export const ACTIVE_RUN_STATUSES = ["queued", "pending", "running"] as const
const ACTIVE = new Set<string>(ACTIVE_RUN_STATUSES)

/** Host statuses that mean that host's part of a run is over. */
const HOST_DONE = new Set(["succeeded", "failed", "skipped", "cancelled"])

/** Poll fast while something runs, slowly while the fleet is idle. The API
 *  allows 100 requests a minute per client; these two cost 15 and 4. */
const ACTIVE_POLL_MS = 4_000
const IDLE_POLL_MS = 15_000
/** A finished run nobody here started (a schedule, an alert, another
 *  operator) leaves the tray on its own after this long. The operator's
 *  own runs stay until dismissed, the same as their syncs. */
const FOREIGN_LINGER_MS = 60_000

export interface TrackedRun {
  run: ActionRun
  /** When the tray first saw the run finished. Null while it runs. */
  finishedAt: number | null
  /** Started by the signed-in operator by hand, not by a schedule. */
  own: boolean
}

export function runActive(r: ActionRun): boolean {
  return ACTIVE.has(r.status)
}

/** How many of a run's hosts are done, from `host_counts` (list endpoint)
 *  or `host_runs` (detail endpoint), whichever the row carries. */
export function runHostCounts(r: ActionRun) {
  const counts: Record<string, number> = { ...(r.host_counts ?? {}) }
  if (Object.keys(counts).length === 0) {
    for (const hr of r.host_runs ?? []) counts[hr.status] = (counts[hr.status] ?? 0) + 1
  }
  let total = 0
  let done = 0
  let failed = 0
  let running = 0
  for (const [status, n] of Object.entries(counts)) {
    total += n
    if (HOST_DONE.has(status)) done += n
    if (status === "failed") failed += n
    if (status === "running") running += n
  }
  return { total, done, failed, running }
}

interface ActionTrayValue {
  runs: TrackedRun[]
  /** Show a run straight away rather than at the next poll. */
  trackRun: (run: ActionRun) => void
  dismiss: (runId: number) => void
  clearFinished: () => void
}

const ActionTrayContext = createContext<ActionTrayValue | null>(null)

/**
 * Watches every action run in the fleet that is queued, waiting on a busy
 * host, or running — whoever or whatever started it — and keeps each one
 * until it finishes, so the activity tray can show it. Unlike syncs, whose
 * job ids only the page that started them knows, runs are discovered by
 * polling `/api/actions/runs?status=queued,pending,running`; a run that
 * drops out of that list is fetched once more for its final state.
 */
export function ActionTrayProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [tracked, setTracked] = useState<Record<number, TrackedRun>>({})
  const trackedRef = useRef(tracked)
  useEffect(() => {
    trackedRef.current = tracked
  }, [tracked])
  // Ids the operator dismissed: a run still running must not reappear on
  // the next poll just because it is still in the active list.
  const dismissedRef = useRef(new Set<number>())
  const kickRef = useRef<() => void>(() => {})

  const isOwn = useCallback(
    (r: ActionRun) => user != null && r.triggered_by_user_id === user.id && r.scheduled_action_id == null,
    [user],
  )

  useEffect(() => {
    if (!user) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false

    const schedule = (ms: number) => {
      if (timer) clearTimeout(timer)
      if (!cancelled) timer = setTimeout(tick, ms)
    }

    async function tick() {
      if (cancelled || inFlight) return
      if (typeof document !== "undefined" && document.hidden) {
        schedule(IDLE_POLL_MS)
        return
      }
      inFlight = true
      try {
        const active = await apiFetch<ActionRun[]>(`/api/actions/runs?status=${ACTIVE_RUN_STATUSES.join(",")}&limit=50`)
        const activeIds = new Set(active.map((r) => r.id))
        const vanished = Object.values(trackedRef.current)
          .filter((t) => t.finishedAt == null && !activeIds.has(t.run.id))
          .map((t) => t.run.id)
        const finals = await Promise.all(
          vanished.map((id) => apiFetch<ActionRun>(`/api/actions/runs/${id}`).catch(() => null)),
        )
        if (cancelled) return

        const now = Date.now()
        // Merge into whatever the state is *now*, not the snapshot taken
        // before the awaits, so a dismiss or a trackRun made meanwhile is
        // not overwritten. Pure, so a StrictMode double call is harmless.
        const merge = (prev: Record<number, TrackedRun>) => {
          const next: Record<number, TrackedRun> = { ...prev }
          for (const r of active) {
            if (dismissedRef.current.has(r.id)) continue
            next[r.id] = { run: r, finishedAt: null, own: prev[r.id]?.own || isOwn(r) }
          }
          vanished.forEach((id, i) => {
            const r = finals[i]
            if (!prev[id]) return
            if (r == null) {
              // Deleted, or no longer readable: nothing left to show.
              delete next[id]
            } else if (runActive(r)) {
              // Fell out of the list between the two reads; keep polling it.
              next[id] = { ...prev[id], run: r }
            } else {
              next[id] = { ...prev[id], run: r, finishedAt: prev[id].finishedAt ?? now }
            }
          })
          for (const [id, t] of Object.entries(next)) {
            if (t.finishedAt != null && !t.own && now - t.finishedAt > FOREIGN_LINGER_MS) delete next[Number(id)]
          }
          return next
        }
        const justFinished = vanished
          .map((id, i) => ({ t: trackedRef.current[id], r: finals[i] }))
          .filter((x): x is { t: TrackedRun; r: ActionRun } => x.t != null && x.r != null && !runActive(x.r))
          .map(({ t, r }) => ({ ...t, run: r }))
        const next = merge(trackedRef.current)
        trackedRef.current = next
        setTracked(merge)

        for (const t of justFinished) {
          if (!t.own) continue
          const label = `${actionLabel(t.run.action_key)} on ${t.run.target_label}`
          if (t.run.status === "succeeded" || t.run.status === "completed") showSuccess(`${label}: succeeded`)
          else if (t.run.status === "cancelled") showSuccess(`${label}: cancelled`)
          else showError(`${label}: ${t.run.status}`)
        }
        schedule(Object.values(next).some((t) => t.finishedAt == null) ? ACTIVE_POLL_MS : IDLE_POLL_MS)
      } catch {
        // Transient failure (or rate limit): keep what we have, back off.
        schedule(IDLE_POLL_MS)
      } finally {
        inFlight = false
      }
    }

    kickRef.current = () => schedule(1_000)
    const onVisible = () => {
      if (!document.hidden) schedule(0)
    }
    document.addEventListener("visibilitychange", onVisible)
    timer = setTimeout(tick, 0)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      document.removeEventListener("visibilitychange", onVisible)
      kickRef.current = () => {}
    }
  }, [user, isOwn])

  const trackRun = useCallback(
    (run: ActionRun) => {
      dismissedRef.current.delete(run.id)
      setTracked((prev) => ({ ...prev, [run.id]: { run, finishedAt: runActive(run) ? null : Date.now(), own: true } }))
      kickRef.current()
    },
    [],
  )

  const dismiss = useCallback((runId: number) => {
    dismissedRef.current.add(runId)
    setTracked((prev) => {
      const next = { ...prev }
      delete next[runId]
      return next
    })
  }, [])

  const clearFinished = useCallback(() => {
    setTracked((prev) => {
      const next: Record<number, TrackedRun> = {}
      for (const [id, t] of Object.entries(prev)) if (t.finishedAt == null) next[Number(id)] = t
      return next
    })
  }, [])

  // Signed out: show nothing, whatever is left over from the last session.
  const runs = (user ? Object.values(tracked) : []).sort((a, b) => b.run.created_at.localeCompare(a.run.created_at))

  return <ActionTrayContext.Provider value={{ runs, trackRun, dismiss, clearFinished }}>{children}</ActionTrayContext.Provider>
}

export function useActionTray(): ActionTrayValue {
  const ctx = useContext(ActionTrayContext)
  if (!ctx) throw new Error("useActionTray must be used within an ActionTrayProvider")
  return ctx
}
