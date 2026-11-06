"use client"

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { syncModuleLabel } from "@/lib/modules"
import type { SyncJob } from "@/lib/sync-tray"
import { durationMs, tallyLabel } from "@/lib/overview"
import { useNow } from "@/hooks/use-now"
import type { ActionRun, AISession, AlertEvent, Host } from "@/lib/types"

/**
 * One activity stream. Applies (sync jobs), action runs and scheduled
 * runs used to be three lists with three detail routes rendering the same
 * object; here they are one shape, newest first, so "what is happening to
 * my fleet" has one answer.
 */
export type ActivityKind = "apply" | "action" | "schedule" | "collect" | "drift"
export type ActivityStatus = "ok" | "failed" | "running" | "queued" | "cancelled"

export interface ActivityItem {
  id: string
  /** ISO timestamp the stream sorts by — when the run was created. */
  at: string
  kind: ActivityKind
  status: ActivityStatus
  title: string
  detail: string
  who: "schedule" | "alert" | "user" | "system"
  /** Where the full object lives; null when it has no page of its own. */
  href: string | null
  hostId: number | null
  /** The user who started it, when one did. */
  userId?: number | null
  /** How long it took, once finished. */
  durationMs?: number | null
  /** "17 ok · 1 failed" for a run over more than one host. */
  tally?: string
  /** The action run behind the item, for Investigate with AI. */
  run?: ActionRun
}

/** Alerts and assistant sessions join the stream on the Overview's
 *  Activity tab; the Runs page lists runs only. */
export type FeedKind = ActivityKind | "alert" | "ai"
export interface FeedItem extends Omit<ActivityItem, "kind"> {
  kind: FeedKind
}

export interface SyncJobRow extends SyncJob {
  created_at: string
  started_at: string | null
  completed_at: string | null
  module_filter: string[] | null
  triggered_by_user_id: number | null
}

const JOB_STATUS: Record<string, ActivityStatus> = {
  success: "ok",
  failed: "failed",
  running: "running",
  pending: "queued",
  cancelled: "cancelled",
}

const RUN_STATUS: Record<string, ActivityStatus> = {
  succeeded: "ok",
  completed: "ok",
  failed: "failed",
  partial: "failed",
  running: "running",
  queued: "queued",
  pending: "queued",
  cancelled: "cancelled",
  skipped: "cancelled",
}

/** An action run's raw status folded into the stream's five words. */
export function runStatus(status: string): ActivityStatus {
  return RUN_STATUS[status] ?? "queued"
}

export function jobToItem(j: SyncJobRow, hostname: string): ActivityItem {
  const mods = j.module_filter?.length ? j.module_filter : j.module_type === "bulk" ? null : [j.module_type]
  const what = mods === null ? "all modules" : mods.map(syncModuleLabel).join(", ")
  const status = JOB_STATUS[j.status] ?? "queued"
  const changed = j.modules?.filter((m) => m.sync_status === "in_sync").length
  return {
    id: `job:${j.id}`,
    at: j.created_at,
    kind: "apply",
    status,
    title: `${what} → ${hostname}`,
    detail:
      j.error_message ??
      (status === "ok" && j.modules?.length ? `${changed} of ${j.modules.length} modules in sync` : status === "queued" ? (j.pending_reason ?? "waiting for the host") : status === "running" ? "ansible is running" : ""),
    who: j.triggered_by_user_id ? "user" : "system",
    href: null,
    hostId: j.host_id,
    userId: j.triggered_by_user_id,
    durationMs: durationMs({ started_at: j.started_at, finished_at: j.completed_at }),
  }
}

function runToItem(r: ActionRun): ActivityItem {
  const builtin = r.action_key.startsWith("_builtin.")
  const kind: ActivityKind = r.scheduled_action_id
    ? "schedule"
    : r.action_key === "_builtin.drift_check"
      ? "drift"
      : r.action_key === "_builtin.collect_state"
        ? "collect"
        : "action"
  const name = builtin ? r.action_key.slice("_builtin.".length).replace(/_/g, " ") : r.action_key
  const status = runStatus(r.status)
  const ff = r.first_failure
  // The failing host's own reason beats the run's "N of M hosts failed".
  const failure = ff ? `${ff.hostname}: ${ff.reason ?? ff.error_message ?? `exit ${ff.exit_code ?? "?"}`}` : null
  return {
    id: `run:${r.id}`,
    at: r.created_at,
    kind,
    status,
    title: `${name} — ${r.target_label}`,
    detail: failure ?? r.error_message ?? (status === "queued" ? (r.pending_reason ?? "queued") : status === "running" ? "running" : ""),
    who: r.scheduled_action_id ? "schedule" : r.alert_event_id ? "alert" : r.triggered_by_user_id ? "user" : "system",
    href: `/actions/runs/${r.id}`,
    hostId: r.host_id,
    userId: r.triggered_by_user_id,
    durationMs: durationMs(r),
    tally: tallyLabel(r.host_counts),
    run: r,
  }
}

/**
 * The newest `limit` sync jobs and action runs, merged. With `sinceHours`
 * the runs are those of that window instead (up to 500), for a range
 * longer than the newest few.
 */
