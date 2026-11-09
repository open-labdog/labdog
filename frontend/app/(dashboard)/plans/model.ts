import type { Tone } from "@/components/ld"
import type { SyncJob } from "@/lib/sync-tray"
import type { DiffChange, Host, ModuleDiff } from "@/lib/types"

/** The plan screen's steps, in order: define → computing → review → applying → done. */
export type Phase = "define" | "computing" | "review" | "applying" | "done"

export interface HostPlan {
  host: Host
  diffs: ModuleDiff[] | null
  error: string | null
  /** Hosts without an SSH key cannot be reached; they are skipped, not failed. */
  skipped: string | null
}

/** What Apply knows about one host: queued, refused when queueing, or its sync job. */
export type HostJob = SyncJob | { status: "queued" } | { status: "rejected"; error: string }

export const OP: Record<DiffChange["op"], { s: string; tone: Tone; bg: string }> = {
  add: { s: "+", tone: "add", bg: "var(--add-bg)" },
  remove: { s: "−", tone: "del", bg: "var(--del-bg)" },
  update: { s: "~", tone: "warn", bg: "var(--warn-soft)" },
  unchanged: { s: "·", tone: "idle", bg: "transparent" },
}

export const TERMINAL = new Set(["success", "failed", "cancelled"])
export const SSH_RE = /\b(dport\s+22|port\s+22|:22\b|\b22\/tcp|ssh)\b/i

export function realChanges(diffs: ModuleDiff[] | null): DiffChange[] {
  return (diffs ?? []).flatMap((d) => d.changes.filter((c) => c.op !== "unchanged"))
}

/** Modules on this host that answered with an error inside a successful preview. */
export function moduleErrors(diffs: ModuleDiff[] | null): ModuleDiff[] {
  return (diffs ?? []).filter((d) => d.error)
}

/** The computed plan's hosts by outcome, and what the current selection would run. */
export interface PlanBreakdown {
  changing: HostPlan[]
  unchanged: HostPlan[]
  skipped: HostPlan[]
  /** Hosts whose preview failed, or with a module that did (BUG-175). A
   *  host with a module error and real changes elsewhere is also in
   *  `changing`, so the rest of its plan can still run. */
  errored: HostPlan[]
  /** The errored hosts with nothing to run: not in `changing`. */
  notPreviewed: HostPlan[]
  /** The changing hosts selected to run. */
  targets: HostPlan[]
  /** Changing hosts left out of the run. */
  excluded: number
  /** Every change the selected hosts would apply. */
  selChanges: DiffChange[]
  /** Every change across all changing hosts, selected or not. */
  changeCount: number
}

export function breakdown(plans: HostPlan[], sel: Set<number>): PlanBreakdown {
  const changing = plans.filter((p) => !p.skipped && !p.error && realChanges(p.diffs).length > 0)
  // A module error inside a 200 preview is not "no change": the module was
  // never compared. Counting only a failed request made such a host read
  // "no change" and pass the Reachability check (BUG-175).
  const unchanged = plans.filter(
    (p) => !p.skipped && !p.error && realChanges(p.diffs).length === 0 && moduleErrors(p.diffs).length === 0,
  )
  const skipped = plans.filter((p) => p.skipped)
  const errored = plans.filter((p) => !p.skipped && (p.error || moduleErrors(p.diffs).length > 0))
  const notPreviewed = errored.filter((p) => !changing.includes(p))
  const targets = changing.filter((p) => sel.has(p.host.id))
  const excluded = changing.length - targets.length
  const selChanges = targets.flatMap((p) => realChanges(p.diffs))
  const changeCount = changing.reduce((n, p) => n + realChanges(p.diffs).length, 0)
  return { changing, unchanged, skipped, errored, notPreviewed, targets, excluded, selChanges, changeCount }
}
