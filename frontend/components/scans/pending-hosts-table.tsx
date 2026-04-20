"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip } from "@/components/ui/tooltip"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatRelativeTime } from "@/lib/utils"
import type { PendingHost } from "@/lib/types"

interface PendingHostsTableProps {
  rows: PendingHost[]
  onApprove: (ids: number[]) => void
  onDismiss: (ids: number[]) => void
  approveLoading?: boolean
  dismissLoading?: boolean
  showConfigColumn?: boolean
}

export function PendingHostsTable({
  rows,
  onApprove,
  onDismiss,
  approveLoading = false,
  dismissLoading = false,
  showConfigColumn = false,
}: PendingHostsTableProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set())

  function toggleRow(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    if (selected.size === rows.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(rows.map((r) => r.id)))
    }
  }

  function handleApprove() {
    const ids = Array.from(selected)
    onApprove(ids)
    setSelected(new Set())
  }

  function handleDismiss() {
    const ids = Array.from(selected)
    onDismiss(ids)
    setSelected(new Set())
  }

  const allChecked = rows.length > 0 && selected.size === rows.length
  const someChecked = selected.size > 0 && selected.size < rows.length
  const noneSelected = selected.size === 0
  const actionsPending = approveLoading || dismissLoading

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <span className="text-sm text-slate-400">
          {selected.size > 0
            ? `${selected.size} of ${rows.length} selected`
            : `${rows.length} host${rows.length !== 1 ? "s" : ""}`}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={noneSelected || actionsPending}
            onClick={handleDismiss}
          >
            {dismissLoading ? "Dismissing\u2026" : "Dismiss selected"}
          </Button>
          <Button
            size="sm"
            disabled={noneSelected || actionsPending}
            onClick={handleApprove}
          >
            {approveLoading ? "Approving\u2026" : "Approve selected"}
          </Button>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg border border-slate-700 bg-slate-900">
        <Table>
          <TableHeader>
            <TableRow className="border-slate-700">
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  checked={allChecked}
                  ref={(el) => {
                    if (el) el.indeterminate = someChecked
                  }}
                  onChange={toggleAll}
                  aria-label="Select all"
                  className="rounded border-slate-600"
                />
              </TableHead>
              <TableHead className="text-slate-400">IP Address</TableHead>
              <TableHead className="text-slate-400">Hostname</TableHead>
              {showConfigColumn && (
                <TableHead className="text-slate-400">From Config</TableHead>
              )}
              <TableHead className="text-slate-400">Discovered</TableHead>
              <TableHead className="text-slate-400">SSH Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id} className="border-slate-700">
                <TableCell>
                  <input
                    type="checkbox"
                    checked={selected.has(row.id)}
                    onChange={() => toggleRow(row.id)}
                    aria-label={`Select ${row.ip_address}`}
                    className="rounded border-slate-600"
                  />
                </TableCell>
                <TableCell className="font-mono text-slate-300 text-xs">
                  {row.ip_address}
                </TableCell>
                <TableCell className="text-slate-300 text-xs">
                  {row.hostname ?? <span className="text-slate-500">—</span>}
                </TableCell>
                {showConfigColumn && (
                  <TableCell className="text-slate-400 text-xs">
                    {"scan_config_name" in row
                      ? (row as unknown as { scan_config_name: string }).scan_config_name
                      : row.scan_config_id}
                  </TableCell>
                )}
                <TableCell className="text-slate-400 text-xs">
                  {formatRelativeTime(row.discovered_at)}
                </TableCell>
                <TableCell>
                  {row.ssh_verified ? (
                    <Badge className="bg-green-600 text-white text-[11px]">Verified</Badge>
                  ) : row.ssh_error ? (
                    <Tooltip content={row.ssh_error} side="top">
                      <Badge className="bg-red-600 text-white text-[11px] cursor-help">
                        Unverified
                      </Badge>
                    </Tooltip>
                  ) : (
                    <Badge className="bg-slate-600 text-slate-300 text-[11px]">Unverified</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
