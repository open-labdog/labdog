"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { DataTable } from "@/components/ui/data-table"

interface AuditEntry {
  id: number
  created_at: string
  user_id: number | null
  user_email: string | null
  action: "create" | "update" | "delete" | string
  entity_type: string
  entity_id: number | string | null
  before_state: Record<string, unknown> | null
  after_state: Record<string, unknown> | null
  ip_address: string | null
}

const STUB_DATA: AuditEntry[] = [
]

const ACTION_COLORS: Record<string, string> = {
  create: "bg-green-600 text-white",
  update: "bg-blue-600 text-white",
  delete: "bg-red-600 text-white",
}

function ActionBadge({ action }: { action: string }) {
  return (
    <Badge className={ACTION_COLORS[action] ?? "bg-slate-600 text-white"}>
      {action.charAt(0).toUpperCase() + action.slice(1)}
    </Badge>
  )
}

const PAGE_SIZE = 20

export default function AuditPage() {
  const [page, setPage] = useState(1)

  const { data, isLoading, error } = useQuery<AuditEntry[]>({
    queryKey: ["audit-log"],
    queryFn: async () => {
      try {
        return await apiFetch<AuditEntry[]>("/api/audit-log")
      } catch {
        // Endpoint may not exist yet — fall back to stub data
        return STUB_DATA
      }
    },
    retry: false,
  })
  const showLoading = useDelayedLoading(isLoading)

  const entries = data ?? []
  const paginated = entries.slice(0, page * PAGE_SIZE)
  const hasMore = paginated.length < entries.length

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Audit Log" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">Audit Log</h1>
        <p className="text-slate-400 text-sm mt-1">
          Track all changes made to firewall configuration
        </p>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={5} />}

      {error && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 px-4 py-3 text-amber-400 text-sm">
          Audit log endpoint unavailable — showing stub data.
        </div>
      )}

      {!isLoading && (
        <>
          <DataTable<AuditEntry>
            tableId="audit-log"
            data={paginated}
            emptyMessage="No audit entries found."
            getRowKey={(e) => e.id}
            columns={[
              {
                key: "timestamp",
                label: "Timestamp",
                accessor: (e) => e.created_at,
                cell: (e) => (
                  <span className="font-mono text-slate-300 text-xs whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString()}
                  </span>
                ),
                defaultWidth: 180,
                filter: { type: "dateRange" },
              },
              {
                key: "user",
                label: "User",
                accessor: (e) => e.user_email ?? (e.user_id ? `User #${e.user_id}` : "System"),
                cell: (e) => (
                  <span className="text-slate-300 text-sm">
                    {e.user_email ?? (e.user_id ? `User #${e.user_id}` : "System")}
                  </span>
                ),
                defaultWidth: 180,
                filter: { type: "text" },
              },
              {
                key: "action",
                label: "Action",
                accessor: (e) => e.action,
                cell: (e) => <ActionBadge action={e.action} />,
                defaultWidth: 120,
                filter: { type: "enum", options: [{label:"Create",value:"create"},{label:"Update",value:"update"},{label:"Delete",value:"delete"}] },
              },
              {
                key: "entity",
                label: "Entity",
                accessor: (e) => `${e.entity_type}${e.entity_id ? ` #${e.entity_id}` : ""}`,
                cell: (e) => (
                  <span className="text-slate-300 text-sm capitalize">
                    {e.entity_type.replace("_", " ")}{e.entity_id ? ` #${e.entity_id}` : ""}
                  </span>
                ),
                defaultWidth: 180,
                filter: { type: "text" },
              },
              {
                key: "ip_address",
                label: "IP Address",
                accessor: (e) => e.ip_address ?? "",
                cell: (e) => (
                  <span className="text-slate-400 text-xs">
                    {e.ip_address ?? "—"}
                  </span>
                ),
                defaultWidth: 160,
                filter: { type: "text", placeholder: "e.g. 10.0.1" },
              },
            ]}
          />

          {hasMore && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                onClick={() => setPage((p) => p + 1)}
              >
                Load More
              </Button>
            </div>
          )}

          {entries.length > 0 && (
            <p className="text-center text-xs text-slate-500">
              Showing {paginated.length} of {entries.length} entries
            </p>
          )}
        </>
      )}
    </div>
  )
}
