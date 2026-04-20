"use client"

import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { PendingHostsTable } from "@/components/scans/pending-hosts-table"
import type { ScanConfig, PendingHost } from "@/lib/types"
import { useState } from "react"

export default function PendingReviewClientPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [approveLoading, setApproveLoading] = useState(false)
  const [dismissLoading, setDismissLoading] = useState(false)

  const { data: scan, isLoading: scanLoading } = useQuery<ScanConfig>({
    queryKey: ["scans", id],
    queryFn: () => apiFetch<ScanConfig>(`/api/scans/${id}`),
    enabled: !!id,
  })

  const {
    data: pending,
    isLoading: pendingLoading,
    error: pendingError,
  } = useQuery<PendingHost[]>({
    queryKey: ["scans", id, "pending"],
    queryFn: () => apiFetch<PendingHost[]>(`/api/scans/${id}/pending`),
    enabled: !!id,
    refetchInterval: 10000,
  })

  const showLoading = useDelayedLoading(scanLoading || pendingLoading)

  async function handleApprove(ids: number[]) {
    setApproveLoading(true)
    try {
      const result = await apiFetch<{ approved: number; skipped: number; skipped_ips: string[] }>(
        `/api/scans/${id}/pending/approve`,
        { method: "POST", body: JSON.stringify({ ids }) }
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scans", id, "pending"] }),
        queryClient.invalidateQueries({ queryKey: ["scans", id] }),
        queryClient.invalidateQueries({ queryKey: ["scans", "pending-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["hosts"] }),
      ])
      if (result.skipped > 0) {
        showSuccess(
          `Approved ${result.approved} host${result.approved !== 1 ? "s" : ""}. ${result.skipped} already existed.`
        )
      } else {
        showSuccess(
          `Approved ${result.approved} host${result.approved !== 1 ? "s" : ""}`
        )
      }
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Failed to approve hosts")
    } finally {
      setApproveLoading(false)
    }
  }

  async function handleDismiss(ids: number[]) {
    setDismissLoading(true)
    try {
      const result = await apiFetch<{ dismissed: number }>(
        `/api/scans/${id}/pending/dismiss`,
        { method: "POST", body: JSON.stringify({ ids }) }
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scans", id, "pending"] }),
        queryClient.invalidateQueries({ queryKey: ["scans", id] }),
        queryClient.invalidateQueries({ queryKey: ["scans", "pending-summary"] }),
      ])
      showSuccess(
        `Dismissed ${result.dismissed} host${result.dismissed !== 1 ? "s" : ""}`
      )
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Failed to dismiss hosts")
    } finally {
      setDismissLoading(false)
    }
  }

  const scanName = scan?.name ?? `Scan config #${id}`

  return (
    <div className="space-y-6">
      <Breadcrumb
        items={[
          { label: "Hosts", href: "/hosts" },
          { label: "Scans", href: "/hosts/scans" },
          { label: scanName, href: `/hosts/scans` },
          { label: "Pending Review" },
        ]}
      />

      <div>
        <h1 className="text-2xl font-bold text-white">
          Pending Review &mdash; {scanName}
        </h1>
        <p className="text-slate-400 text-sm mt-1">
          Hosts discovered by this scan config that are awaiting your review.
          Approve to add them to your inventory, or dismiss to ignore them.
        </p>
      </div>

      {showLoading && <TableSkeleton rows={4} columns={5} />}

      {!scanLoading && !pendingLoading && pendingError && (
        <div className="text-red-400 py-8 text-center">
          Failed to load pending hosts
        </div>
      )}

      {!scanLoading && !pendingLoading && !pendingError && pending?.length === 0 && (
        <div className="flex items-center justify-center">
          <div className="rounded-lg border border-slate-700 bg-slate-900 px-8 py-12 text-center max-w-sm w-full">
            <p className="text-slate-300 font-medium">No hosts pending review</p>
            <p className="text-slate-500 text-sm mt-1">
              No hosts pending review for this scan config.
            </p>
          </div>
        </div>
      )}

      {!scanLoading && !pendingLoading && !pendingError && pending && pending.length > 0 && (
        <PendingHostsTable
          rows={pending}
          onApprove={handleApprove}
          onDismiss={handleDismiss}
          approveLoading={approveLoading}
          dismissLoading={dismissLoading}
          showConfigColumn={false}
        />
      )}
    </div>
  )
}
