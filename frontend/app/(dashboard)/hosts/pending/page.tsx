"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { cn, formatRelativeTime } from "@/lib/utils"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import { Tooltip } from "@/components/ui/tooltip"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { PendingSummary, PendingHostFleet } from "@/lib/types"

export default function PendingApprovalPage() {
  const [pendingSelected, setPendingSelected] = useState<Set<number>>(new Set())
  const [actionLoading, setActionLoading] = useState(false)
  const queryClient = useQueryClient()

  const { data: summary } = useQuery<PendingSummary>({
    queryKey: ["scans", "pending-summary"],
    queryFn: () => apiFetch<PendingSummary>("/api/scans/pending-summary"),
    refetchInterval: 30000,
  })

  const { data: pendingHosts } = useQuery<PendingHostFleet[]>({
    queryKey: ["scans", "pending"],
    queryFn: () => apiFetch<PendingHostFleet[]>("/api/scans/pending"),
    refetchInterval: 30000,
  })

  const uniqueConfigCount = new Set(pendingHosts?.map((h) => h.scan_config_id) ?? []).size
  const allIds = pendingHosts?.map((h) => h.id) ?? []
  const allSelected = allIds.length > 0 && allIds.every((id) => pendingSelected.has(id))

  function toggleAll() {
    if (allSelected) {
      setPendingSelected(new Set())
    } else {
      setPendingSelected(new Set(allIds))
    }
  }

  function toggleOne(id: number) {
    setPendingSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function invalidateAfterAction() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scans", "pending-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["scans", "pending"] }),
      queryClient.invalidateQueries({ queryKey: ["hosts-summary"] }),
    ])
  }

  async function handleApprove() {
    if (pendingSelected.size === 0) return
    const selectedHosts = pendingHosts?.filter((h) => pendingSelected.has(h.id)) ?? []

    const byConfig = new Map<number, number[]>()
    for (const h of selectedHosts) {
      const existing = byConfig.get(h.scan_config_id) ?? []
      existing.push(h.id)
      byConfig.set(h.scan_config_id, existing)
    }

    setActionLoading(true)
    let totalApproved = 0
    let totalSkipped = 0
    let hadError = false

    const results = await Promise.allSettled(
      Array.from(byConfig.entries()).map(([configId, ids]) =>
        apiFetch<{ approved: number; skipped: number; skipped_ips: string[] }>(
          `/api/scans/${configId}/pending/approve`,
          { method: "POST", body: JSON.stringify({ ids }) }
        )
      )
    )

    for (const result of results) {
      if (result.status === "fulfilled") {
        totalApproved += result.value.approved
        totalSkipped += result.value.skipped
      } else {
        hadError = true
      }
    }

    await invalidateAfterAction()
    setPendingSelected(new Set())
    setActionLoading(false)

    if (hadError) {
      showError("Some approvals failed. Please try again.")
    } else if (totalSkipped > 0) {
      showSuccess(`Approved ${totalApproved} host${totalApproved !== 1 ? "s" : ""} (${totalSkipped} skipped as duplicate${totalSkipped !== 1 ? "s" : ""})`)
    } else {
      showSuccess(`Approved ${totalApproved} host${totalApproved !== 1 ? "s" : ""}`)
    }
  }

  async function handleDismiss() {
    if (pendingSelected.size === 0) return
    const selectedHosts = pendingHosts?.filter((h) => pendingSelected.has(h.id)) ?? []

    const byConfig = new Map<number, number[]>()
    for (const h of selectedHosts) {
      const existing = byConfig.get(h.scan_config_id) ?? []
      existing.push(h.id)
      byConfig.set(h.scan_config_id, existing)
    }

    setActionLoading(true)
    let totalDismissed = 0
    let hadError = false

    const results = await Promise.allSettled(
      Array.from(byConfig.entries()).map(([configId, ids]) =>
        apiFetch<{ dismissed: number }>(
          `/api/scans/${configId}/pending/dismiss`,
          { method: "POST", body: JSON.stringify({ ids }) }
        )
      )
    )

    for (const result of results) {
      if (result.status === "fulfilled") {
        totalDismissed += result.value.dismissed
      } else {
        hadError = true
      }
    }

    await invalidateAfterAction()
    setPendingSelected(new Set())
    setActionLoading(false)

    if (hadError) {
      showError("Some dismissals failed. Please try again.")
    } else {
      showSuccess(`Dismissed ${totalDismissed} host${totalDismissed !== 1 ? "s" : ""}`)
    }
  }

  const total = summary?.total ?? 0

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: "Discovery", href: "/hosts/discovery" }, { label: "Pending approval" }]} />

      <div>
        <h1 className="text-2xl font-bold text-white">Pending approval</h1>
        <p className="text-slate-400 text-sm mt-1">
          Hosts discovered by scheduled scans that are awaiting your review before joining the fleet.
        </p>
      </div>

      {total === 0 ? (
        <div className="rounded-lg border border-slate-700 bg-slate-900 px-8 py-12 text-center">
          <p className="text-slate-300 font-medium">No hosts awaiting approval</p>
          <p className="text-slate-500 text-sm mt-1">
            New discoveries will appear here when scan configs find hosts with pending mode.
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-amber-500/40 bg-slate-900">
          <div className="px-4 py-3 border-b border-amber-500/20 flex items-center gap-2.5">
            <span className="h-2 w-2 shrink-0 rounded-full bg-amber-500" />
            <span className="text-sm font-medium text-amber-400">
              {total} host{total !== 1 ? "s" : ""} pending review
              {uniqueConfigCount > 0 && (
                <> &middot; From {uniqueConfigCount} scan config{uniqueConfigCount !== 1 ? "s" : ""}</>
              )}
            </span>
          </div>

          <div className="px-4 pb-4 pt-3">
            {/* Toolbar */}
            <div className="mb-3 flex items-center gap-2">
              <span className="text-xs text-slate-400">
                {pendingSelected.size > 0 ? `${pendingSelected.size} selected` : "Select hosts to act"}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={pendingSelected.size === 0 || actionLoading}
                onClick={handleApprove}
              >
                {actionLoading ? "Working..." : "Approve selected"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={pendingSelected.size === 0 || actionLoading}
                onClick={handleDismiss}
              >
                Dismiss selected
              </Button>
            </div>

            {/* Table */}
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead className="w-10">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={toggleAll}
                        className="rounded border-slate-600"
                        aria-label="Select all pending hosts"
                      />
                    </TableHead>
                    <TableHead className="text-slate-400 text-xs">IP</TableHead>
                    <TableHead className="text-slate-400 text-xs">Hostname</TableHead>
                    <TableHead className="text-slate-400 text-xs">From config</TableHead>
                    <TableHead className="text-slate-400 text-xs">Discovered</TableHead>
                    <TableHead className="text-slate-400 text-xs">SSH</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pendingHosts && pendingHosts.length > 0 ? (
                    pendingHosts.map((host) => (
                      <TableRow key={host.id} className="border-slate-700">
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={pendingSelected.has(host.id)}
                            onChange={() => toggleOne(host.id)}
                            className="rounded border-slate-600"
                            aria-label={`Select ${host.ip_address}`}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{host.ip_address}</TableCell>
                        <TableCell className="text-slate-300 text-xs">
                          {host.hostname ?? <span className="text-slate-500">—</span>}
                        </TableCell>
                        <TableCell className="text-xs">
                          <Link
                            href={`/hosts/discovery/${host.scan_config_id}/pending`}
                            className="text-blue-400 hover:underline"
                          >
                            {host.scan_config_name}
                          </Link>
                        </TableCell>
                        <TableCell className="text-slate-400 text-xs">
                          {formatRelativeTime(host.discovered_at)}
                        </TableCell>
                        <TableCell>
                          {host.ssh_verified ? (
                            <Badge className={cn("text-white text-xs", "bg-green-600")}>verified</Badge>
                          ) : host.ssh_error ? (
                            <Tooltip content={host.ssh_error}>
                              <Badge className={cn("text-white text-xs cursor-help", "bg-amber-600")}>unverified</Badge>
                            </Tooltip>
                          ) : (
                            <Badge className={cn("text-white text-xs", "bg-amber-600")}>unverified</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-slate-400 text-sm py-4">
                        Loading...
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
