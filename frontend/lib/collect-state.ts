import { API_BASE, apiFetch } from "@/lib/api"
import type { ActionRun } from "@/lib/types"

/** Receipt from POST /api/hosts/{id}/collect-state (BUG-74). */
export interface CollectStateAccepted {
  run_id: number
  status: string
  /** True when an identical collection was already in flight and this
   *  request joined it instead of starting a second one. */
  already_running: boolean
}

export interface CollectStateResult {
  /** The finished run, or null if it was still going when polling gave
   *  up. The work carries on either way. */
  run: ActionRun | null
  /** Non-fatal notices the collection reported, e.g. a competing LabDog
   *  ruleset left in the firewall backend LabDog is not managing. */
  notices: string[]
}

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "timed_out", "error"])

/** How long to keep polling before giving up. Generous: the run waits
 *  its turn behind any sync already holding the host. */
const POLL_TIMEOUT_MS = 5 * 60 * 1000
const POLL_INTERVAL_MS = 1500

/**
 * Queue a state collection for one host and wait for it to finish.
 *
 * Collection used to happen inside the POST, which held a database
 * connection for the length of seven SSH collectors and took no host
 * lock (BUG-74). It is an action run now: the request returns at once
 * and the outcome is polled.
 */
export async function collectHostState(
  hostId: number,
  module?: string,
): Promise<CollectStateResult> {
  const query = module ? `?module=${encodeURIComponent(module)}` : ""
  const accepted = await apiFetch<CollectStateAccepted>(
    `/api/hosts/${hostId}/collect-state${query}`,
    { method: "POST" },
  )

  const deadline = Date.now() + POLL_TIMEOUT_MS
  while (Date.now() < deadline) {
    const run = await apiFetch<ActionRun>(`/api/actions/runs/${accepted.run_id}`)
    if (TERMINAL.has(run.status)) {
      return { run, notices: await fetchNotices(run) }
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
  }
  return { run: null, notices: [] }
}

/**
 * Queue a collection for one host without waiting for it.
 *
 * For fan-outs — the dashboard's "Check all" starts one per host — where
 * waiting on every run in the browser would be its own pile of requests.
 */
export async function queueHostStateCollection(hostId: number): Promise<CollectStateAccepted> {
  return apiFetch<CollectStateAccepted>(`/api/hosts/${hostId}/collect-state`, { method: "POST" })
}

/** Read the run's per-host output, where the collector's notices are. */
async function fetchNotices(run: ActionRun): Promise<string[]> {
  const texts = await Promise.all(
    run.host_runs.map(async (hr) => {
      try {
        const res = await fetch(
          `${API_BASE}/api/actions/runs/${run.id}/host-runs/${hr.id}/output`,
          { credentials: "include" },
        )
        return res.ok ? await res.text() : ""
      } catch {
        return ""
      }
    }),
  )
  return texts
    .flatMap((text) => text.split("\n"))
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}
