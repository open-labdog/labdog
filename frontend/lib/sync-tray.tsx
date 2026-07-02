"use client"

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"

export interface ModuleSubStatus {
  module_type: string
  sync_status: string
  error_message: string | null
}

// Mirror of backend SyncJobResponse (the subset the tray reads).
export interface SyncJob {
  id: number
  host_id: number
  group_id: number | null
  status: "pending" | "running" | "success" | "failed" | "cancelled"
  error_message: string | null
  pending_reason: string | null
  module_type: string
  modules?: ModuleSubStatus[]
}

// One user-initiated sync (a host apply, a group per-module apply, a Sync All)
// tracked as the set of per-host jobs it triggered.
export interface SyncOperation {
  id: string
  label: string
  jobIds: number[]
  startedAt: number
  notifiedDone: boolean
  dismissed: boolean
}

const TERMINAL = new Set(["success", "failed", "cancelled"])

interface SyncTrayValue {
  operations: SyncOperation[]
  jobs: Record<number, SyncJob>
  registerSync: (op: { label: string; jobIds: number[] }) => void
  dismiss: (opId: string) => void
  clearFinished: () => void
}

const SyncTrayContext = createContext<SyncTrayValue | null>(null)

/** Whether all of an operation's jobs have reached a terminal state. */
export function operationDone(op: SyncOperation, jobs: Record<number, SyncJob>): boolean {
  return op.jobIds.every((jid) => {
    const j = jobs[jid]
    return j != null && TERMINAL.has(j.status)
  })
}

export function operationCounts(op: SyncOperation, jobs: Record<number, SyncJob>) {
  let done = 0
  let failed = 0
  let running = 0
  let pending = 0
  for (const jid of op.jobIds) {
    const s = jobs[jid]?.status
    if (s === "success") done += 1
    else if (s === "failed" || s === "cancelled") {
      failed += 1
      done += 1
    } else if (s === "running") running += 1
    else pending += 1 // pending or not-yet-polled
  }
  return { total: op.jobIds.length, done, failed, running, pending }
}

export function SyncTrayProvider({ children }: { children: ReactNode }) {
  const [operations, setOperations] = useState<SyncOperation[]>([])
  const [jobs, setJobs] = useState<Record<number, SyncJob>>({})
  // Latest snapshots for use inside the poll interval without re-subscribing.
  // Kept in refs updated via effects (never mutated during render).
  const jobsRef = useRef(jobs)
  const opsRef = useRef(operations)
  useEffect(() => {
    jobsRef.current = jobs
  }, [jobs])
  useEffect(() => {
    opsRef.current = operations
  }, [operations])

  const registerSync = useCallback((op: { label: string; jobIds: number[] }) => {
    if (op.jobIds.length === 0) {
      // Nothing was triggered (e.g. every host already had a job in flight).
      showSuccess(`${op.label}: already in progress`)
      return
    }
    setOperations((prev) => [
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        label: op.label,
        jobIds: op.jobIds,
        startedAt: Date.now(),
        notifiedDone: false,
        dismissed: false,
      },
      ...prev,
    ])
  }, [])

  const dismiss = useCallback((opId: string) => {
    setOperations((prev) => prev.filter((o) => o.id !== opId))
  }, [])

  const clearFinished = useCallback(() => {
    setOperations((prev) => prev.filter((o) => !operationDone(o, jobsRef.current)))
  }, [])

  // Poll the union of not-yet-terminal job ids across live operations.
  useEffect(() => {
    const poll = async () => {
      const active = opsRef.current.filter((o) => !operationDone(o, jobsRef.current))
      const ids = Array.from(new Set(active.flatMap((o) => o.jobIds)))
      if (ids.length === 0) return
      try {
        const fetched = await apiFetch<SyncJob[]>(`/api/sync/jobs?ids=${ids.join(",")}`)
        setJobs((prev) => {
          const next = { ...prev }
          for (const j of fetched) next[j.id] = j
          return next
        })
        // Fire a completion toast once per operation, when it first finishes.
        setOperations((prev) => {
          const jobMap: Record<number, SyncJob> = { ...jobsRef.current }
          for (const j of fetched) jobMap[j.id] = j
          return prev.map((o) => {
            if (o.notifiedDone || !operationDone(o, jobMap)) return o
            const { total, failed } = operationCounts(o, jobMap)
            if (failed > 0) {
              showError(`${o.label}: ${failed} of ${total} host${total === 1 ? "" : "s"} failed`)
            } else {
              showSuccess(`${o.label}: ${total} host${total === 1 ? "" : "s"} synced`)
            }
            return { ...o, notifiedDone: true }
          })
        })
      } catch {
        // Transient poll failure — keep the last known state, retry next tick.
      }
    }

    const anyActive = operations.some((o) => !operationDone(o, jobsRef.current))
    if (!anyActive) return
    poll()
    const interval = setInterval(poll, 3000)
    return () => clearInterval(interval)
  }, [operations])

  return (
    <SyncTrayContext.Provider value={{ operations, jobs, registerSync, dismiss, clearFinished }}>
      {children}
    </SyncTrayContext.Provider>
  )
}

export function useSyncTray(): SyncTrayValue {
  const ctx = useContext(SyncTrayContext)
  if (!ctx) throw new Error("useSyncTray must be used within a SyncTrayProvider")
  return ctx
}
