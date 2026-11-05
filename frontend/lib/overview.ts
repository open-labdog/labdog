"use client"

import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { Tone } from "@/lib/fleet"
import type { ActionRun, Host, ScheduledAction, ScheduledActionRunSummary, StuckOperations } from "@/lib/types"

/**
 * Shared reads and arithmetic for the Overview tabs. The queries share
 * their keys with the rest of the app (`["scheduled-actions"]` is the
 * shell's too), so opening a tab refetches nothing the shell already has.
 */

const HOUR = 3_600_000

export function useSchedules() {
  return useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions"],
    queryFn: () => apiFetch<ScheduledAction[]>("/api/scheduled-actions"),
    refetchInterval: 60_000,
  })
}

export function useStuckOperations(enabled = true) {
  return useQuery<StuckOperations>({
    queryKey: ["dashboard", "stuck-operations"],
    queryFn: () => apiFetch<StuckOperations>("/api/dashboard/stuck-operations"),
    refetchInterval: 30_000,
    retry: false,
    enabled,
  })
}

/** Failed and partial runs of the last `days` days, newest first, with
 *  their host tallies and first failure. */
export function useFailedRuns(days = 7, enabled = true) {
  return useQuery<ActionRun[]>({
    queryKey: ["action-runs", "failed", days],
    queryFn: () => {
      const since = new Date(Date.now() - days * 24 * HOUR).toISOString()
      return apiFetch<ActionRun[]>(`/api/actions/runs?status=failed,partial&since=${encodeURIComponent(since)}&limit=200`)
    },
    refetchInterval: 30_000,
    retry: false,
    enabled,
  })
}

/** Every run a schedule started in the last 7 days, for the success rate. */
export function useScheduledRuns(days = 7) {
  return useQuery<ActionRun[]>({
    queryKey: ["action-runs", "scheduled", days],
    queryFn: () => {
      const since = new Date(Date.now() - days * 24 * HOUR).toISOString()
      return apiFetch<ActionRun[]>(`/api/actions/runs?scheduled=true&since=${encodeURIComponent(since)}&limit=500`)
    },
    refetchInterval: 60_000,
    retry: false,
  })
}

/* ── runs ───────────────────────────────────────────────────────── */

export const FINISHED = new Set(["succeeded", "completed", "failed", "partial", "cancelled"])
export const BAD = new Set(["failed", "partial"])

/** The tone a run's outcome is painted in, in a history strip. */
export function outcomeTone(status: string): Tone {
  if (status === "succeeded" || status === "completed") return "ok"
  if (status === "failed") return "danger"
  if (status === "partial") return "warn"
  if (status === "running" || status === "queued" || status === "pending") return "sync"
  return "idle"
}

/** How long a finished run took, in ms; null while it runs or when it never started. */
export function durationMs(r: Pick<ScheduledActionRunSummary, "started_at" | "finished_at">): number | null {
  if (!r.started_at || !r.finished_at) return null
  const ms = new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()
  return Number.isFinite(ms) && ms >= 0 ? ms : null
}

