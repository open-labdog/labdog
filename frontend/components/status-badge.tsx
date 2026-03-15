import { Badge } from "@/components/ui/badge"
import type { SyncStatus, FirewallBackend } from "@/lib/types"

export function SyncStatusBadge({ status }: { status: SyncStatus }) {
  const config: Record<SyncStatus, { label: string; className: string }> = {
    in_sync: { label: "In Sync", className: "bg-green-600 text-white" },
    out_of_sync: { label: "Out of Sync", className: "bg-amber-600 text-white" },
    pending: { label: "Pending", className: "bg-blue-600 text-white" },
    unknown: { label: "Unknown", className: "" },
    error: { label: "Error", className: "bg-red-600 text-white" },
  }
  const c = config[status] ?? config.unknown
  return <Badge className={c.className}>{c.label}</Badge>
}

export function FirewallBadge({ backend }: { backend: FirewallBackend }) {
  return <Badge variant="outline">{backend}</Badge>
}
