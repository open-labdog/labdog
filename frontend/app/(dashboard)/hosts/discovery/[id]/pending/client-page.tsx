"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import { plural, shortAgo } from "@/lib/fleet"
import { Table, Tag, Toolbar } from "@/components/ld"
import { SSH_ERROR_LABELS } from "@/lib/types"
import type { PendingHost, ScanConfig } from "@/lib/types"

/** One scan config's pending queue — embedded in the Discovery screen's
 *  Pending approval tab under `?scan=<id>`. */
export default function PendingReviewClientPage({ scanId }: { scanId: number }) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string | number>>(new Set())
  const [busy, setBusy] = useState<"approve" | "dismiss" | null>(null)

  const { data: scan } = useQuery<ScanConfig>({ queryKey: ["scans", scanId], queryFn: () => apiFetch<ScanConfig>(`/api/scans/${scanId}`) })
  const { data: pending, isLoading } = useQuery<PendingHost[]>({
    queryKey: ["scans", scanId, "pending"],
    queryFn: () => apiFetch<PendingHost[]>(`/api/scans/${scanId}/pending`),
    refetchInterval: 10000,
  })

  async function act(kind: "approve" | "dismiss") {
    if (selected.size === 0) return
    setBusy(kind)
    try {
      const result = await apiFetch<{ approved?: number; dismissed?: number; skipped?: number }>(`/api/scans/${scanId}/pending/${kind}`, { method: "POST", body: JSON.stringify({ ids: Array.from(selected) }) })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scans", scanId, "pending"] }),
        queryClient.invalidateQueries({ queryKey: ["scans", scanId] }),
        queryClient.invalidateQueries({ queryKey: ["scans", "pending-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["hosts"] }),
      ])
      setSelected(new Set())
      const n = kind === "approve" ? (result.approved ?? 0) : (result.dismissed ?? 0)
      if (kind === "approve" && (result.skipped ?? 0) > 0) showSuccess(`Approved ${plural(n, "host")}. ${result.skipped} already existed.`)
      else showSuccess(`${kind === "approve" ? "Approved" : "Dismissed"} ${plural(n, "host")}`)
    } catch (e) {
      showError(e instanceof Error ? e.message : `Failed to ${kind} hosts`)
    } finally {
      setBusy(null)
    }
  }

  const scanName = scan?.name ?? `Scan config #${scanId}`
  const rows = pending ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={selected.size === 0 || !!busy} onClick={() => act("dismiss")}>
              {busy === "dismiss" ? "dismissing…" : "dismiss selected"}
            </button>
            <button type="button" className="btn btn-sm btn-primary" disabled={selected.size === 0 || !!busy} onClick={() => act("approve")}>
              {busy === "approve" ? "approving…" : "approve selected"}
            </button>
          </>
        }
      >
        <span className="tt">found by <span className="mono text-text-2">{scanName}</span></span>
        <Link href="/discovery?tab=pending" className="tt text-ld-accent hover:no-underline">every scan →</Link>
      </Toolbar>

      <Table<PendingHost>
        cols={[
          { k: "ip", label: "ip address", w: "140px", sortable: false, cell: (h) => <span className="mono text-text">{h.ip_address}</span> },
          { k: "hostname", label: "hostname", w: "minmax(140px,1fr)", sortable: false, cell: (h) => <span className="text-text-2">{h.hostname ?? "—"}</span> },
          { k: "discovered", label: "discovered", w: "100px", sortable: false, cell: (h) => <span className="mono num text-[11px] text-text-3">{shortAgo(h.discovered_at)} ago</span> },
          { k: "ssh", label: "ssh", w: "110px", sortable: false, cell: (h) => (h.ssh_verified ? <Tag tone="ok">verified</Tag> : h.ssh_error ? <Tag tone="warn" title={SSH_ERROR_LABELS[h.ssh_error]}>{SSH_ERROR_LABELS[h.ssh_error]}</Tag> : <Tag tone="warn">unverified</Tag>) },
        ]}
        rows={rows}
        keyOf={(h) => h.id}
        selected={selected}
        onSelect={setSelected}
        loading={isLoading}
        empty="No hosts pending review for this scan config."
      />
    </div>
  )
}