export function useActivityStream(limit = 60, refetchInterval = 30_000, sinceHours?: number) {
  const jobs = useQuery<SyncJobRow[]>({
    queryKey: ["sync-jobs", "recent", limit],
    queryFn: () => apiFetch<SyncJobRow[]>(`/api/sync/jobs?limit=${limit}`),
    refetchInterval,
  })
  const runs = useQuery<ActionRun[]>({
    queryKey: sinceHours ? ["action-runs", "since", sinceHours] : ["action-runs", "recent", limit],
    queryFn: () =>
      sinceHours
        ? apiFetch<ActionRun[]>(`/api/actions/runs?since=${encodeURIComponent(new Date(Date.now() - sinceHours * 3_600_000).toISOString())}&limit=500`)
        : apiFetch<ActionRun[]>(`/api/actions/runs?limit=${Math.min(limit, 100)}`),
    refetchInterval,
  })
  const hosts = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })

  const items = useMemo(() => {
    const byId = new Map((hosts.data ?? []).map((h) => [h.id, h.hostname]))
    const out: ActivityItem[] = []
    for (const j of jobs.data ?? []) out.push(jobToItem(j, byId.get(j.host_id) ?? `host #${j.host_id}`))
    for (const r of runs.data ?? []) out.push(runToItem(r))
    return out.sort((a, b) => b.at.localeCompare(a.at))
  }, [jobs.data, runs.data, hosts.data])

  return {
    items,
    isLoading: jobs.isLoading || runs.isLoading,
    error: jobs.error ?? runs.error,
  }
}

/** Failures pinned to the top, otherwise newest first. */
export function failuresFirst<T extends Pick<ActivityItem, "status">>(items: T[]): T[] {
  return [...items].sort((a, b) => Number(b.status === "failed") - Number(a.status === "failed"))
}

/** Items created within the last `hours`. */
export function withinHours<T extends Pick<ActivityItem, "at">>(items: T[], hours: number): T[] {
  const cutoff = Date.now() - hours * 3_600_000
  return items.filter((i) => new Date(i.at).getTime() >= cutoff)
}

const SESSION_STATUS: Record<string, ActivityStatus> = {
  succeeded: "ok",
  failed: "failed",
  running: "running",
  waiting_approval: "running",
  queued: "queued",
  cancelled: "cancelled",
}

/**
 * Alerts that fired and assistant sessions that started in the last
 * `hours`, in the stream's shape. An alert is "running" while it fires.
 */
export function useFeedExtras(hours: number, refetchInterval = 30_000) {
  const alerts = useQuery<AlertEvent[]>({
    queryKey: ["alerts", "recent"],
    queryFn: () => apiFetch<AlertEvent[]>("/api/alerts?limit=200"),
    refetchInterval,
    retry: false,
  })
  const sessions = useQuery<AISession[]>({
    queryKey: ["ai-sessions", "recent"],
    queryFn: () => apiFetch<AISession[]>("/api/ai/sessions?limit=100&view=all"),
    refetchInterval,
    retry: false,
  })
  const hosts = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })
  const now = useNow()

  const items = useMemo<FeedItem[]>(() => {
    const cutoff = now - hours * 3_600_000
    const name = new Map((hosts.data ?? []).map((h) => [h.id, h.hostname]))
    const out: FeedItem[] = []
    for (const al of alerts.data ?? []) {
      if (new Date(al.starts_at).getTime() >= cutoff) out.push(alertToItem(al, al.host_id != null ? name.get(al.host_id) : undefined))
    }
    for (const s of sessions.data ?? []) {
      if (new Date(s.created_at).getTime() >= cutoff) out.push(sessionToItem(s))
    }
    return out
  }, [alerts.data, sessions.data, hosts.data, hours, now])

  return { items, isLoading: alerts.isLoading || sessions.isLoading }
}

/** An alert in the stream's shape; "running" while it fires. */
export function alertToItem(al: AlertEvent, host: string | undefined): FeedItem {
  const outcome = al.investigation_outcome
  return {
    id: `alert:${al.id}`,
    at: al.starts_at,
    kind: "alert",
    status: al.status === "firing" ? "running" : "ok",
    title: `${al.alertname}${host ? ` fired on ${host}` : " fired"}`,
    detail: `${al.status === "firing" ? "still firing" : `resolved after ${shortSpan(al.starts_at, al.ends_at)}`}${al.severity ? ` · ${al.severity}` : ""}${outcome ? ` · ${outcome.replace(/_/g, " ")}` : ""}`,
    who: "alert",
    href: al.investigation_session_id ? `/assistant?session=${al.investigation_session_id}` : "/alerts",
    hostId: al.host_id,
  }
}

/** An assistant session in the stream's shape. */
export function sessionToItem(s: AISession): FeedItem {
  const status = SESSION_STATUS[s.status] ?? "queued"
  return {
    id: `ai:${s.id}`,
    at: s.created_at,
    kind: "ai",
    status,
    title: s.title ?? s.mission.split("\n")[0].slice(0, 120),
    detail: s.error_message ?? `${s.command_count} command${s.command_count === 1 ? "" : "s"}${s.stopped_reason ? ` · stopped: ${s.stopped_reason}` : ""}`,
    who: s.investigates_action_run_id ? "system" : "user",
    href: `/assistant?session=${s.id}`,
    hostId: s.target_host_ids?.length === 1 ? s.target_host_ids[0] : null,
    durationMs: durationMs(s),
  }
}

export function shortSpan(from: string, to: string | null): string {
  if (!to) return "—"
  const m = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60_000)
  return m < 60 ? `${m}m` : m < 48 * 60 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`
}
