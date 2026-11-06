"use client"

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { alertToItem, jobToItem, runStatus, sessionToItem, type ActivityKind, type FeedItem, type SyncJobRow } from "@/lib/activity"
import { actionLabel, durationMs, scheduleName } from "@/lib/overview"
import type { Tone } from "@/lib/fleet"
import type { ActionDefinition, AISession, AlertEvent, Host, HostGroup, HostRunEntry, ScheduledAction } from "@/lib/types"

/**
 * The reads behind a host's Overview tab. A host mostly gets its scheduled
 * work through a group, so its history comes from its own per-host rows
 * (`/api/actions/host-runs`), not from runs aimed at it.
 */

/** Every run that reached this host, newest first, with its own result. */
export function useHostRuns(hostId: number) {
  return useQuery<HostRunEntry[]>({
    queryKey: ["host-runs", hostId],
    queryFn: () => apiFetch<HostRunEntry[]>(`/api/actions/host-runs?host_id=${hostId}&limit=300`),
    refetchInterval: (q) => ((q.state.data as HostRunEntry[] | undefined)?.some((r) => !HOST_FINISHED.has(r.status)) ? 5_000 : 30_000),
  })
}

/** A host row's terminal statuses (a host row is never "partial"). */
export const HOST_FINISHED = new Set(["succeeded", "failed", "cancelled", "skipped"])

/** The colour of one host-run square: a rollback is its own colour, since
 *  the host ended where it started. */
export function hostRunTone(r: Pick<HostRunEntry, "status" | "rolled_back">): Tone {
  if (r.status === "succeeded") return "ok"
  if (r.status === "failed") return r.rolled_back ? "hold" : "danger"
  if (r.status === "cancelled" || r.status === "skipped") return "idle"
  return "sync"
}

export function hostRunWord(r: Pick<HostRunEntry, "status" | "rolled_back">): string {
  if (r.status === "failed" && r.rolled_back) return "rolled back"
  if (r.status === "succeeded") return "ok"
  return r.status
}

/** Why it failed here, in one line. */
export function hostRunReason(r: Pick<HostRunEntry, "reason" | "error_message" | "exit_code">): string {
  return r.reason ?? r.error_message ?? (r.exit_code != null ? `exit ${r.exit_code}` : "no reason recorded")
}

export interface HostSchedule {
  s: ScheduledAction
  /** How it reaches this host: "this host", "group web-dmz", "every host". */
  via: string
  viaGroupId: number | null
  /** This host's rows for the schedule's runs, newest first. */
  history: HostRunEntry[]
  /** The newest of those that finished. */
  last: HostRunEntry | null
  /** Failures in a row on this host, counted back from the newest finished. */
  streak: number
}

/**
 * Every schedule that reaches this host — aimed at it, at one of its
 * groups, or at the fleet — with this host's own results. Failing ones
 * first, then the next to fire.
 */
export function hostSchedules(schedules: ScheduledAction[], host: Pick<Host, "id" | "group_ids">, groups: Pick<HostGroup, "id" | "name">[], runs: HostRunEntry[]): HostSchedule[] {
  const groupName = new Map(groups.map((g) => [g.id, g.name]))
  const bySchedule = new Map<number, HostRunEntry[]>()
  for (const r of runs) {
    if (r.scheduled_action_id == null) continue
    const list = bySchedule.get(r.scheduled_action_id) ?? []
    list.push(r)
    bySchedule.set(r.scheduled_action_id, list)
  }
  const out: HostSchedule[] = []
  for (const s of schedules) {
    let via: string | null = null
    let viaGroupId: number | null = null
    if (s.target_kind === "host" && s.target_id === host.id) via = "this host"
    else if (s.target_kind === "group" && s.target_id != null && host.group_ids.includes(s.target_id)) {
      viaGroupId = s.target_id
      via = `group ${groupName.get(s.target_id) ?? s.target_name ?? `#${s.target_id}`}`
    } else if (s.target_kind === "fleet") via = "every host"
    if (!via) continue
    const history = bySchedule.get(s.id) ?? []
    const finished = history.filter((r) => HOST_FINISHED.has(r.status))
    let streak = 0
    for (const r of finished) {
      if (r.status !== "failed") break
      streak++
    }
    out.push({
      s,
      via,
      viaGroupId,
      history,
      last: finished[0] ?? null,
      streak,
    })
  }
  const next = (h: HostSchedule) => (h.s.enabled && h.s.next_run_at ? new Date(h.s.next_run_at).getTime() : Infinity)
  return out.sort((a, b) => Number(b.streak > 0) - Number(a.streak > 0) || Number(b.s.enabled) - Number(a.s.enabled) || next(a) - next(b))
}

