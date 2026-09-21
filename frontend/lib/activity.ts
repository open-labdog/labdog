"use client"

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { syncModuleLabel } from "@/lib/modules"
import type { SyncJob } from "@/lib/sync-tray"
import type { ActionRun, Host } from "@/lib/types"

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
  who: "schedule" | "user" | "system"
  /** Where the full object lives; null when it has no page of its own. */
  href: string | null
  hostId: number | null
}

interface SyncJobRow extends SyncJob {
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

function jobToItem(j: SyncJobRow, hostname: string): ActivityItem {
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
  return {
    id: `run:${r.id}`,
    at: r.created_at,
    kind,
    status,
    title: `${name} — ${r.target_label}`,
    detail: r.error_message ?? (status === "queued" ? (r.pending_reason ?? "queued") : status === "running" ? "running" : ""),
    who: r.scheduled_action_id ? "schedule" : r.triggered_by_user_id ? "user" : "system",
    href: `/actions/runs/${r.id}`,
    hostId: r.host_id,
  }
}

export function useActivityStream(limit = 60, refetchInterval = 30_000) {
  const jobs = useQuery<SyncJobRow[]>({
    queryKey: ["sync-jobs", "recent", limit],
    queryFn: () => apiFetch<SyncJobRow[]>(`/api/sync/jobs?limit=${limit}`),
    refetchInterval,
  })
  const runs = useQuery<ActionRun[]>({
    queryKey: ["action-runs", "recent", limit],
    queryFn: () => apiFetch<ActionRun[]>(`/api/actions/runs?limit=${Math.min(limit, 100)}`),
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
export function failuresFirst(items: ActivityItem[]): ActivityItem[] {
  return [...items].sort((a, b) => Number(b.status === "failed") - Number(a.status === "failed"))
}

/** Items created within the last `hours`. */
export function withinHours(items: ActivityItem[], hours: number): ActivityItem[] {
  const cutoff = Date.now() - hours * 3_600_000
  return items.filter((i) => new Date(i.at).getTime() >= cutoff)
}
