import { plural } from "@/lib/fleet"

/** `POST /api/scans/{id}/run` — the run is queued, not done. */
export interface ScanRunQueued {
  queued: boolean
  task_id: string
}

/** `GET /api/scans/{id}/runs/{task_id}` — the outcome of that one run.
 *  `pending` covers both waiting for a scan slot and running. */
export interface ScanRunStatus {
  status: "pending" | "done" | "skipped" | "error"
  hosts_added: number
  hosts_pending: number
  error: string | null
}

export type ScanRunAnnouncement =
  | { tone: "info"; message: string }
  | { tone: "error"; message: string }
  | { tone: "success"; message: string; added: number; pending: number }

/** What to tell the operator about a finished run, or null while it is
 *  still pending. */
export function scanRunAnnouncement(name: string, run: ScanRunStatus): ScanRunAnnouncement | null {
  switch (run.status) {
    case "pending":
      return null
    case "error":
      return { tone: "error", message: `Scan "${name}" failed: ${run.error ?? "unknown error"}` }
    case "skipped":
      return { tone: "info", message: `Scan "${name}" did not run: the schedule was disabled or deleted` }
  }
  const { hosts_added: added, hosts_pending: pending } = run
  if (added === 0 && pending === 0) return { tone: "info", message: `Scan "${name}" finished: no new hosts` }
  const message =
    added > 0
      ? `Scan "${name}" added ${plural(added, "host")}${pending > 0 ? `; ${pending} awaiting review` : ""}`
      : `Scan "${name}" found ${plural(pending, "host")} awaiting review`
  return { tone: "success", message, added, pending }
}
