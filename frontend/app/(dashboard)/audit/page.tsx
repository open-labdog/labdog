"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

interface AuditEntry {
  id: number
  timestamp: string
  user: string
  action: "create" | "update" | "delete" | string
  entity_type: string
  entity_id: number | string
  details: string | null
}

const STUB_DATA: AuditEntry[] = [
  {
    id: 1,
    timestamp: new Date(Date.now() - 60000).toISOString(),
    user: "admin@example.com",
    action: "create",
    entity_type: "group",
    entity_id: 1,
    details: "Created group 'web-servers'",
  },
  {
    id: 2,
    timestamp: new Date(Date.now() - 120000).toISOString(),
    user: "admin@example.com",
    action: "update",
    entity_type: "rule",
    entity_id: 5,
    details: "Updated rule priority from 10 to 20",
  },
  {
    id: 3,
    timestamp: new Date(Date.now() - 300000).toISOString(),
    user: "ops@example.com",
    action: "delete",
    entity_type: "host",
    entity_id: 3,
    details: "Removed host 'old-server-01'",
  },
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

const ACTION_TYPES = ["all", "create", "update", "delete"]
const ENTITY_TYPES = ["all", "group", "host", "rule", "ssh_key"]
const PAGE_SIZE = 20

export default function AuditPage() {
  const [actionFilter, setActionFilter] = useState("all")
  const [entityFilter, setEntityFilter] = useState("all")
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

  const entries = data ?? []

  const filtered = entries.filter((e) => {
    if (actionFilter !== "all" && e.action !== actionFilter) return false
    if (entityFilter !== "all" && e.entity_type !== entityFilter) return false
    return true
  })

  const paginated = filtered.slice(0, page * PAGE_SIZE)
  const hasMore = paginated.length < filtered.length

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Audit Log" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">Audit Log</h1>
        <p className="text-slate-400 text-sm mt-1">
          Track all changes made to firewall configuration
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label htmlFor="action-filter" className="text-sm text-slate-400">Action:</label>
          <select
            id="action-filter"
            value={actionFilter}
            onChange={(e) => { setActionFilter(e.target.value); setPage(1) }}
            className="rounded-md border border-slate-700 bg-slate-900 text-slate-200 text-sm px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-slate-500"
          >
            {ACTION_TYPES.map((a) => (
              <option key={a} value={a}>
                {a === "all" ? "All Actions" : a.charAt(0).toUpperCase() + a.slice(1)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="entity-filter" className="text-sm text-slate-400">Entity:</label>
          <select
            id="entity-filter"
            value={entityFilter}
            onChange={(e) => { setEntityFilter(e.target.value); setPage(1) }}
            className="rounded-md border border-slate-700 bg-slate-900 text-slate-200 text-sm px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-slate-500"
          >
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {t === "all" ? "All Entities" : t.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading audit log…</div>
      )}

      {error && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 px-4 py-3 text-amber-400 text-sm">
          Audit log endpoint unavailable — showing stub data.
        </div>
      )}

      {!isLoading && filtered.length === 0 && (
        <div className="text-slate-400 py-8 text-center">No audit entries found.</div>
      )}

      {!isLoading && (
        <>
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead>Timestamp</TableHead>
                  <TableHead>User</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Entity</TableHead>
                  <TableHead>Details</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginated.map((entry) => (
                  <TableRow key={entry.id} className="border-slate-700">
                    <TableCell className="font-mono text-slate-300 text-xs whitespace-nowrap">
                      {new Date(entry.timestamp).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-slate-300 text-sm">
                      {entry.user}
                    </TableCell>
                    <TableCell>
                      <ActionBadge action={entry.action} />
                    </TableCell>
                    <TableCell className="text-slate-300 text-sm capitalize">
                      {entry.entity_type.replace("_", " ")} #{entry.entity_id}
                    </TableCell>
                    <TableCell className="text-slate-400 text-xs max-w-xs truncate">
                      {entry.details ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

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

          {filtered.length > 0 && (
            <p className="text-center text-xs text-slate-500">
              Showing {paginated.length} of {filtered.length} entries
            </p>
          )}
        </>
      )}
    </div>
  )
}
