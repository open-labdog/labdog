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
    return <Badge className="bg-slate-700 text-slate-400">unknown</Badge>
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