const BUILTIN_KIND: Record<string, ActivityKind> = {
  "_builtin.drift_check": "drift",
  "_builtin.collect_state": "collect",
}

/** One host row in the activity stream's shape. */
export function hostRunToItem(r: HostRunEntry, hostId: number, schedule: ScheduledAction | undefined, actionName?: string): FeedItem {
  const kind: ActivityKind = r.scheduled_action_id ? "schedule" : (BUILTIN_KIND[r.action_key] ?? "action")
  const status = runStatus(r.status)
  const name = schedule ? scheduleName(schedule) : (actionName ?? actionLabel(r.action_key))
  return {
    id: `host-run:${r.host_run_id}`,
    at: r.created_at,
    kind,
    status,
    title: r.target_kind === "host" ? name : `${name} via ${r.target_label}`,
    detail: r.status === "failed" ? `${r.rolled_back ? "rolled back to the snapshot · " : ""}${hostRunReason(r)}` : "",
    who: r.scheduled_action_id ? "schedule" : r.alert_event_id ? "alert" : r.triggered_by_user_id ? "user" : "system",
    href: `/hosts/${hostId}/actions/runs/${r.action_run_id}`,
    hostId,
    userId: r.triggered_by_user_id,
    durationMs: durationMs(r),
  }
}

/**
 * Runs, applies, alerts and assistant sessions on this host, newest first.
 * A routine built-in check that worked (a drift check or a collection every
 * few hours) stays out: it would bury everything else.
 */
export function useHostFeed(hostId: number, hostname: string, runs: HostRunEntry[] | undefined, schedules: ScheduledAction[] | undefined) {
  const jobs = useQuery<SyncJobRow[]>({
    queryKey: ["sync-jobs", "host", hostId],
    queryFn: () => apiFetch<SyncJobRow[]>(`/api/sync/jobs?host_id=${hostId}&limit=20`),
    refetchInterval: 30_000,
  })
  const alerts = useHostAlerts(hostId)
  const sessions = useQuery<AISession[]>({
    queryKey: ["ai-sessions", "host", hostId],
    queryFn: () => apiFetch<AISession[]>(`/api/ai/sessions?host_id=${hostId}&limit=20&view=all`),
    refetchInterval: 60_000,
    retry: false,
  })
  // Same key as the Actions page, so a manual run reads "Upgrade Kubernetes
  // cluster" there and here rather than its key.
  const catalog = useQuery<ActionDefinition[]>({ queryKey: ["actions"], queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/") })

  const items = useMemo<FeedItem[]>(() => {
    const byId = new Map((schedules ?? []).map((s) => [s.id, s]))
    const names = new Map((catalog.data ?? []).map((a) => [a.key, a.name]))
    const out: FeedItem[] = []
    for (const r of runs ?? []) {
      if (r.action_key in BUILTIN_KIND && r.status === "succeeded") continue
      out.push(hostRunToItem(r, hostId, r.scheduled_action_id != null ? byId.get(r.scheduled_action_id) : undefined, names.get(r.action_key)))
    }
    for (const j of jobs.data ?? []) {
      // The page is the host's own, so "firewall → media" says the host twice.
      const it = jobToItem(j, hostname)
      out.push({ ...it, title: it.title.replace(` → ${hostname}`, "") })
    }
    for (const al of alerts.data ?? []) out.push({ ...alertToItem(al, undefined), title: al.alertname })
    for (const s of sessions.data ?? []) out.push(sessionToItem(s))
    return out.sort((a, b) => b.at.localeCompare(a.at))
  }, [runs, jobs.data, alerts.data, sessions.data, catalog.data, hostId, hostname, schedules])

  return { items, isLoading: jobs.isLoading }
}

/** Alerts LabDog tied to this host, newest first. */
export function useHostAlerts(hostId: number) {
  return useQuery<AlertEvent[]>({
    queryKey: ["alerts", "host", hostId],
    queryFn: () => apiFetch<AlertEvent[]>(`/api/alerts?host_id=${hostId}&limit=20`),
    refetchInterval: 60_000,
    retry: false,
  })
}
