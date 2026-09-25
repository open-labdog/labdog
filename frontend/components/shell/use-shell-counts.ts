"use client"

import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { AIApprovalRequest, AlertEvent, Host, HostGroup, PendingSummary, ScheduledAction } from "@/lib/types"
import type { ShellCounts } from "./zones"

const POLL = 30_000

/**
 * The handful of numbers the rail and pane show. Every query here shares
 * its key with the page that owns the data, so opening a page never
 * refetches what the shell already has, and a mutation's invalidation
 * updates the pane for free.
 *
 * The Overview badge counts only what blocks or expires: approval gates
 * and firing alerts, plus one for the discovery queue when it is
 * non-empty. Drift is a count inside Pending, never a badge — a badge
 * that says 214 because of drift findings is a badge you stop reading.
 */
export function useShellCounts(enabled = true): ShellCounts {
  const q = { refetchInterval: POLL, retry: false, enabled }
  const hosts = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), ...q })
  const groups = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups"), ...q })
  const pending = useQuery<PendingSummary>({
    queryKey: ["scans", "pending-summary"],
    queryFn: () => apiFetch<PendingSummary>("/api/scans/pending-summary"),
    ...q,
  })
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
  const schedules = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions"],
    queryFn: () => apiFetch<ScheduledAction[]>("/api/scheduled-actions"),
    ...q,
  })

  const pendingHosts = pending.data?.total
  const approvalsN = approvals.data?.length
  const firing = alerts.data?.length
  const blocking =
    approvalsN === undefined && firing === undefined && pendingHosts === undefined
      ? undefined
      : (approvalsN ?? 0) + (firing ?? 0) + (pendingHosts ? 1 : 0)

  return {
    hosts: hosts.data?.length,
    groups: groups.data?.length,
    pendingHosts,
    approvals: approvalsN,
    firingAlerts: firing,
    drifted: hosts.data?.filter((h) => h.sync_status === "out_of_sync").length,
    schedules: schedules.data?.filter((s) => s.enabled).length,
    pendingBlocking: blocking,
  }
}
