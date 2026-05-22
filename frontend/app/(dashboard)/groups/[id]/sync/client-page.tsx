"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { CardSkeleton } from "@/components/ui/skeleton"
import type { HostGroup } from "@/lib/types"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"

interface RuleDiffItem {
  action: string
  protocol: string
  direction: string
  source_cidr: string | null
  destination_cidr: string | null
  port_start: number | null
  port_end: number | null
  comment: string | null
  is_system: boolean
}

interface HostDiff {
  host_id: number
  hostname: string
  has_changes: boolean
  rules_to_add: RuleDiffItem[]
  rules_to_remove: RuleDiffItem[]
  rules_unchanged: RuleDiffItem[]
}

interface SyncResponse {
  job_id: string
}

interface JobStatus {
  id: string
  status: "pending" | "running" | "success" | "failed"
  message?: string
  // Set when ``status='pending'`` because another op on the host was
  // running -- mirrors ActionRun.pending_reason. Backend writes a
  // human-readable string like "Waiting for sync 47 on host node-1".
  pending_reason?: string | null
}

function StatusIcon({ status }: { status: JobStatus["status"] }) {
  if (status === "pending") {
    return (
      <span className="inline-flex items-center gap-2 text-blue-400">
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
        </svg>
        Pending
      </span>
    )
  }
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-2 text-amber-400">
        <svg className="animate-pulse h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="12" r="10" />
        </svg>
        Running…
      </span>
    )
  }
  if (status === "success") {
    return (
      <span className="inline-flex items-center gap-2 text-green-400">
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
        Success
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-2 text-red-400">
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
      Failed
    </span>
  )
}

function formatRule(r: RuleDiffItem): string {
  const port = r.port_start
    ? r.port_end && r.port_end !== r.port_start ? `${r.port_start}-${r.port_end}` : `${r.port_start}`
    : "any"
  // Mirror the nftables renderer fallback (backend/app/rules/renderers/nftables.py:33-37):
  // empty/null user-authored comments still land on the host as "Managed by LabDog".
  const comment = r.comment || "Managed by LabDog"
  return `${r.action} ${r.protocol} ${r.direction} ${r.source_cidr ?? "any"} → ${r.destination_cidr ?? "any"} port=${port} (${comment})`
}

function DiffLine({ rule, status }: { rule: RuleDiffItem; status: "add" | "remove" | "unchanged" }) {
  if (status === "add") {
    return (
      <div className="font-mono text-xs text-green-400 bg-green-950/30 px-3 py-0.5 rounded">
        + {formatRule(rule)}
      </div>
    )
  }
  if (status === "remove") {
    return (
      <div className="font-mono text-xs text-red-400 bg-red-950/30 px-3 py-0.5 rounded">
        - {formatRule(rule)}
      </div>
    )
  }
  return (
    <div className="font-mono text-xs text-slate-500 px-3 py-0.5">
      &nbsp;&nbsp;{formatRule(rule)}
    </div>
  )
}

