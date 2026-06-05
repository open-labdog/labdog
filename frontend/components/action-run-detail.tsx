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
  // Per-host output, keyed by host_id, for the click-to-filter view.
  // `undefined` entry = not fetched yet (shows a loading hint).
  const [hostOutputs, setHostOutputs] = useState<Record<number, string>>({})
  // Which host's log to show. null = combined "All hosts" view.
  const [selectedHostId, setSelectedHostId] = useState<number | null>(null)
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
      const entries = await Promise.all(
        run.host_runs.map(async (hr) => {
          try {
            const res = await fetch(`${API_BASE}/api/actions/runs/${runId}/hosts/${hr.host_id}/output`, {
              credentials: "include",
            })
            return { hr, text: res.ok ? stripAnsi(await res.text()) : "" }
          } catch {
            return { hr, text: "" }
          }
        }),
      )
      if (cancelled) return
      // Cache each host's log so clicking a host card switches instantly.
      const map: Record<number, string> = {}
      for (const { hr, text } of entries) map[hr.host_id] = text
      setHostOutputs(map)
      // Combined "All hosts" view: prefix per-host sections only for group runs.
      const combined =
        run.host_runs.length > 1
          ? entries
              .map(
                ({ hr, text }) =>
                  `===== ${hr.hostname ?? `Host ${hr.host_id}`} (${hr.status}) =====\n${text}\n`,
              )
              .join("\n")
          : entries.map((e) => e.text).join("\n")
      setOutput(combined)
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

  // Auto-scroll output (also when switching the selected host)
  useEffect(() => {
    if (pinToBottom && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [output, hostOutputs, selectedHostId, pinToBottom])

  // Toggle the per-host log filter. Clicking the active host clears back to
  // the combined view. Fetches the host's log on demand if it isn't cached
  // yet (e.g. a still-running run, where the terminal-fetch effect hasn't
  // populated the map).
  async function selectHost(hostId: number) {
    if (selectedHostId === hostId) {
      setSelectedHostId(null)
      return
    }
    setSelectedHostId(hostId)
    if (hostOutputs[hostId] !== undefined) return
    try {
      const res = await fetch(`${API_BASE}/api/actions/runs/${runId}/hosts/${hostId}/output`, {
        credentials: "include",
      })
      const text = res.ok ? stripAnsi(await res.text()) : ""
      setHostOutputs((prev) => ({ ...prev, [hostId]: text }))
    } catch {
      setHostOutputs((prev) => ({ ...prev, [hostId]: "" }))
    }
  }

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

  // Resolve the currently-selected host + what the output pane should show.
  const selectedHost =
    selectedHostId !== null ? run?.host_runs.find((hr) => hr.host_id === selectedHostId) ?? null : null
  const selectedLabel = selectedHost ? selectedHost.hostname ?? `Host ${selectedHost.host_id}` : null
  const paneText = selectedHostId !== null ? hostOutputs[selectedHostId] ?? "" : output
  const paneFallback =
    selectedHostId !== null
      ? hostOutputs[selectedHostId] === undefined
        ? "Loading…"
        : "(no output captured for this host)"
      : isLoading
        ? "Loading…"
        : isTerminal
          ? "(no output captured)"
          : "Waiting for output…"

  // Derive the back link from the fetched run so it is always correct
  // regardless of which URL the user navigated from.
  const effectiveBackHref = run?.host_id
    ? `/hosts/${run.host_id}?tab=actions`
    : run?.group_id
      ? `/groups/${run.group_id}?tab=actions`
      : backHref

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link href={effectiveBackHref} className="text-sm text-slate-400 hover:text-white">
            ← {backLabel}
          </Link>
          {run && <RunStatusBadge status={run.status} reason={run.pending_reason} />}
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

      {/* Pending-reason callout: when the run is deferred by another op
          on the target host, surface the blocker as an amber banner so
          the operator doesn't have to hover the badge to see what's
          ahead of them in the queue. */}
      {run && run.status === "pending" && run.pending_reason && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-600/40 bg-amber-600/10 px-4 py-3 text-sm text-amber-200">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="mt-0.5 h-4 w-4 shrink-0"
            aria-hidden="true"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zm0-12a1 1 0 011 1v4a1 1 0 11-2 0V7a1 1 0 011-1zm0 9a1 1 0 100-2 1 1 0 000 2z"
              clipRule="evenodd"
            />
          </svg>
          <span>Waiting: {run.pending_reason}</span>
        </div>
      )}

      {/* Per-host status grid for group runs. Host-scoped runs already
          show the host in the header, so the grid is hidden there. Each
          card is clickable to filter the output pane to that host. */}
      {isGroupRun && run && run.host_runs.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-200">Host Status</h3>
            {selectedHostId !== null && (
              <button
                type="button"
                onClick={() => setSelectedHostId(null)}
                className="text-xs text-slate-400 hover:text-white"
              >
                Show all hosts
              </button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
            {run.host_runs.map((hr: ActionHostRun) => {
              const selected = selectedHostId === hr.host_id
              return (
                <button
                  key={hr.id}
                  type="button"
                  onClick={() => selectHost(hr.host_id)}
                  aria-pressed={selected}
                  title={`Show ${hr.hostname ?? `Host ${hr.host_id}`} log`}
                  className={`flex items-center justify-between rounded border px-3 py-2 text-left transition-colors ${
                    selected
                      ? "border-sky-500 bg-sky-500/10"
                      : "border-slate-700 bg-slate-800/50 hover:border-slate-500"
                  }`}
                >
                  <span className="text-xs text-slate-400 truncate">
                    {hr.hostname ?? `Host ${hr.host_id}`}
                  </span>
                  <RunStatusBadge status={hr.status} reason={hr.pending_reason} />
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Output pane */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-slate-200">
            Ansible Output{selectedLabel ? ` — ${selectedLabel}` : ""}
          </h3>
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
          {paneText || paneFallback}
        </pre>
      </div>
    </div>
  )
}
