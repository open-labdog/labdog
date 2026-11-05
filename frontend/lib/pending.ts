"use client"

import { useCallback, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { shortAgo, untilLabel } from "@/lib/fleet"
import { useNow } from "@/hooks/use-now"
import { actionLabel, BAD, durationLabel, failStreak, scheduleName, tallyLabel, useFailedRuns, useSchedules, useStuckOperations } from "@/lib/overview"
import type { ActionRun, AIApprovalRequest, AlertEvent, Host, PendingHostFleet, StuckOperation } from "@/lib/types"

/**
 * Pending — one list, typed lanes: assistant sessions paused at an
 * approval gate, discovered hosts awaiting approval, schedules whose last
 * run failed, operations stuck holding their hosts, firing alerts, and
 * drift. "Pending" is the app's own word for this state, and unlike
 * Inbox it promises no messages, senders or read state.
 *
 * Mixed urgency is the cost of one list, so every item carries an expiry
 * and the default sort is soonest-to-expire, not most recent. Items that
 * never expire follow, worst first.
 *
 * The rail badge does not count from here (see use-shell-counts.ts): it
 * stays blocking-and-expiring only.
 */
export type PendingLane = "approvals" | "failed" | "stuck" | "alerts" | "drift"
export type PendingSeverity = "fail" | "block" | "warn" | "info"

export const LANES: PendingLane[] = ["approvals", "failed", "stuck", "alerts", "drift"]

const SEVERITY_RANK: Record<PendingSeverity, number> = { fail: 0, block: 1, warn: 2, info: 3 }

export interface PendingItem {
  id: string
  lane: PendingLane
  kind: string
  title: string
  detail: string
  /** "in 3h 12m", "firing", or "—" when it does not expire. */
  expires: string
  /** Sort key: epoch ms of expiry; Infinity for things that never expire. */
  expiresAt: number
  severity: PendingSeverity
  href: string
  /**
   * Whether hiding it from this browser's queue is allowed. Approval gates
   * are not: a hidden gate still blocks its session, and the only honest
   * way off the list is a decision.
   */
  dismissible: boolean
  /** The hosts it is about, for the by-host view. */
  hosts: string[]
  /** An approval's lifetime left, 0–1, for its countdown bar. */
  left?: number
  /** The primary button's word: "Review", "Open run". */
  open: string
  /** A failed run, for Investigate with AI. */
  run?: ActionRun
  /** A stuck operation, for Cancel. */
  stuck?: StuckOperation
}

const STORAGE_KEY = "labdog:pending-dismissed"

function readDismissed(): Record<string, number> {
  try {
    return JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, number>
  } catch {
    return {}
  }
}

export function usePendingQueue(enabled = true) {
  const q = { refetchInterval: 30_000, retry: false, enabled }
  const approvals = useQuery<AIApprovalRequest[]>({
    queryKey: ["ai-approvals"],
    queryFn: () => apiFetch<AIApprovalRequest[]>("/api/ai/approvals?status=pending"),
    ...q,
  })
  const alerts = useQuery<AlertEvent[]>({
    queryKey: ["alerts", "firing"],
    queryFn: () => apiFetch<AlertEvent[]>("/api/alerts?status=firing&limit=100"),
    ...q,
  })
  const pendingHosts = useQuery<PendingHostFleet[]>({
    queryKey: ["scans", "pending-hosts"],
    queryFn: () => apiFetch<PendingHostFleet[]>("/api/scans/pending"),
    ...q,
  })
  const hosts = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), ...q })
  const schedules = useSchedules()
  const failedRuns = useFailedRuns(7, enabled)
  const stuck = useStuckOperations(enabled)
  const now = useNow()

  // Lazily from sessionStorage. Safe across hydration: the server renders
  // no queue rows at all (the queries have no data yet), so what is hidden
  // cannot differ between the two renders.
  const [dismissed, setDismissed] = useState<Record<string, number>>(() => (typeof window === "undefined" ? {} : readDismissed()))

  const dismiss = useCallback((id: string) => {
    setDismissed((d) => {
      const next = { ...d, [id]: Date.now() }
      try {
        window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        /* private mode — the queue just will not remember */
      }
      return next
    })
  }, [])

  const all = useMemo<PendingItem[]>(() => {
    const out: PendingItem[] = []
    const hostName = new Map((hosts.data ?? []).map((h) => [h.id, h.hostname]))

    for (const a of approvals.data ?? []) {
      const exp = a.expires_at ? new Date(a.expires_at).getTime() : Infinity
      const born = new Date(a.created_at).getTime()
      const host = a.target_host_id != null ? (hostName.get(a.target_host_id) ?? `host #${a.target_host_id}`) : null
      out.push({
        id: `approval:${a.id}`,
        lane: "approvals",
        kind: "Assistant paused",
        title: `assistant wants to run: ${a.command_preview}`,
        detail: `session #${a.session_id} · ${a.classification} · ${host ?? "control plane"}`,
        expires: a.expires_at ? untilLabel(a.expires_at) : "—",
        expiresAt: exp,
        severity: "block",
        href: `/assistant?session=${a.session_id}`,
        dismissible: false,
        hosts: host ? [host] : [],
        left: Number.isFinite(exp) && exp > born ? Math.max(0, Math.min(1, (exp - now) / (exp - born))) : undefined,
        open: "Review",
      })
    }

    const byScan = new Map<string, PendingHostFleet[]>()
    for (const p of pendingHosts.data ?? []) byScan.set(p.scan_config_name, [...(byScan.get(p.scan_config_name) ?? []), p])
    for (const [scan, rows] of byScan) {
      const ssh = rows.filter((r) => r.ssh_verified).length
      out.push({
        id: `discovery:${scan}`,
        lane: "approvals",
        kind: "Discovered hosts",
        title: `${rows.length} host${rows.length === 1 ? "" : "s"} awaiting approval from scan ${scan}`,
        detail: `${ssh} of ${rows.length} respond to SSH · newest ${shortAgo(rows[0].discovered_at)} ago`,
        expires: "—",
        expiresAt: Infinity,
        severity: "block",
        href: "/discovery",
        dismissible: true,
        hosts: [],
        open: "Review",
      })
    }

    for (const al of alerts.data ?? []) {
      const critical = (al.severity ?? "").toLowerCase() === "critical"
      const host = al.host_id != null ? hostName.get(al.host_id) : undefined
      out.push({
        id: `alert:${al.id}`,
        lane: "alerts",
        kind: "Alert",
        title: host ? `${al.alertname} — ${host}` : al.alertname,
        detail: `firing ${shortAgo(al.starts_at)} · severity ${al.severity ?? "unknown"}${al.investigation_status ? ` · investigation ${al.investigation_status.replace(/_/g, " ")}` : ""}`,
        expires: "firing",
        expiresAt: new Date(al.starts_at).getTime(),
        severity: critical ? "block" : "warn",
        href: al.investigation_session_id ? `/assistant?session=${al.investigation_session_id}` : "/alerts",
        dismissible: true,
        hosts: host ? [host] : [],
        open: al.investigation_session_id ? "Investigation" : "Review",
      })
    }

    // A schedule whose newest finished run failed, once — however many
    // times in a row it has failed. The id carries the run, so hiding it
    // lasts until the next failure, not forever.
    const runById = new Map((failedRuns.data ?? []).map((r) => [r.id, r]))
    for (const sched of schedules.data ?? []) {
      const streak = failStreak(sched.recent_runs)
      if (streak === 0) continue
      const last = (sched.recent_runs ?? []).find((r) => BAD.has(r.status))!
      const run = runById.get(last.id)
      const ff = run?.first_failure
      const tally = tallyLabel(run?.host_counts)
      const name = scheduleName(sched)
      out.push({
        id: `failed:${sched.id}:${last.id}`,
        lane: "failed",
        kind: "Failed run",
        title: `${name} ${last.status === "partial" ? "failed on some hosts" : "failed"}${ff && run?.target_kind !== "host" ? ` · ${ff.hostname}` : ""}${streak > 1 ? ` · ${streak} times in a row` : ""}`,
        detail: [`schedule · ${run?.target_label ?? sched.target_name ?? "all hosts"}`, tally, ff ? `${ff.hostname}: ${ff.reason ?? ff.error_message ?? `exit ${ff.exit_code ?? "?"}`}` : run?.error_message].filter(Boolean).join(" · "),
        expires: `${shortAgo(last.created_at)} ago`,
        expiresAt: Infinity,
        severity: "fail",
        href: `/actions/runs/${last.id}`,
        dismissible: true,
        hosts: ff ? [ff.hostname] : run?.target_kind === "host" ? [run.target_label] : [],
        open: "Open run",
        run,
      })
    }
    // Runs an alert started fail unattended too. Ad-hoc runs are left out:
    // whoever pressed Run watched it end.
    for (const run of failedRuns.data ?? []) {
      if (run.scheduled_action_id != null || run.alert_event_id == null) continue
      const ff = run.first_failure
      out.push({
        id: `failed:run:${run.id}`,
        lane: "failed",
        kind: "Failed run",
        title: `${actionLabel(run.action_key)} ${run.status === "partial" ? "failed on some hosts" : "failed"} — ${run.target_label}`,
        detail: [`started by an alert`, tallyLabel(run.host_counts), ff ? `${ff.hostname}: ${ff.reason ?? ff.error_message ?? `exit ${ff.exit_code ?? "?"}`}` : run.error_message].filter(Boolean).join(" · "),
        expires: `${shortAgo(run.created_at)} ago`,
        expiresAt: Infinity,
        severity: "fail",
        href: `/actions/runs/${run.id}`,
        dismissible: true,
        hosts: ff ? [ff.hostname] : run.target_kind === "host" ? [run.target_label] : [],
        open: "Open run",
        run,
      })
    }

    for (const op of stuck.data?.items ?? []) {
      const where = op.hostnames.length > 3 ? `${op.hostnames.length} hosts` : op.hostnames.join(", ")
      out.push({
        id: `stuck:${op.kind}:${op.id}`,
        lane: "stuck",
        kind: "Stuck",
        title: `${op.kind === "sync" ? `sync of ${op.label}` : actionLabel(op.label)} running on ${where} for ${durationLabel(now - new Date(op.started_at).getTime())}`,
        detail: op.queued_behind
          ? `${op.queued_behind} operation${op.queued_behind === 1 ? " is" : "s are"} queued behind it on ${op.hostnames.length === 1 ? "that host" : "those hosts"}`
          : "nothing is queued behind it yet",
        expires: `${shortAgo(op.started_at)}`,
        expiresAt: Infinity,
        severity: "warn",
        href: op.kind === "action" ? `/actions/runs/${op.id}` : `/hosts/${op.host_ids[0]}`,
        dismissible: true,
        hosts: op.hostnames,
        open: "Open",
        stuck: op,
      })
    }

    const drifted = (hosts.data ?? []).filter((h) => h.sync_status === "out_of_sync").map((h) => h.hostname)
    if (drifted.length > 0) {
      out.push({
        id: "drift",
        lane: "drift",
        kind: "Drift",
        title: `${drifted.length} host${drifted.length === 1 ? " has" : "s have"} drifted from desired state`,
        detail: `${drifted.slice(0, 4).join(", ")}${drifted.length > 4 ? ` and ${drifted.length - 4} more` : ""} · see Operations · Drift`,
        expires: "—",
        expiresAt: Infinity,
        severity: "info",
        href: "/drift",
        dismissible: false,
        hosts: drifted,
        open: "Review",
      })
    }

    // Soonest to expire first; what never expires after it, worst first.
    return out.sort((a, b) => {
      const fa = Number.isFinite(a.expiresAt)
      const fb = Number.isFinite(b.expiresAt)
      if (fa && fb) return a.expiresAt - b.expiresAt
      if (fa !== fb) return fa ? -1 : 1
      return SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]
    })
  }, [approvals.data, alerts.data, pendingHosts.data, hosts.data, schedules.data, failedRuns.data, stuck.data, now])

  const live = useMemo(() => all.filter((p) => !dismissed[p.id]), [all, dismissed])

  return {
    all,
    live,
    dismiss,
    isLoading: approvals.isLoading || alerts.isLoading || pendingHosts.isLoading,
    laneCounts: {
      all: live.length,
      approvals: live.filter((p) => p.lane === "approvals").length,
      failed: live.filter((p) => p.lane === "failed").length,
      stuck: live.filter((p) => p.lane === "stuck").length,
      alerts: live.filter((p) => p.lane === "alerts").length,
      drift: live.filter((p) => p.lane === "drift").length,
    } satisfies Record<PendingLane | "all", number>,
  }
}

