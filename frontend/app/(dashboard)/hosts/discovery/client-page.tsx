"use client"

import { useState, useRef, useLayoutEffect, useEffect, useSyncExternalStore } from "react"
import { createPortal } from "react-dom"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ScanIcon, PlayIcon, PencilIcon, Trash2Icon, ClockIcon, ChevronDownIcon, SearchIcon, InboxIcon } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card"
import { DataTable } from "@/components/ui/data-table"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading, formatRelativeTime, cn } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import type { ScanConfig, PendingSummary } from "@/lib/types"
import { ScanConfigDialog } from "@/components/scans/scan-config-dialog"

// ── helpers ──────────────────────────────────────────────────────────────────

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

function CidrCell({ cidrs }: { cidrs: string[] }) {
  if (cidrs.length === 0) return <span className="text-slate-500">—</span>
  const first = cidrs[0]
  const extra = cidrs.length - 1
  return (
    <span className="font-mono text-slate-300 text-xs">
      {first}
      {extra > 0 && (
        <span className="ml-1 text-slate-500">+{extra} more</span>
      )}
    </span>
  )
}

type RunStatus = "ok" | "error" | "running" | "never"

function runStatus(scan: ScanConfig): RunStatus {
  if (scan.last_run_status === "running") return "running"
  if (!scan.last_run_at) return "never"
  if (scan.last_run_status === "error") return "error"
  return "ok"
}

const RUN_STATUS_BADGE: Record<RunStatus, { label: string; className: string }> = {
  ok: { label: "OK", className: "bg-green-600 text-white" },
  error: { label: "Error", className: "bg-red-600 text-white" },
  running: { label: "Running\u2026", className: "bg-amber-600 text-white" },
  never: { label: "Never", className: "bg-slate-600 text-slate-300" },
}

function LastRunCell({ scan }: { scan: ScanConfig }) {
  const status = runStatus(scan)
  const { label, className } = RUN_STATUS_BADGE[status]
  return (
    <div className="flex flex-col gap-0.5">
      <Badge className={cn("text-[11px] px-1.5 py-0 w-fit", className)}>{label}</Badge>
      {status !== "never" && status !== "running" && scan.last_run_at && (
        <span className="text-[11px] text-slate-500">{formatRelativeTime(scan.last_run_at)}</span>
      )}
      {status === "running" && scan.last_run_at && (
        <span className="text-[11px] text-slate-500">{formatRelativeTime(scan.last_run_at)}</span>
      )}
    </div>
  )
}

function EnabledToggle({
  scan,
  onToggle,
  disabled,
}: {
  scan: ScanConfig
  onToggle: (scan: ScanConfig) => void
  disabled: boolean
}) {
  const on = scan.enabled
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onToggle(scan)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30 disabled:cursor-not-allowed disabled:opacity-50",
        on ? "bg-green-600" : "bg-slate-600"
      )}
    >
      <span
        className={cn(
          "pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
          on ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  )
}

// ── row-action menu ───────────────────────────────────────────────────────────

