"use client"

import { useCallback, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { shortAgo, untilLabel } from "@/lib/fleet"
import type { AIApprovalRequest, AlertEvent, Host, PendingHostFleet } from "@/lib/types"

/**
 * Pending — one list, typed lanes: assistant sessions paused at an
 * approval gate, discovered hosts awaiting approval, firing alerts, and
 * drift. "Pending" is the app's own word for this state, and unlike
 * Inbox it promises no messages, senders or read state.
 *
 * Mixed urgency is the cost of one list, so every item carries an expiry
 * and the default sort is soonest-to-expire, not most recent.
 */
export type PendingLane = "approvals" | "alerts" | "drift"
export type PendingSeverity = "block" | "warn" | "info"

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
    queryFn: () => apiFetch<AlertEvent[]>("/api/ai/alerts?status=firing&limit=100"),
    ...q,
  })
  const pendingHosts = useQuery<PendingHostFleet[]>({
    queryKey: ["scans", "pending-hosts"],
    queryFn: () => apiFetch<PendingHostFleet[]>("/api/scans/pending"),
    ...q,
  })
  const hosts = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), ...q })

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
      out.push({
        id: `approval:${a.id}`,
        lane: "approvals",
        kind: "Assistant paused",
        title: `assistant wants to run: ${a.command_preview}`,
        detail: `session #${a.session_id} · ${a.classification} · ${a.target_host_id != null ? (hostName.get(a.target_host_id) ?? `host #${a.target_host_id}`) : "control plane"}`,
        expires: a.expires_at ? untilLabel(a.expires_at) : "—",
        expiresAt: exp,
        severity: "block",
        href: `/assistant?session=${a.session_id}`,
        dismissible: false,
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
      })
    }

    const drifted = (hosts.data ?? []).filter((h) => h.sync_status === "out_of_sync").length
    if (drifted > 0) {
      out.push({
        id: "drift",
        lane: "drift",
        kind: "Drift",
        title: `${drifted} host${drifted === 1 ? " has" : "s have"} drifted from desired state`,
        detail: "see Operations · Drift",
        expires: "—",
        expiresAt: Infinity,
        severity: "info",
        href: "/drift",
        dismissible: false,
      })
    }

    return out.sort((a, b) => a.expiresAt - b.expiresAt)
  }, [approvals.data, alerts.data, pendingHosts.data, hosts.data])

  const live = useMemo(() => all.filter((p) => !dismissed[p.id]), [all, dismissed])

  return {
    all,
    live,
    dismiss,
    isLoading: approvals.isLoading || alerts.isLoading || pendingHosts.isLoading,
    laneCounts: {
      all: live.length,
      approvals: live.filter((p) => p.lane === "approvals").length,
      alerts: live.filter((p) => p.lane === "alerts").length,
      drift: live.filter((p) => p.lane === "drift").length,
    },
  }
}
