"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { showSuccess, showError } from "@/lib/toast"
import { plural, shortAgo } from "@/lib/fleet"
import { Table, Tag, Toolbar } from "@/components/ld"
import { SSH_ERROR_LABELS } from "@/lib/types"
import type { PendingHostFleet } from "@/lib/types"

/** The fleet-wide pending queue, embedded in the Discovery screen's
 *  Pending approval tab (no route of its own). */
export default function PendingApprovalPage() {
  const [selected, setSelected] = useState<Set<string | number>>(new Set())
  const [busy, setBusy] = useState<"approve" | "dismiss" | null>(null)
  const queryClient = useQueryClient()

  const { data: pendingHosts, isLoading } = useQuery<PendingHostFleet[]>({
    queryKey: ["scans", "pending"],
    queryFn: () => apiFetch<PendingHostFleet[]>("/api/scans/pending"),
    refetchInterval: 30000,
  })

  async function act(kind: "approve" | "dismiss") {
    if (selected.size === 0) return
    const selectedHosts = (pendingHosts ?? []).filter((h) => selected.has(h.id))
    const byConfig = new Map<number, number[]>()
    for (const h of selectedHosts) {
      const ids = byConfig.get(h.scan_config_id) ?? []
      ids.push(h.id)
      byConfig.set(h.scan_config_id, ids)
    }

    setBusy(kind)
    const results = await Promise.allSettled(
      Array.from(byConfig.entries()).map(([configId, ids]) =>
        apiFetch<{ approved?: number; dismissed?: number; skipped?: number }>(`/api/scans/${configId}/pending/${kind}`, { method: "POST", body: JSON.stringify({ ids }) }),
      ),
    )
    let total = 0
    let totalSkipped = 0
    let hadError = false
    for (const r of results) {
      if (r.status === "fulfilled") {
        total += kind === "approve" ? (r.value.approved ?? 0) : (r.value.dismissed ?? 0)
        totalSkipped += r.value.skipped ?? 0
      } else {
        hadError = true
      }
    }

    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scans", "pending-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["scans", "pending"] }),
      queryClient.invalidateQueries({ queryKey: ["hosts-summary"] }),
    ])
    setSelected(new Set())
    setBusy(null)

    if (hadError) showError(`Some ${kind === "approve" ? "approvals" : "dismissals"} failed. Please try again.`)
    else if (kind === "approve" && totalSkipped > 0) showSuccess(`Approved ${plural(total, "host")} (${totalSkipped} skipped as duplicate${totalSkipped === 1 ? "" : "s"})`)
    else showSuccess(`${kind === "approve" ? "Approved" : "Dismissed"} ${plural(total, "host")}`)
  }

  const rows = pendingHosts ?? []
  const uniqueConfigCount = new Set(rows.map((h) => h.scan_config_id)).size

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
        <span className="tt">{plural(rows.length, "host")} pending{uniqueConfigCount > 0 ? ` · from ${plural(uniqueConfigCount, "scan config")}` : ""}</span>
      </Toolbar>

      <Table<PendingHostFleet>
        cols={[
          { k: "ip", label: "ip address", w: "140px", sortable: false, cell: (h) => <span className="mono text-text">{h.ip_address}</span> },
          { k: "hostname", label: "hostname", w: "minmax(140px,1fr)", sortable: false, cell: (h) => <span className="text-text-2">{h.hostname ?? "—"}</span> },
          { k: "config", label: "from config", w: "minmax(120px,1fr)", sortable: false, cell: (h) => <Link href={`/discovery?tab=pending&scan=${h.scan_config_id}`} className="tt text-ld-accent hover:no-underline" onClick={(e) => e.stopPropagation()}>{h.scan_config_name}</Link> },
          { k: "discovered", label: "discovered", w: "90px", sortable: false, cell: (h) => <span className="mono num text-[11px] text-text-3">{shortAgo(h.discovered_at)} ago</span> },
          { k: "ssh", label: "ssh", w: "110px", sortable: false, cell: (h) => (h.ssh_verified ? <Tag tone="ok">verified</Tag> : h.ssh_error ? <Tag tone="warn" title={SSH_ERROR_LABELS[h.ssh_error]}>{SSH_ERROR_LABELS[h.ssh_error]}</Tag> : <Tag tone="warn">unverified</Tag>) },
        ]}
        rows={rows}
        keyOf={(h) => h.id}
        selected={selected}
        onSelect={setSelected}
        loading={isLoading}
        empty="No hosts awaiting approval. New discoveries will appear here when scan configs find hosts with pending mode."
      />
    </div>
  )
}