function RowActions({
  scan,
  onEdit,
  onDelete,
  onRun,
}: {
  scan: ScanConfig
  onEdit: (scan: ScanConfig) => void
  onDelete: (scan: ScanConfig) => void
  onRun: (scan: ScanConfig) => void
}) {
  const [open, setOpen] = useState(false)
  const [menuPos, setMenuPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 })
  const triggerRef = useRef<HTMLDivElement>(null)
  const hasPending = (scan.last_run_hosts_pending ?? 0) > 0

  // Guard SSR: only portal once the client has mounted
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  )

  // Compute fixed-position coords from the trigger's bounding rect whenever the menu opens
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    setMenuPos({
      top: rect.bottom + 4,
      right: window.innerWidth - rect.right,
    })
  }, [open])

  // Recompute on scroll/resize so the menu tracks the trigger
  useEffect(() => {
    if (!open || !triggerRef.current) return
    function recompute() {
      if (!triggerRef.current) return
      const rect = triggerRef.current.getBoundingClientRect()
      setMenuPos({
        top: rect.bottom + 4,
        right: window.innerWidth - rect.right,
      })
    }
    window.addEventListener("scroll", recompute, { capture: true, passive: true })
    window.addEventListener("resize", recompute, { passive: true })
    return () => {
      window.removeEventListener("scroll", recompute, { capture: true })
      window.removeEventListener("resize", recompute)
    }
  }, [open])

  const portal =
    mounted && open
      ? createPortal(
          <>
            {/* backdrop */}
            <div
              style={{ position: "fixed", inset: 0, zIndex: 40 }}
              onClick={() => setOpen(false)}
            />
            {/* menu */}
            <div
              style={{
                position: "fixed",
                top: menuPos.top,
                right: menuPos.right,
                zIndex: 50,
              }}
              className="min-w-[160px] rounded-lg border border-slate-700 bg-slate-900 py-1 shadow-xl"
            >
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
                onClick={() => { setOpen(false); onEdit(scan) }}
              >
                <PencilIcon className="w-3.5 h-3.5" /> Edit
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
                onClick={() => { setOpen(false); onRun(scan) }}
              >
                <PlayIcon className="w-3.5 h-3.5" /> Run now
              </button>
              {hasPending && (
                <Link
                  href={`/hosts/discovery/${scan.id}/pending`}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
                  onClick={() => setOpen(false)}
                >
                  <ClockIcon className="w-3.5 h-3.5" /> View pending
                </Link>
              )}
              <div className="my-1 border-t border-slate-700" />
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-red-400 hover:bg-slate-800"
                onClick={() => { setOpen(false); onDelete(scan) }}
              >
                <Trash2Icon className="w-3.5 h-3.5" /> Delete
              </button>
            </div>
          </>,
          document.body
        )
      : null

  return (
    <div className="relative" ref={triggerRef}>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0"
        onClick={() => setOpen((v) => !v)}
        aria-label="Row actions"
      >
        <ChevronDownIcon className="w-3.5 h-3.5" />
      </Button>
      {portal}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

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
  const showLoading = useDelayedLoading(isLoading)

  const { data: pendingSummary } = useQuery<PendingSummary>({
    queryKey: ["scans", "pending-summary"],
    queryFn: () => apiFetch<PendingSummary>("/api/scans/pending-summary"),
    refetchInterval: 30_000,
    retry: false,
  })
  const pendingTotal = pendingSummary?.total ?? 0

  // ── toggle enabled ──────────────────────────────────────────────────────────
  const toggleMutation = useApiMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      apiFetch(`/api/scans/${id}`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      }),
    invalidateKeys: [["scans"]],
    // no successMessage — silent toggle per UX spec
  })

  function handleToggle(scan: ScanConfig) {
    toggleMutation.mutate({ id: scan.id, enabled: !scan.enabled })
  }

  // ── delete ──────────────────────────────────────────────────────────────────
  const deleteMutation = useApiMutation({
    mutationFn: (id: number) => apiFetch(`/api/scans/${id}`, { method: "DELETE" }),
    invalidateKeys: [["scans"]],
    successMessage: "Scan config deleted",
    onSuccess: () => setDeleteTarget(null),
  })

  // ── run now ─────────────────────────────────────────────────────────────────
  async function handleRun(scan: ScanConfig) {
    try {
      await apiFetch(`/api/scans/${scan.id}/run`, { method: "POST" })
      showSuccess(`Run triggered for "${scan.name}"`)
      await queryClient.invalidateQueries({ queryKey: ["scans"] })
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Failed to trigger run")
    }
  }

  // ── open dialogs ────────────────────────────────────────────────────────────
  function openCreate() {
    setEditingScan(null)
    setDialogOpen(true)
  }

  function openEdit(scan: ScanConfig) {
    setEditingScan(scan)
    setDialogOpen(true)
  }

  // ── columns ─────────────────────────────────────────────────────────────────
  const columns = [
    {
      key: "name",
      label: "Name",
      accessor: (r: ScanConfig) => r.name,
      cell: (r: ScanConfig) => (
        <span className="text-sm font-medium text-white">{r.name}</span>
      ),
      defaultWidth: 180,
      filter: { type: "text" as const },
    },
    {
      key: "cidrs",
      label: "CIDRs",
      accessor: (r: ScanConfig) => r.cidrs.join(", "),
      cell: (r: ScanConfig) => <CidrCell cidrs={r.cidrs} />,
      defaultWidth: 200,
      filter: { type: "text" as const, placeholder: "e.g. 10.0.0.0/8" },
    },
    {
      key: "schedule",
      label: "Schedule",
      accessor: (r: ScanConfig) => formatSchedule(r),
      cell: (r: ScanConfig) => (
        <span className="text-sm text-slate-300">{formatSchedule(r)}</span>
      ),
      defaultWidth: 130,
    },
    {
      key: "auto_add",
      label: "Auto-add",
      accessor: (r: ScanConfig) => r.auto_add,
      cell: (r: ScanConfig) =>
        r.auto_add ? (
          <Badge className="bg-green-600 text-white text-[11px]">Auto</Badge>
        ) : (
          <Badge className="bg-blue-600 text-white text-[11px]">Pending</Badge>
        ),
      defaultWidth: 90,
      filter: {
        type: "enum" as const,
        options: [
          { label: "Auto", value: "true" },
          { label: "Pending", value: "false" },
        ],
      },
    },
    {
      key: "last_run",
      label: "Last Run",
      accessor: (r: ScanConfig) => r.last_run_at ?? "",
      cell: (r: ScanConfig) => <LastRunCell scan={r} />,
      defaultWidth: 120,
    },
    {
      key: "enabled",
      label: "Enabled",
      accessor: (r: ScanConfig) => r.enabled,
      cell: (r: ScanConfig) => (
        <EnabledToggle
          scan={r}
          onToggle={handleToggle}
          disabled={toggleMutation.isPending}
        />
      ),
      defaultWidth: 80,
      sortable: false as const,
    },
    {
      key: "actions",
      label: "",
      cell: (r: ScanConfig) => (
        <RowActions
          scan={r}
          onEdit={openEdit}
          onDelete={setDeleteTarget}
          onRun={handleRun}
        />
      ),
      defaultWidth: 60,
      resizable: false as const,
      sortable: false as const,
    },
  ]

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: "Discovery" }]} />

      <div>
        <h1 className="text-2xl font-bold text-white">Discovery</h1>
        <p className="text-slate-400 text-sm mt-1">
          Find and onboard hosts via one-shot scans, schedules, or the approval inbox
        </p>
      </div>

      {/* ── Action tiles ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card className="bg-slate-900 border border-slate-700 ring-0">
          <CardHeader className="px-4">
            <CardTitle className="flex items-center gap-2 text-white">
              <SearchIcon className="w-4 h-4 text-slate-400" />
              Scan now
            </CardTitle>
            <CardDescription className="text-slate-400">
              Scan a network range right now
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1" />
          <CardFooter className="justify-end border-0 bg-transparent px-4 pb-4 pt-2">
            <Link href="/hosts/discover" className={cn(buttonVariants())}>
              Scan now
            </Link>
          </CardFooter>
        </Card>

        <Card className="bg-slate-900 border border-slate-700 ring-0">
          <CardHeader className="px-4">
            <CardTitle className="flex items-center gap-2 text-white">
              <InboxIcon className="w-4 h-4 text-slate-400" />
              Review pending
            </CardTitle>
            <CardDescription className="text-slate-400">
              Hosts awaiting approval before joining the fleet
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 px-4">
            {pendingTotal > 0 && (
              <Badge className="bg-amber-600 text-white text-[11px]">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-200 mr-1.5" />
                {pendingTotal} pending
              </Badge>
            )}
          </CardContent>
          <CardFooter className="justify-end border-0 bg-transparent px-4 pb-4 pt-2">
            <Link href="/hosts/pending" className={cn(buttonVariants({ variant: "outline" }))}>
              Review pending
            </Link>
          </CardFooter>
        </Card>
      </div>

      {/* ── Scan schedules list ──────────────────────────────────────── */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white">Scan schedules</h2>
            <p className="text-slate-400 text-sm mt-0.5">
              Recurring network scans that discover and onboard hosts
            </p>
          </div>
          <Button onClick={openCreate}>Add Scan Schedule</Button>
        </div>

      {showLoading && <TableSkeleton rows={3} columns={7} />}
      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load scan schedules</div>
      )}

      {!isLoading && !error && (
        <DataTable<ScanConfig>
          tableId="scan-configs-v1"
          data={scans}
          emptyMessage={
            <div className="flex flex-col items-center gap-3 py-8 mx-auto" style={{ maxWidth: "28rem" }}>
              <ScanIcon className="w-10 h-10 text-slate-700" />
              <div className="text-center">
                <p className="text-slate-300 font-medium">No scan schedules yet</p>
                <p className="text-slate-500 text-sm mt-1">
                  Create a scan schedule to automatically discover hosts on your network.
                </p>
              </div>
              <Button onClick={openCreate} className="mt-2">
                Add Scan Schedule
              </Button>
            </div>
          }
          getRowKey={(r) => r.id}
          columns={columns}
        />
      )}
      </div>

      {/* Create / Edit dialog — T8 owns this component */}
      <ScanConfigDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) setEditingScan(null)
        }}
        config={editingScan ?? undefined}
      />

      {/* Delete confirmation */}
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        title="Delete Scan Schedule"
        description={
          deleteTarget
            ? `Delete "${deleteTarget.name}"? This cannot be undone.`
            : "Delete this scan schedule? This cannot be undone."
        }
        confirmLabel={deleteMutation.isPending ? "Deleting\u2026" : "Delete"}
        variant="destructive"
        loading={deleteMutation.isPending}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
        }}
      />
    </div>
  )
}
