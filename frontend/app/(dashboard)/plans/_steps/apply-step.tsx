"use client"

import { useRouter } from "next/navigation"
import { Dot, Panel, Status, toneVar, type Tone } from "@/components/ld"
import { realChanges, TERMINAL, type HostJob, type HostPlan } from "../model"

type JobStatus = (hostId: number) => HostJob["status"] | null

/** Step 3, while it runs: one line per selected host, as its job moves on. */
export function ApplyProgress({ targets, jobs, jobStatus }: { targets: HostPlan[]; jobs: Record<number, HostJob>; jobStatus: JobStatus }) {
  const router = useRouter()
  return (
    <Panel title="applying" meta={`${targets.filter((p) => TERMINAL.has(jobStatus(p.host.id) ?? "") || jobStatus(p.host.id) === "rejected").length} of ${targets.length} hosts finished`}>
      {targets.map((p) => {
        const s = jobStatus(p.host.id)
        const j = jobs[p.host.id]
        return (
          <div key={p.host.id} className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]">
            <button type="button" className="mono w-[120px] shrink-0 border-0 bg-transparent text-left text-xs text-text underline decoration-line-strong underline-offset-[3px]" onClick={() => router.push(`/hosts/${p.host.id}`)}>
              {p.host.hostname}
            </button>
            <Status s={s === "success" ? "in_sync" : s === "failed" || s === "rejected" || s === "cancelled" ? "error" : "pending"} />
            <span className="mono trunc flex-1 text-[11px] text-text-3">
              {s === "success" ? `${realChanges(p.diffs).length} change${realChanges(p.diffs).length === 1 ? "" : "s"} applied` : j && "error" in j ? j.error : j && "error_message" in j && j.error_message ? j.error_message : s === "running" ? "ansible is running…" : j && "pending_reason" in j && j.pending_reason ? j.pending_reason : "waiting for the host…"}
            </span>
          </div>
        )
      })}
    </Panel>
  )
}

/** Step 3, once every job has finished: the counts, each failure, and what was left out. */
export function ApplyResult({
  meta,
  counts,
  targets,
  jobs,
  jobStatus,
  onPlanRemaining,
}: {
  meta: string
  counts: { applied: number; failed: number; excluded: number; unchanged: number; skipped: number }
  targets: HostPlan[]
  jobs: Record<number, HostJob>
  jobStatus: JobStatus
  onPlanRemaining: () => void
}) {
  const router = useRouter()
  const { applied, failed, excluded, unchanged, skipped } = counts
  return (
    <Panel title="result" meta={meta}>
      <div className="flex flex-col gap-[11px] p-[13px]">
        <div className="flex flex-wrap gap-[18px]">
          {(
            [
              ["applied", applied, "ok"],
              ["failed", failed, failed ? "danger" : "idle"],
              ["excluded", excluded, excluded ? "warn" : "idle"],
              ["unchanged", unchanged, "idle"],
              ["skipped", skipped, "idle"],
            ] as [string, number, Tone][]
          ).map(([l, n, t]) => (
            <div key={l} className="flex flex-col gap-[3px]">
              <span className="tt">{l}</span>
              <span className="mono num text-xl font-semibold" style={{ color: toneVar(t) }}>
                {n}
              </span>
            </div>
          ))}
        </div>
        {targets
          .filter((p) => ["failed", "cancelled", "rejected"].includes(jobStatus(p.host.id) ?? ""))
          .map((p) => {
            const j = jobs[p.host.id]
            const msg = j && "error" in j ? j.error : j && "error_message" in j ? j.error_message : null
            return (
              <div key={p.host.id} className="rounded-r border border-danger bg-danger-soft p-2.5 text-xs text-text">
                <b className="mono">{p.host.hostname}</b> failed{msg ? <> — <span className="mono">{msg}</span></> : ""}. The host was left on its previous state; a failed play does not half-apply.
                <div className="mt-2 flex gap-[7px]">
                  <button type="button" className="btn btn-sm" onClick={() => router.push(`/assistant`)}>
                    Ask the assistant
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => router.push(`/hosts/${p.host.id}`)}>
                    Open {p.host.hostname}
                  </button>
                </div>
              </div>
            )
          })}
        {excluded > 0 && (
          <div className="flex flex-wrap items-center gap-[9px] rounded-r border border-line bg-surface-2 p-2.5 text-xs">
            <Dot tone="warn" />
            <span>
              {excluded} host{excluded === 1 ? "" : "s"} excluded from this run and still pending this change.
            </span>
            <button type="button" className="btn btn-sm ml-auto" onClick={onPlanRemaining}>
              Plan the remaining {excluded}
            </button>
          </div>
        )}
        <span className="text-[11.5px] text-text-3">Recorded in the audit trail with a per-module outcome for every host.</span>
      </div>
    </Panel>
  )
}