export interface SettledItem {
  id: string
  at: string
  what: string
  outcome: "approved" | "rejected" | "expired" | "resolved"
}

/**
 * What left the queue in the last 24 hours, and how: approval gates that
 * were decided or ran out, alerts that resolved. Newest first.
 */
export function useSettled(hours = 24) {
  const q = { refetchInterval: 60_000, retry: false }
  const approvals = useQuery<AIApprovalRequest[]>({
    queryKey: ["ai-approvals", "all"],
    queryFn: () => apiFetch<AIApprovalRequest[]>("/api/ai/approvals?status=all&limit=100"),
    ...q,
  })
  const alerts = useQuery<AlertEvent[]>({
    queryKey: ["alerts", "resolved"],
    queryFn: () => apiFetch<AlertEvent[]>("/api/alerts?status=resolved&limit=100"),
    ...q,
  })
  const hosts = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), ...q })
  const now = useNow(60_000)

  const items = useMemo<SettledItem[]>(() => {
    const cutoff = now - hours * 3_600_000
    const name = new Map((hosts.data ?? []).map((h) => [h.id, h.hostname]))
    const out: SettledItem[] = []
    for (const a of approvals.data ?? []) {
      if (a.status === "pending") continue
      const at = a.status === "expired" ? (a.decided_at ?? a.expires_at) : a.decided_at
      if (!at || new Date(at).getTime() < cutoff) continue
      const host = a.target_host_id != null ? name.get(a.target_host_id) : undefined
      out.push({ id: `approval:${a.id}`, at, what: `assistant: ${a.command_preview}${host ? ` on ${host}` : ""}`, outcome: a.status })
    }
    for (const al of alerts.data ?? []) {
      const at = al.ends_at
      if (!at || new Date(at).getTime() < cutoff) continue
      const host = al.host_id != null ? name.get(al.host_id) : undefined
      out.push({ id: `alert:${al.id}`, at, what: host ? `${al.alertname} — ${host}` : al.alertname, outcome: "resolved" })
    }
    return out.sort((a, b) => b.at.localeCompare(a.at))
  }, [approvals.data, alerts.data, hosts.data, hours, now])

  return { items, isLoading: approvals.isLoading || alerts.isLoading }
}