function HostDiffCard({ host }: { host: HostDiff }) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-800 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-white">{host.hostname}</span>
          {host.rules_to_add.length > 0 && <span className="text-xs text-green-400">+{host.rules_to_add.length}</span>}
          {host.rules_to_remove.length > 0 && <span className="text-xs text-red-400">-{host.rules_to_remove.length}</span>}
          {!host.has_changes && <span className="text-xs text-slate-500">no changes</span>}
        </div>
        <svg
          className={`h-4 w-4 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="border-t border-slate-700 p-3 space-y-0.5 max-h-64 overflow-y-auto">
          {!host.has_changes && host.rules_unchanged.length === 0 ? (
            <div className="text-slate-500 text-xs px-3 py-2">No rules configured</div>
          ) : (
            <>
              {host.rules_to_add.map((r, i) => <DiffLine key={`a${i}`} rule={r} status="add" />)}
              {host.rules_to_remove.map((r, i) => <DiffLine key={`r${i}`} rule={r} status="remove" />)}
              {host.rules_unchanged.map((r, i) => <DiffLine key={`u${i}`} rule={r} status="unchanged" />)}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function GroupSyncPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = params.id as string

  const [plan, setPlan] = useState<HostDiff[] | null>(null)

  const [confirmOpen, setConfirmOpen] = useState(false)

  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const pollFailures = useRef(0)

  const previewMutation = useApiMutation<HostDiff[]>({
    mutationFn: () => apiFetch<HostDiff[]>(`/api/sync/groups/${id}/plan`, { method: "POST" }),
    onSuccess: (data) => setPlan(data),
  })

  const syncMutation = useApiMutation<SyncResponse>({
    mutationFn: () => apiFetch<SyncResponse>(`/api/sync/groups/${id}/sync`, { method: "POST" }),
    onSuccess: (data) => setJobId(data.job_id),
  })

  const handlePreview = () => {
    setPlan(null)
    setJobId(null)
    setJobStatus(null)
    setPollError(null)
    pollFailures.current = 0
    previewMutation.mutate(undefined as never)
  }

  const handleApplyConfirm = () => {
    setConfirmOpen(false)
    setPollError(null)
    pollFailures.current = 0
    syncMutation.mutate(undefined as never)
  }

  const MAX_POLL_FAILURES = 5

  const pollJob = useCallback(async (jid: string) => {
    try {
      const data = await apiFetch<JobStatus>(`/api/sync/jobs/${jid}`)
      pollFailures.current = 0
      setJobStatus(data)
      if (data.status === "success" || data.status === "failed") {
        syncMutation.reset()
      }
    } catch (err) {
      pollFailures.current += 1
      if (pollFailures.current >= MAX_POLL_FAILURES) {
        setPollError(err instanceof Error ? err.message : "Lost connection while polling job status")
        syncMutation.reset()
      }
    }
  }, [syncMutation])

  useEffect(() => {
    if (!jobId) return
    const terminal = jobStatus?.status === "success" || jobStatus?.status === "failed"
    if (terminal || pollError) return

    const interval = setInterval(() => pollJob(jobId), 3000)
    const raf = requestAnimationFrame(() => pollJob(jobId))
    return () => { clearInterval(interval); cancelAnimationFrame(raf) }
  }, [jobId, jobStatus?.status, pollError, pollJob])

  const hasChanges = plan && plan.some((h) => h.has_changes)

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  return (
    <div className="space-y-6">
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Firewall Sync" }]} />}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Firewall Sync</h1>
          <p className="text-sm text-slate-400 mt-1">
            Previews and applies <strong className="text-slate-300">firewall rule</strong>{" "}
            changes only. Services, packages, /etc/hosts entries, cron jobs, users, DNS resolver, and CA certs sync from each module&apos;s own tab.
          </p>
        </div>
        <div className="flex gap-3 shrink-0">
          <Button
            onClick={handlePreview}
            disabled={previewMutation.isPending}
            variant="outline"
          >
            {previewMutation.isPending ? "Previewing…" : "Preview Changes"}
          </Button>
          <Button
            onClick={() => setConfirmOpen(true)}
            disabled={!plan || syncMutation.isPending}
          >
            {syncMutation.isPending ? "Applying…" : "Apply Changes"}
          </Button>
        </div>
      </div>

      {previewMutation.error && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          {previewMutation.error.message}
        </div>
      )}

      {syncMutation.error && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          {syncMutation.error.message}
        </div>
      )}

      {pollError && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          Polling stopped: {pollError}
        </div>
      )}

      {jobStatus && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 flex items-center gap-3">
          <span className="text-slate-400 text-sm">Job status:</span>
          <StatusIcon status={jobStatus.status} />
          {jobStatus.status === "pending" && jobStatus.pending_reason && (
            <span className="text-amber-300 text-xs ml-2" title={jobStatus.pending_reason}>
              {jobStatus.pending_reason}
            </span>
          )}
          {jobStatus.message && (
            <span className="text-slate-400 text-xs ml-2">{jobStatus.message}</span>
          )}
        </div>
      )}

      {previewMutation.isPending && <CardSkeleton />}

      {plan && !previewMutation.isPending && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-white">Preview</h2>
            {!hasChanges && (
              <span className="text-sm text-green-400">All hosts in sync</span>
            )}
          </div>
          {plan.length === 0 ? (
            <div className="text-slate-400 py-8 text-center">No hosts in this group</div>
          ) : (
            plan.map((host) => (
              <HostDiffCard key={host.host_id} host={host} />
            ))
          )}
        </div>
      )}

      {!plan && !previewMutation.isPending && (
        <div className="text-slate-400 py-12 text-center">
          Click <strong className="text-white">Preview Changes</strong> to see what will be applied.
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Changes</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-slate-400">
            This will apply the planned firewall changes to all hosts in this group.
            Are you sure you want to proceed?
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleApplyConfirm}>
              Apply Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
