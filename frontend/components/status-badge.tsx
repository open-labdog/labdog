import { Badge } from "@/components/ui/badge"
import type { SyncStatus, FirewallBackend, GitOpsStatus } from "@/lib/types"

export function SyncStatusBadge({ status }: { status: SyncStatus }) {
  const config: Record<SyncStatus, { label: string; className: string }> = {
    in_sync: { label: "In Sync", className: "bg-green-600 text-white" },
    out_of_sync: { label: "Out of Sync", className: "bg-amber-600 text-white" },
    pending: { label: "Pending", className: "bg-blue-600 text-white" },
    unknown: { label: "Unknown", className: "bg-slate-600 text-slate-300" },
    error: { label: "Error", className: "bg-red-600 text-white" },
  }
  const c = config[status] ?? config.unknown
  return <Badge className={c.className}>{c.label}</Badge>
}

export function FirewallBadge({ backend }: { backend: FirewallBackend }) {
  if (backend === "unknown") {
    return <Badge className="bg-slate-600 text-slate-300">Unknown</Badge>
  }
  return <Badge variant="outline">{backend}</Badge>
}

export function GitOpsStatusBadge({ status }: { status: GitOpsStatus }) {
  const config: Record<GitOpsStatus, { label: string; className: string }> = {
    synced: { label: "Synced", className: "bg-green-600 text-white" },
    error: { label: "Error", className: "bg-red-600 text-white" },
    importing: { label: "Importing", className: "bg-blue-600 text-white" },
    disconnected: { label: "Disconnected", className: "bg-slate-600 text-slate-300" },
  }
  const c = config[status] ?? config.disconnected
  return <Badge className={c.className}>{c.label}</Badge>
}

/**
 * Item state — applies to cron jobs, users, groups, repos, ca-certs,
 * hosts-file entries, and any other "present or absent in the desired state"
 * boolean-with-a-name concept.
 */
export function ItemStateBadge({ state }: { state: "present" | "absent" | string }) {
  const className =
    state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"
  const label = state.charAt(0).toUpperCase() + state.slice(1)
  return <Badge className={className}>{label}</Badge>
}

/**
 * Package state — adds a third "latest" value (blue) on top of the
 * present/absent dichotomy. Used for package rules only.
 */
export function PackageStateBadge({
  state,
}: {
  state: "present" | "absent" | "latest" | string
}) {
  const className =
    state === "present"
      ? "bg-green-600 text-white"
      : state === "latest"
        ? "bg-blue-600 text-white"
        : "bg-red-600 text-white"
  const label = state.charAt(0).toUpperCase() + state.slice(1)
  return <Badge className={className}>{label}</Badge>
}

/**
 * Enabled flag — a boolean rendered as Enabled/Disabled badge.
 * Used for services, scan configs, schedules, workflows, etc.
 */
export function EnabledBadge({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <Badge className="bg-green-700 text-white">Enabled</Badge>
  ) : (
    <Badge variant="outline">Disabled</Badge>
  )
}

/**
 * Systemd active-state — maps the standard systemd states to colors.
 * Optional `label` prop lets the caller override the visible text
 * (e.g. render `sub_state` while colouring by `active_state`). By
 * default the state is rendered as-is (lowercase); pass `titleCase`
 * to capitalise the first letter.
 */
export function SystemdStateBadge({
  state,
  label,
  titleCase = false,
}: {
  state: string
  label?: string
  titleCase?: boolean
}) {
  const className =
    state === "active" || state === "running"
      ? "bg-green-600 text-white"
      : state === "failed"
        ? "bg-red-600 text-white"
        : state === "activating" || state === "deactivating"
          ? "bg-yellow-600 text-white"
          : "bg-slate-600 text-white"
  const text = label ?? (titleCase ? state.charAt(0).toUpperCase() + state.slice(1) : state)
  return <Badge className={className}>{text}</Badge>
}

const RUN_STATUS_COLORS: Record<string, string> = {
  pending: "bg-slate-600 text-white",
  queued: "bg-slate-600 text-white",
  running: "bg-blue-600 text-white",
  succeeded: "bg-green-600 text-white",
  completed: "bg-green-600 text-white",
  failed: "bg-red-600 text-white",
  partial: "bg-amber-600 text-white",
  cancelled: "bg-slate-500 text-white",
  skipped: "bg-slate-400 text-white",
}

export function RunStatusBadge({ status }: { status: string }) {
  return (
    <Badge className={RUN_STATUS_COLORS[status] ?? "bg-slate-600 text-white"}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  )
}