/** "18s", "7m 12s", "2h 40m". */
export function durationLabel(ms: number | null | undefined): string {
  if (ms == null) return "—"
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return s % 60 && m < 10 ? `${m}m ${s % 60}s` : `${m}m`
  const h = Math.floor(m / 60)
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`
}

/** "17 ok · 1 failed" from a run's host tally; empty for a one-host run. */
export function tallyLabel(counts: Record<string, number> | undefined): string {
  if (!counts) return ""
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  if (total <= 1) return ""
  const parts: string[] = []
  const ok = (counts.succeeded ?? 0) + (counts.completed ?? 0)
  if (ok) parts.push(`${ok} ok`)
  if (counts.failed) parts.push(`${counts.failed} failed`)
  const going = (counts.running ?? 0) + (counts.queued ?? 0) + (counts.pending ?? 0)
  if (going) parts.push(`${going} to go`)
  const other = (counts.skipped ?? 0) + (counts.cancelled ?? 0)
  if (other) parts.push(`${other} skipped`)
  return parts.join(" · ")
}

/** Leading failed or partial runs, newest first: "failed 3 times in a row". */
export function failStreak(recent: ScheduledActionRunSummary[] | undefined): number {
  let n = 0
  for (const r of recent ?? []) {
    if (!FINISHED.has(r.status)) continue
    if (!BAD.has(r.status)) break
    n++
  }
  return n
}

/** The latest finished run of a schedule. */
export function lastFinished(recent: ScheduledActionRunSummary[] | undefined): ScheduledActionRunSummary | null {
  return (recent ?? []).find((r) => FINISHED.has(r.status)) ?? null
}

export function avgDuration(recent: ScheduledActionRunSummary[] | undefined): number | null {
  const ds = (recent ?? []).map(durationMs).filter((d): d is number => d !== null)
  return ds.length ? ds.reduce((a, b) => a + b, 0) / ds.length : null
}

/* ── schedules ──────────────────────────────────────────────────── */

/** A schedule's display name: the manifest's, else its key. */
export function scheduleName(s: Pick<ScheduledAction, "action_name" | "action_key">): string {
  return s.action_name ?? s.action_key
}

/** An action key as words where it is a built-in: `_builtin.collect_state`
 *  → "collect state". Pack keys are already readable. */
export function actionLabel(key: string): string {
  return key.startsWith("_builtin.") ? key.slice("_builtin.".length).replace(/_/g, " ") : key
}

/** A schedule whose action the registry no longer has (its pack was
 *  disabled or removed). The scheduler skips it every tick and logs a
 *  warning; nothing else says so. */
export function isOrphaned(s: ScheduledAction): boolean {
  return s.action_name == null && s.destructive == null
}

export function scheduleTarget(s: ScheduledAction): string {
  return s.target_kind === "fleet" ? "all hosts" : `${s.target_kind === "group" ? "" : "host "}${s.target_name ?? `#${s.target_id}`}`
}

/** The hosts a schedule reaches today. */
export function reach(s: Pick<ScheduledAction, "target_kind" | "target_id">, hosts: Host[]): Host[] {
  if (s.target_kind === "fleet") return hosts
  if (s.target_kind === "host") return hosts.filter((h) => h.id === s.target_id)
  return hosts.filter((h) => s.target_id != null && h.group_ids.includes(s.target_id))
}

export interface Fire {
  at: number
  schedule: ScheduledAction
  hosts: number
}

/** Every enabled schedule's fire times between now and `hours` ahead. A
 *  schedule whose action is gone is left out: the scheduler skips it. */
export function fires(schedules: ScheduledAction[], hosts: Host[], hours: number, now: number): Fire[] {
  const end = now + hours * HOUR
  const out: Fire[] = []
  for (const s of schedules) {
    if (!s.enabled || isOrphaned(s)) continue
    const n = reach(s, hosts).length
    for (const t of s.upcoming_runs ?? []) {
      const at = new Date(t).getTime()
      if (at >= now && at <= end) out.push({ at, schedule: s, hosts: n })
    }
  }
  return out.sort((a, b) => a.at - b.at)
}

export interface Overlap {
  /** The first time it happens. */
  at: number
  /** Every time it happens in the window, for the timeline. */
  times: number[]
  /** The schedule that fires first; the other waits for it on shared hosts. */
  first: ScheduledAction
  second: ScheduledAction
  shared: string[]
  /** How long the first one usually takes; null without history. */
  wait: number | null
}

/** How long a run is assumed to hold its hosts without any history. */
const ASSUMED_RUN = 30 * 60_000

/**
 * Two schedules that reach a common host while the first one is usually
 * still running there: the second fires before the first's average
 * duration (30 minutes without history) is up. The host queue runs one
 * operation at a time, so the later one waits. Nothing breaks, but the
 * wait is usually a surprise. One entry per pair, with every time it
 * happens.
 */
export function overlaps(schedules: ScheduledAction[], hosts: Host[], now: number, hours = 168): Overlap[] {
  const list = fires(schedules, hosts, hours, now)
  const reachIds = new Map(schedules.map((s) => [s.id, new Set(reach(s, hosts).map((h) => h.id))]))
  const name = new Map(hosts.map((h) => [h.id, h.hostname]))
  const byPair = new Map<string, Overlap>()
  for (let i = 0; i < list.length; i++) {
    const a = list[i].schedule
    const wait = avgDuration(a.recent_runs)
    const busy = Math.max(wait ?? ASSUMED_RUN, 60_000)
    for (let j = i + 1; j < list.length && list[j].at - list[i].at < busy; j++) {
      const b = list[j].schedule
      if (a.id === b.id) continue
      const key = `${a.id}:${b.id}`
      const known = byPair.get(key)
      if (known) {
        known.times.push(list[i].at)
        continue
      }
      const ra = reachIds.get(a.id)!
      const shared = [...reachIds.get(b.id)!].filter((id) => ra.has(id))
      if (shared.length === 0) continue
      byPair.set(key, { at: list[i].at, times: [list[i].at], first: a, second: b, shared: shared.map((id) => name.get(id) ?? `#${id}`), wait })
    }
  }
  return [...byPair.values()].sort((x, y) => x.at - y.at)
}

/* ── time buckets ───────────────────────────────────────────────── */

export interface Bucket {
  start: number
  ok: number
  failed: number
  running: number
}

/** Count items per bucket, oldest bucket first, ending at the current one. */
export function buckets<T>(
  items: T[],
  at: (t: T) => string,
  status: (t: T) => "ok" | "failed" | "running" | null,
  hours: number,
  size: number,
  now: number,
): Bucket[] {
  const width = size * HOUR
  const end = Math.ceil(now / width) * width
  const n = Math.ceil(hours / size)
  const out: Bucket[] = Array.from({ length: n }, (_, i) => ({ start: end - (n - i) * width, ok: 0, failed: 0, running: 0 }))
  for (const it of items) {
    const s = status(it)
    if (!s) continue
    const i = Math.floor((new Date(at(it)).getTime() - out[0].start) / width)
    if (i >= 0 && i < n) out[i][s]++
  }
  return out
}

/** "04:00", or "Sun 04:00" when it is not today. */
export function clock(ms: number, withDay = false): string {
  const d = new Date(ms)
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
  return withDay ? `${d.toLocaleDateString([], { weekday: "short" })} ${hm}` : hm
}
