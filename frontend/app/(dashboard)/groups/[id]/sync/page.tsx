"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { apiFetch } from "@/lib/api"

interface RuleDiff {
  rule: string
  status: "add" | "remove" | "unchanged"
}

interface HostDiff {
  host_id: number
  hostname: string
  diffs: RuleDiff[]
}

interface PlanResponse {
  hosts: HostDiff[]
}

interface SyncResponse {
  job_id: string
}

interface JobStatus {
  id: string
  status: "pending" | "running" | "success" | "failed"
  message?: string
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

function DiffLine({ diff }: { diff: RuleDiff }) {
  if (diff.status === "add") {
    return (
      <div className="font-mono text-xs text-green-400 bg-green-950/30 px-3 py-0.5 rounded">
        + {diff.rule}
      </div>
    )
  }
  if (diff.status === "remove") {
    return (
      <div className="font-mono text-xs text-red-400 bg-red-950/30 px-3 py-0.5 rounded">
        - {diff.rule}
      </div>
    )
  }
  return (
    <div className="font-mono text-xs text-slate-500 px-3 py-0.5">
      &nbsp;&nbsp;{diff.rule}
    </div>
  )
}

function HostDiffCard({ host }: { host: HostDiff }) {
  const [expanded, setExpanded] = useState(true)
  const addCount = host.diffs.filter((d) => d.status === "add").length
  const removeCount = host.diffs.filter((d) => d.status === "remove").length

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-800 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-white">{host.hostname}</span>
          <span className="text-xs text-green-400">+{addCount}</span>
          <span className="text-xs text-red-400">-{removeCount}</span>
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
          {host.diffs.length === 0 ? (
            <div className="text-slate-500 text-xs px-3 py-2">No changes</div>
          ) : (
            host.diffs.map((diff, i) => <DiffLine key={i} diff={diff} />)
          )}
        </div>
      )}
    </div>
  )
}

export default function GroupSyncPage() {
  const params = useParams()
  const id = params.id as string

  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [confirmOpen, setConfirmOpen] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)

  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const pollFailures = useRef(0)

  const handlePreview = async () => {
    setPreviewing(true)
    setPreviewError(null)
    setPlan(null)
    setJobId(null)
    setJobStatus(null)
    setPollError(null)
    pollFailures.current = 0
    try {
      const data = await apiFetch<PlanResponse>(`/api/sync/groups/${id}/plan`, {
        method: "POST",
      })
      setPlan(data)
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Preview failed")
    } finally {
      setPreviewing(false)
    }
  }

  const handleApplyConfirm = async () => {
    setConfirmOpen(false)
    setApplying(true)
    setApplyError(null)
    setPollError(null)
    pollFailures.current = 0
    try {
      const data = await apiFetch<SyncResponse>(`/api/sync/groups/${id}/sync`, {
        method: "POST",
      })
      setJobId(data.job_id)
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "Apply failed")
      setApplying(false)
    }
  }

  const MAX_POLL_FAILURES = 5

  const pollJob = useCallback(async (jid: string) => {
    try {
      const data = await apiFetch<JobStatus>(`/api/sync/jobs/${jid}`)
      pollFailures.current = 0
      setJobStatus(data)
      if (data.status === "success" || data.status === "failed") {
        setApplying(false)
      }
    } catch (err) {
      pollFailures.current += 1
      if (pollFailures.current >= MAX_POLL_FAILURES) {
        setPollError(err instanceof Error ? err.message : "Lost connection while polling job status")
        setApplying(false)
      }
    }
  }, [])

  useEffect(() => {
    if (!jobId) return
    const terminal = jobStatus?.status === "success" || jobStatus?.status === "failed"
    if (terminal || pollError) return

    const interval = setInterval(() => pollJob(jobId), 3000)
    // Poll immediately
    pollJob(jobId)
    return () => clearInterval(interval)
  }, [jobId, jobStatus?.status, pollError, pollJob])

  const hasChanges = plan && plan.hosts?.some(
    (h) => h.diffs.some((d) => d.status !== "unchanged")
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Sync Group</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        <div className="flex gap-3">
          <Button
            onClick={handlePreview}
            disabled={previewing}
            variant="outline"
          >
            {previewing ? "Previewing…" : "Preview Changes"}
          </Button>
          <Button
            onClick={() => setConfirmOpen(true)}
            disabled={!plan || applying}
          >
            {applying ? "Applying…" : "Apply Changes"}
          </Button>
        </div>
      </div>

      {previewError && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          {previewError}
        </div>
      )}

      {applyError && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          {applyError}
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
          {jobStatus.message && (
            <span className="text-slate-400 text-xs ml-2">{jobStatus.message}</span>
          )}
        </div>
      )}

      {previewing && (
        <div className="text-slate-400 py-8 text-center">Loading preview…</div>
      )}

      {plan && !previewing && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-white">Planned Changes</h2>
            {!hasChanges && (
              <span className="text-sm text-green-400">All hosts in sync</span>
            )}
          </div>
          {plan.hosts.length === 0 ? (
            <div className="text-slate-400 py-8 text-center">No hosts in this group</div>
          ) : (
            plan.hosts.map((host) => (
              <HostDiffCard key={host.host_id} host={host} />
            ))
          )}
        </div>
      )}

      {!plan && !previewing && (
        <div className="text-slate-400 py-12 text-center">
          Click <strong className="text-white">Preview Changes</strong> to see what will be applied.
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Apply</DialogTitle>
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
