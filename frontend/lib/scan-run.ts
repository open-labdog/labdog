import type { ScanConfig } from "@/lib/types"

/** What a scan schedule's last-run fields held when "run now" was clicked. */
export type ScanBaseline = Pick<ScanConfig, "last_run_at" | "last_run_status" | "last_run_error">

export type ScanRunOutcome =
  | { kind: "running" }
  | { kind: "done"; added: number; pending: number }
  | { kind: "error"; message: string }

export function scanBaseline(scan: ScanConfig): ScanBaseline {
  return { last_run_at: scan.last_run_at, last_run_status: scan.last_run_status, last_run_error: scan.last_run_error }
}

/**
 * Has the run that was triggered against `before` finished, and how?
 *
 * The runner only commits its counters when it finishes, so a changed
 * `last_run_at` is the completion signal for a successful run. A failed
 * run rolls that back and records `status=error` plus the message in a
 * separate session, so it shows up as an error that was not there before.
 */
export function scanRunOutcome(before: ScanBaseline, now: ScanConfig): ScanRunOutcome {
  if (now.last_run_status === "running") return { kind: "running" }
  const errored = now.last_run_status === "error"
  if (errored && (before.last_run_status !== "error" || before.last_run_error !== now.last_run_error)) {
    return { kind: "error", message: now.last_run_error ?? "unknown error" }
  }
  if (now.last_run_at !== before.last_run_at) {
    return errored
      ? { kind: "error", message: now.last_run_error ?? "unknown error" }
      : { kind: "done", added: now.last_run_hosts_added ?? 0, pending: now.last_run_hosts_pending ?? 0 }
  }
  return { kind: "running" }
}
