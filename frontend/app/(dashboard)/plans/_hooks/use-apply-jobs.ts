"use client"

import { useCallback, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch, ApiError } from "@/lib/api"
import { pollWhileVisible } from "@/lib/poll"
import { useSyncTray, type SyncJob } from "@/lib/sync-tray"
import { showError } from "@/lib/toast"
import { TERMINAL, type HostJob, type HostPlan, type Phase } from "../model"

/**
 * Applying a plan: one coalesced sync job per selected host, polled until
 * every job has finished, which moves the screen to `done`.
 */
export function useApplyJobs(phase: Phase, setPhase: (phase: Phase) => void) {
  const queryClient = useQueryClient()
  const { registerSync } = useSyncTray()
  const [jobs, setJobs] = useState<Record<number, HostJob>>({})
  const [jobIds, setJobIds] = useState<Record<number, number>>({})

  const clearJobs = useCallback(() => {
    setJobs({})
    setJobIds({})
  }, [])

  /** Queue one job per target host; `label` names the run in the sync tray. */
  const queueJobs = async (targets: HostPlan[], moduleFilter: string[] | null, label: string) => {
    const ids: Record<number, number> = {}
    const initial: typeof jobs = {}
    await Promise.all(
      targets.map(async (p) => {
        try {
          const r = await apiFetch<{ job_id: number; status: string }>(`/api/sync/hosts/${p.host.id}/bulk`, { method: "POST", json: { module_filter: moduleFilter } })
          ids[p.host.id] = r.job_id
          initial[p.host.id] = { status: "queued" }
        } catch (e) {
          initial[p.host.id] = { status: "rejected", error: e instanceof ApiError ? e.message : "could not queue the sync" }
        }
      }),
    )
    setJobIds(ids)
    setJobs(initial)
    const jobList = Object.values(ids)
    if (jobList.length) registerSync({ label, jobIds: jobList })
    else {
      showError("No sync could be queued")
      setPhase("done")
    }
  }

  useEffect(() => {
    if (phase !== "applying") return
    const ids = Object.values(jobIds)
    if (ids.length === 0) return
    let stop = false
    const tick = async () => {
      try {
        const rows = await apiFetch<SyncJob[]>(`/api/sync/jobs?ids=${ids.join(",")}`)
        if (stop) return
        setJobs((prev) => {
          const next = { ...prev }
          for (const [hostId, jobId] of Object.entries(jobIds)) {
            const j = rows.find((r) => r.id === jobId)
            if (j) next[Number(hostId)] = j
          }
          return next
        })
        if (rows.length === ids.length && rows.every((r) => TERMINAL.has(r.status))) {
          setPhase("done")
          void queryClient.invalidateQueries({ queryKey: ["hosts"] })
          void queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
        }
      } catch {
        /* transient — try again next tick */
      }
    }
    const stopPolling = pollWhileVisible(() => void tick(), 2000)
    return () => {
      stop = true
      stopPolling()
    }
  }, [phase, jobIds, queryClient, setPhase])

  const jobStatus = (hostId: number) => {
    const j = jobs[hostId]
    if (!j) return null
    return j.status
  }

  return { jobs, jobStatus, queueJobs, clearJobs }
}
