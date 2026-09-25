"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess, showError } from "@/lib/toast"
import { plural, shortAgo } from "@/lib/fleet"
import { Confirm, Table, Tag, Toolbar } from "@/components/ld"
import { ScanConfigDialog } from "@/components/scans/scan-config-dialog"
import type { ScanConfig } from "@/lib/types"

function formatSchedule(scan: ScanConfig): string {
  if (scan.interval_minutes != null) {
    const m = scan.interval_minutes
    if (m % (60 * 24) === 0) return `every ${m / (60 * 24)}d`
    if (m % 60 === 0) return `every ${m / 60}h`
    return `every ${m} min`
  }
  if (scan.cron_expression) return scan.cron_expression
  return "—"
}

function runStatus(scan: ScanConfig): "ok" | "error" | "running" | "never" {
  if (scan.last_run_status === "running") return "running"
  if (!scan.last_run_at) return "never"
  if (scan.last_run_status === "error") return "error"
  return "ok"
}

/** The recurring scan-schedule list, embedded in the Discovery screen's
 *  Scan schedules tab (no route of its own). */
export default function ScansPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingScan, setEditingScan] = useState<ScanConfig | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ScanConfig | null>(null)

  const { data: scans, isLoading, error } = useQuery<ScanConfig[]>({
    queryKey: ["scans"],
    queryFn: () => apiFetch<ScanConfig[]>("/api/scans"),
    refetchInterval: 10000,
  })

  const toggleMutation = useApiMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => apiFetch(`/api/scans/${id}`, { method: "PUT", body: JSON.stringify({ enabled }) }),
    invalidateKeys: [["scans"]],
  })
  const deleteMutation = useApiMutation({
    mutationFn: (id: number) => apiFetch(`/api/scans/${id}`, { method: "DELETE" }),
    invalidateKeys: [["scans"]],
    successMessage: "Scan config deleted",
    onSuccess: () => setDeleteTarget(null),
  })

  async function handleRun(scan: ScanConfig) {
    try {
      await apiFetch(`/api/scans/${scan.id}/run`, { method: "POST" })
      showSuccess(`Run triggered for "${scan.name}"`)
      await queryClient.invalidateQueries({ queryKey: ["scans"] })
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to trigger run")
    }
  }

  function openCreate() {
    setEditingScan(null)
    setDialogOpen(true)
  }
  function openEdit(scan: ScanConfig) {
    setEditingScan(scan)
    setDialogOpen(true)
  }

  const rows = scans ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar actions={<button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>add scan schedule</button>}>
        <span className="tt">{plural(rows.length, "schedule")} · recurring network scans</span>
      </Toolbar>

      {error && <span className="p-3.5 text-[11.5px] text-danger">Failed to load scan schedules</span>}

      <Table<ScanConfig>
        cols={[
          { k: "name", label: "name", w: "minmax(130px,1fr)", sortable: false, cell: (r) => <span className="font-medium text-text">{r.name}</span> },
          { k: "cidrs", label: "cidrs", w: "minmax(120px,1fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px] text-text-2">{r.cidrs.join(", ") || "—"}</span> },
          { k: "schedule", label: "schedule", w: "110px", sortable: false, cell: (r) => <span className="mono text-[11px] text-text-2">{formatSchedule(r)}</span> },
          { k: "mode", label: "mode", w: "90px", sortable: false, cell: (r) => <Tag tone={r.auto_add ? "ok" : undefined}>{r.auto_add ? "auto" : "pending"}</Tag> },
          {
            k: "last_run", label: "last run", w: "minmax(100px,1fr)", sortable: false,
            cell: (r) => {
              const s = runStatus(r)
              const tone = s === "ok" ? "ok" : s === "error" ? "danger" : s === "running" ? "sync" : undefined
              return (
                <span className="flex flex-col gap-0.5">
                  <Tag tone={tone}>{s === "running" ? "running…" : s}</Tag>
                  {s !== "never" && r.last_run_at && <span className="text-[10px] text-text-faint">{shortAgo(r.last_run_at)} ago</span>}
                </span>
              )
            },
          },
          { k: "enabled", label: "enabled", w: "70px", sortable: false, cell: (r) => <input type="checkbox" checked={r.enabled} disabled={toggleMutation.isPending} onChange={() => toggleMutation.mutate({ id: r.id, enabled: !r.enabled })} style={{ accentColor: "var(--accent)" }} aria-label={`${r.enabled ? "disable" : "enable"} ${r.name}`} /> },
          {
            k: "actions", label: "", w: "180px", right: true, sortable: false,
            cell: (r) => (
              <span className="flex flex-wrap justify-end gap-0.5">
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(r)}>edit</button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => handleRun(r)}>run now</button>
                {(r.last_run_hosts_pending ?? 0) > 0 && <Link href={`/discovery?tab=pending&scan=${r.id}`} className="btn btn-sm btn-ghost hover:no-underline">pending</Link>}
                <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setDeleteTarget(r)}>delete</button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(r) => r.id}
        loading={isLoading}
        empty="No scan schedules yet. Add one to automatically discover hosts on your network."
      />

      {dialogOpen && (
        <ScanConfigDialog
          open={dialogOpen}
          onOpenChange={(open) => { setDialogOpen(open); if (!open) setEditingScan(null) }}
          config={editingScan ?? undefined}
        />
      )}

      {deleteTarget && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          title="Delete scan schedule"
          description={`Delete "${deleteTarget.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
        />
      )}
    </div>
  )
}
