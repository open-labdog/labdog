"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Inbox } from "lucide-react"
import { apiFetch } from "@/lib/api"
import type { AuditLogEntry, SyncStatus } from "@/lib/types"
import { SyncStatusBadge } from "@/components/status-badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn, formatRelativeTime, useDelayedLoading } from "@/lib/utils"
import { PanelShell, PanelHeader, PanelFootnote, PANEL_BODY_HEIGHT } from "@/components/dashboard/panel-shell"

// Derived from the real `action` values seen in app/api/audit.py callers.
const ACTION_PHRASES: Record<string, string> = {
  create: "created",
  update: "updated",
  delete: "deleted",
  sync_triggered: "triggered a sync on",
  sync_completed: "completed a sync on",
  sync_failed: "sync failed on",
  add_hosts: "added hosts to",
  remove_hosts: "removed hosts from",
  trust_host_key: "trusted the host key for",
  session_start: "started an SSH session on",
  session_end: "ended an SSH session on",
}

function actionPhrase(action: string): string {
  return ACTION_PHRASES[action] ?? action.replace(/_/g, " ")
}

const VALID_SYNC_STATUSES: readonly SyncStatus[] = ["in_sync", "out_of_sync", "pending", "unknown", "error"]

function isSyncStatus(value: unknown): value is SyncStatus {
  return typeof value === "string" && (VALID_SYNC_STATUSES as readonly string[]).includes(value)
}

/** Only sync_completed / sync_failed rows carry a derivable outcome status. */
function derivedStatus(entry: AuditLogEntry): SyncStatus | null {
  if (entry.action === "sync_failed") return "error"
  if (entry.action === "sync_completed") {
    const raw = entry.after_state?.status
    return isSyncStatus(raw) ? raw : "in_sync"
  }
  return null
}

function dotColor(entry: AuditLogEntry): string {
  const status = derivedStatus(entry)
  if (status === "out_of_sync") return "bg-amber-500"
  if (status === "error") return "bg-red-500"
  if (status === "in_sync") return "bg-green-500"

  switch (entry.action) {
    case "delete":
      return "bg-red-500"
    case "create":
    case "add_hosts":
      return "bg-green-500"
    case "update":
    case "sync_triggered":
      return "bg-blue-500"
    case "session_start":
    case "session_end":
      return "bg-slate-500"
    default:
      return "bg-slate-500"
  }
}

function EmptyFeedState() {
  return (
    <div className={cn(PANEL_BODY_HEIGHT, "flex flex-col items-center justify-center gap-2 text-center")}>
      <Inbox className="h-8 w-8 text-slate-600" />
      <p className="text-sm text-slate-400">No activity yet</p>
      <p className="max-w-[240px] text-xs text-slate-500">
        Actions will appear here as your fleet is managed.
      </p>
    </div>
  )
}

function FeedSkeleton() {
  return (
    <div className="space-y-3">
      {[85, 70, 90, 60, 75, 80].map((w, i) => (
        <div key={i} className="flex items-start gap-2.5">
          <Skeleton className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" />
          <div className="flex-1 space-y-1">
            <Skeleton className="h-3" style={{ width: `${w}%` }} />
            <Skeleton className="h-2 w-16" />
          </div>
        </div>
      ))}
    </div>
  )
}

export function ActivityFeedPanel() {
  const { data, isLoading, error } = useQuery<AuditLogEntry[]>({
    queryKey: ["audit", "recent"],
    queryFn: () => apiFetch<AuditLogEntry[]>("/api/audit-log?limit=10"),
    refetchInterval: 30000,
  })
  const showLoading = useDelayedLoading(isLoading)
  const entries = data ?? []

  return (
    <PanelShell>
      <PanelHeader
        title="Recent Activity"
        action={
          <Link href="/audit" className="text-xs text-blue-400 hover:underline">
            View all →
          </Link>
        }
      />
      <PanelFootnote />

      {showLoading && <div className={PANEL_BODY_HEIGHT}><FeedSkeleton /></div>}

      {!showLoading && error && (
        <div className={cn(PANEL_BODY_HEIGHT, "flex items-center justify-center text-sm text-red-400")}>
          Failed to load activity feed
        </div>
      )}

      {!showLoading && !error && entries.length === 0 && <EmptyFeedState />}

      {!showLoading && !error && entries.length > 0 && (
        <div className={cn(PANEL_BODY_HEIGHT, "overflow-y-auto")}>
          {entries.map((entry) => {
            const status = derivedStatus(entry)
            const actor = entry.user_email ?? (entry.user_id ? `User #${entry.user_id}` : "System")
            const entityLabel = `${entry.entity_type.replace(/_/g, " ")}${entry.entity_id ? ` #${entry.entity_id}` : ""}`
            return (
              <div
                key={entry.id}
                className="flex items-start gap-2.5 py-2 border-b border-slate-800 last:border-b-0"
              >
                <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dotColor(entry))} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-slate-300">
                    <span className="font-medium text-white">{actor}</span>{" "}
                    <span className="text-slate-400">{actionPhrase(entry.action)}</span>{" "}
                    <span className="font-medium text-slate-200">{entityLabel}</span>
                  </p>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <span className="text-[11px] text-slate-500">{formatRelativeTime(entry.created_at)}</span>
                    {status && <SyncStatusBadge status={status} />}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </PanelShell>
  )
}
