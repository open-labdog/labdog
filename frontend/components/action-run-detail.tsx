"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { RunStatusBadge } from "@/components/status-badge"
import { API_BASE, apiFetch } from "@/lib/api"
import { toast } from "sonner"
import type { ActionRun, ActionHostRun } from "@/lib/types"

interface ActionRunDetailProps {
  runId: number
  backHref: string       // e.g. "/hosts/5?tab=actions" or "/groups/3?tab=actions"
  backLabel: string      // e.g. "Back to Actions"
}

const TERMINAL = new Set(["succeeded", "failed", "partial", "cancelled"])

// Strip ANSI CSI SGR escape sequences (colour codes) from Ansible output.
// Future runs are emitted uncoloured via ANSIBLE_NOCOLOR=1; this keeps legacy
// rows that were captured before that env var was set rendering cleanly too.
// eslint-disable-next-line no-control-regex
const ANSI_SGR = /\x1B\[[0-9;]*m/g
function stripAnsi(text: string): string {
  return text.replace(ANSI_SGR, "")
}

export function ActionRunDetail({ runId, backHref, backLabel }: ActionRunDetailProps) {
  const queryClient = useQueryClient()
  const [output, setOutput] = useState("")
  const [pinToBottom, setPinToBottom] = useState(true)
  const outputRef = useRef<HTMLPreElement>(null)
  const [cancelling, setCancelling] = useState(false)

  // Fetch initial run state
  const { data: run, isLoading } = useQuery<ActionRun>({
    queryKey: ["action-run", runId],
    queryFn: () => apiFetch<ActionRun>(`/api/actions/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) => {
      const data = query.state.data as ActionRun | undefined
      if (!data) return 2000
      return TERMINAL.has(data.status) ? false : 3000
    },
  })

  // Terminal runs: fetch stored output from DB (SSE doesn't replay)
  useEffect(() => {
    if (!run || !TERMINAL.has(run.status)) return
    if (output) return  // already have it — either from SSE or a previous fetch
    if (run.host_runs.length === 0) return

    let cancelled = false
    ;(async () => {
      const parts = await Promise.all(
        run.host_runs.map(async (hr) => {
          try {
            const res = await fetch(`${API_BASE}/api/actions/runs/${runId}/hosts/${hr.host_id}/output`, {
              credentials: "include",
            })
            if (!res.ok) return ""
            const text = await res.text()
            // Prefix per-host section only when there are multiple hosts (group run)
            if (run.host_runs.length > 1) {
              return `===== Host ${hr.host_id} (${hr.status}) =====\n${text}\n`
            }
            return text
          } catch {
            return ""
          }
        }),
      )
      if (!cancelled) setOutput(stripAnsi(parts.join("\n")))
    })()

    return () => {
      cancelled = true
    }
  }, [run, runId, output])

  // SSE subscription for live output
  useEffect(() => {
    if (!runId) return
    if (run && TERMINAL.has(run.status)) return  // already done, no need for SSE

    const es = new EventSource(`${API_BASE}/api/actions/runs/${runId}/stream`, {
      withCredentials: true,
    })

    es.addEventListener("output", (e) => {
      try {
        const data = JSON.parse(e.data) as { text?: string }
        if (data.text) {
          const clean = stripAnsi(data.text)
          setOutput((prev) => prev + clean)
        }
      } catch {}
    })

    es.addEventListener("status", (e) => {
      try {
        const data = JSON.parse(e.data) as { status?: string }
        if (data.status) {
          // Invalidate the run query so it refetches updated status
          queryClient.invalidateQueries({ queryKey: ["action-run", runId] })
        }
        if (data.status && TERMINAL.has(data.status)) {
          es.close()
        }
      } catch {}
    })

    es.onerror = () => {
      // SSE errors happen when the stream closes; just close
      es.close()
    }

    return () => es.close()
  // run?.status is intentional — we only want to re-subscribe when status changes,
  // not on every re-render of the full run object
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.status, queryClient])

  // Auto-scroll output
  useEffect(() => {
    if (pinToBottom && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [output, pinToBottom])

  async function handleCancel() {
    setCancelling(true)
    try {
      await apiFetch(`/api/actions/runs/${runId}/cancel`, { method: "POST" })
      queryClient.invalidateQueries({ queryKey: ["action-run", runId] })
      toast.success("Cancellation requested")
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to cancel")
    } finally {
      setCancelling(false)
    }
  }

  const isTerminal = run && TERMINAL.has(run.status)
  const isGroupRun = run?.group_id != null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link href={backHref} className="text-sm text-slate-400 hover:text-white">
            ← {backLabel}
          </Link>
          {run && <RunStatusBadge status={run.status} />}
        </div>
        {run && !isTerminal && (
          <Button
            variant="destructive"
            size="sm"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling…" : "Cancel run"}
          </Button>
        )}
      </div>

      {/* Run summary */}
      {run && (
        <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-4 space-y-1">
          <div className="text-sm text-slate-200 font-medium">{run.action_key}</div>
          <div className="text-xs text-slate-500">
            Version {run.action_version} ·{" "}
            {run.started_at
              ? `Started ${new Date(run.started_at).toLocaleString()}`
              : `Created ${new Date(run.created_at).toLocaleString()}`}
          </div>
          {run.error_message && (
            <div className="mt-2 text-xs text-red-400">{run.error_message}</div>
          )}
        </div>
      )}

      {/* Per-host status grid. Hidden for runs with a single host_run —
          host-scoped runs already show the host in the header, and
          cluster-mode runs (k8s-upgrade) anchor the run to a single
          driver host_run that doesn't represent per-node progress. */}
      {isGroupRun && run && run.host_runs.length >= 2 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-200 mb-3">Host Status</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
            {run.host_runs.map((hr: ActionHostRun) => (
              <div
                key={hr.id}
                className="flex items-center justify-between rounded border border-slate-700 bg-slate-800/50 px-3 py-2"
              >
                <span className="text-xs text-slate-400 truncate">Host {hr.host_id}</span>
                <RunStatusBadge status={hr.status} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Output pane */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-slate-200">Ansible Output</h3>
          <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
            <input
              type="checkbox"
              checked={pinToBottom}
              onChange={(e) => setPinToBottom(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            Pin to bottom
          </label>
        </div>
        <pre
          ref={outputRef}
          className="max-h-[60vh] overflow-y-auto rounded-lg bg-slate-950 p-4 text-xs font-mono text-slate-300 whitespace-pre-wrap"
        >
          {output || (isLoading ? "Loading…" : isTerminal ? "(no output captured)" : "Waiting for output…")}
        </pre>
      </div>
    </div>
  )
}
