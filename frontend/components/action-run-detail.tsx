"use client"

import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { API_BASE, apiFetch } from "@/lib/api"
import { toast } from "sonner"
import { Banner, CodeBlock, PageHead, RunStatus, Table } from "@/components/ld"
import type { ActionHostRun, ActionRun } from "@/lib/types"

const TERMINAL = new Set(["succeeded", "failed", "partial", "cancelled"])

// Strip terminal control sequences from Ansible output.
//
// Future runs are emitted uncoloured via ANSIBLE_NOCOLOR=1; this keeps legacy
// rows that were captured before that env var was set rendering cleanly too.
//
// This used to match SGR (colour) sequences only, so cursor movement, OSC
// title/hyperlink sequences and bare carriage returns survived into the
// <pre>. React escapes them, so it was never an XSS risk — but the rendered
// log was wrong, and copying it into a terminal re-injected the control
// codes. The three patterns below cover what a playbook actually emits:
//
//   CSI  — ESC [ ... final byte: every colour code, cursor move, erase and
//          scroll. A superset of the old SGR-only pattern.
//   OSC  — ESC ] ... terminated by BEL or ST, which is how a window title
//          or a hyperlink is set.
//   CR   — a bare carriage return, used to overwrite a progress line in
//          place. With no terminal to act on it, it renders as a stray break.
const ANSI_CSI = /\x1B\[[0-?]*[ -/]*[@-~]/g
const ANSI_OSC = /\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)/g
const BARE_CR = /\r(?!\n)/g
function stripAnsi(text: string): string {
  return text.replace(ANSI_OSC, "").replace(ANSI_CSI, "").replace(BARE_CR, "")
}

// `hostname` is the live host's name when it still exists and the
// dispatch-time snapshot when it does not, so it is set on every row;
// the fallback is for rows written before that column existed.
function hostLabel(hr: ActionHostRun): string {
  return hr.hostname ?? (hr.host_id !== null ? `Host ${hr.host_id}` : "Deleted host")
}

export function ActionRunDetail({ runId }: { runId: number }) {
  const queryClient = useQueryClient()
  const [output, setOutput] = useState("")
  // Per-host output, keyed by ActionHostRun.id, for the click-to-filter
  // view. `undefined` entry = not fetched yet (shows a loading hint).
  // Keyed by the row rather than by host_id because the transcript
  // outlives the host: a deleted host leaves host_id null, and the row
  // id is the only identifier still guaranteed to resolve.
  const [hostOutputs, setHostOutputs] = useState<Record<number, string>>({})
  // Which host's log to show, by ActionHostRun.id. null = combined view.
  const [selectedHostRunId, setSelectedHostRunId] = useState<number | null>(null)
  const [pinToBottom, setPinToBottom] = useState(true)
  const outputRef = useRef<HTMLPreElement>(null)
  // Tracks the runId we've already loaded persisted output for, so the
  // terminal fetch runs exactly once per run (see the effect below).
  const terminalFetchedForRef = useRef<number | null>(null)
  const [cancelling, setCancelling] = useState(false)

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

  // Once a run reaches a terminal state, load the authoritative, complete
  // output from the DB. Do this even when SSE already streamed partial
  // output (e.g. the pre-run step-log): the live stream only carries what
  // was published while this tab was connected and is never replayed, so
  // the persisted per-host output is the source of truth. Gate on a ref
  // keyed by runId (NOT on `output` being empty) so a live-watched run
  // still loads its full log on completion. Fetches exactly once per run.
  useEffect(() => {
    if (!run || !TERMINAL.has(run.status)) return
    if (terminalFetchedForRef.current === runId) return
    if (run.host_runs.length === 0) return
    terminalFetchedForRef.current = runId

    ;(async () => {
      const entries = await Promise.all(
        run.host_runs.map(async (hr) => {
          try {
            const res = await fetch(`${API_BASE}/api/actions/runs/${runId}/host-runs/${hr.id}/output`, { credentials: "include" })
            return { hr, text: res.ok ? stripAnsi(await res.text()) : "" }
          } catch {
            return { hr, text: "" }
          }
        }),
      )
      if (terminalFetchedForRef.current !== runId) return
      const map: Record<number, string> = {}
      for (const { hr, text } of entries) map[hr.id] = text
      setHostOutputs(map)
      const combined = run.host_runs.length > 1
        ? entries.map(({ hr, text }) => `===== ${hostLabel(hr)} (${hr.status}) =====\n${text}\n`).join("\n")
        : entries.map((e) => e.text).join("\n")
      setOutput(combined)
    })()
  }, [run, runId])

  // SSE subscription for live output
  useEffect(() => {
    if (!runId) return
    if (run && TERMINAL.has(run.status)) return

    const es = new EventSource(`${API_BASE}/api/actions/runs/${runId}/stream`, { withCredentials: true })

    es.addEventListener("output", (e) => {
      try {
        const data = JSON.parse(e.data) as { text?: string }
        if (data.text) setOutput((prev) => prev + stripAnsi(data.text!))
      } catch {}
    })
    es.addEventListener("status", (e) => {
      try {
        const data = JSON.parse(e.data) as { status?: string }
        if (data.status) queryClient.invalidateQueries({ queryKey: ["action-run", runId] })
        if (data.status && TERMINAL.has(data.status)) es.close()
      } catch {}
    })
    es.onerror = () => es.close()

    return () => es.close()
  // run?.status is intentional — we only want to re-subscribe when status
  // changes, not on every re-render of the full run object.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.status, queryClient])

  useEffect(() => {
    if (pinToBottom && outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output, hostOutputs, selectedHostRunId, pinToBottom])

  // Toggle the per-host log filter. Clicking the active host clears back
  // to the combined view. Fetches the host's log on demand if it isn't
  // cached yet (e.g. a still-running run).
  async function selectHostRun(hostRunId: number) {
    if (selectedHostRunId === hostRunId) {
      setSelectedHostRunId(null)
      return
    }
    setSelectedHostRunId(hostRunId)
    if (hostOutputs[hostRunId] !== undefined) return
    try {
      const res = await fetch(`${API_BASE}/api/actions/runs/${runId}/host-runs/${hostRunId}/output`, { credentials: "include" })
      const text = res.ok ? stripAnsi(await res.text()) : ""
      setHostOutputs((prev) => ({ ...prev, [hostRunId]: text }))
    } catch {
      setHostOutputs((prev) => ({ ...prev, [hostRunId]: "" }))
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
  const isMultiHost = (run?.host_runs.length ?? 0) > 1
  const selectedHost = selectedHostRunId !== null ? run?.host_runs.find((hr) => hr.id === selectedHostRunId) ?? null : null
  const selectedLabel = selectedHost ? hostLabel(selectedHost) : null
  const paneText = selectedHostRunId !== null ? hostOutputs[selectedHostRunId] ?? "" : output
  const paneFallback = selectedHostRunId !== null
    ? hostOutputs[selectedHostRunId] === undefined ? "Loading…" : "(no output captured for this host)"
    : isLoading ? "Loading…" : isTerminal ? "(no output captured)" : "Waiting for output…"

  // A fleet run legitimately has neither id; only host/group targets go
  // null because the row was removed.
  const targetDeleted = !!run && run.target_kind !== "fleet" && run.host_id === null && run.group_id === null
  const cleanTargetLabel = (run?.target_label ?? "").replace(/^group:\s*/i, "")

  const crumbs = run?.host_id
    ? [{ label: "fleet", href: "/hosts" }, { label: "hosts", href: "/hosts" }, { label: cleanTargetLabel, href: `/hosts/${run.host_id}?tab=activity` }]
    : run?.group_id
      ? [{ label: "fleet", href: "/hosts" }, { label: "groups", href: "/groups" }, { label: cleanTargetLabel, href: `/groups/${run.group_id}?tab=activity` }]
      : [{ label: "operations", href: "/plans" }, { label: "runs", href: "/runs" }]

  return (
    <>
      <PageHead
        crumbs={crumbs}
        title={<><span className="mono">{run?.action_key ?? (isLoading ? "Loading…" : `run #${runId}`)}</span> {run && <RunStatus s={run.status} reason={run.pending_reason} />}</>}
        sub={run && (
          <>
            v{run.action_version} · {run.started_at ? `started ${new Date(run.started_at).toLocaleString()}` : `created ${new Date(run.created_at).toLocaleString()}`}
            {targetDeleted && <> · {run.target_label} (deleted)</>}
          </>
        )}
        actions={run && !isTerminal && (
          <button type="button" className="btn btn-sm btn-danger" disabled={cancelling} onClick={handleCancel}>
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        )}
      />

      <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
        {run?.error_message && <Banner tone="danger">{run.error_message}</Banner>}
        {run?.status === "pending" && run.pending_reason && <Banner tone="warn">Waiting: {run.pending_reason}</Banner>}

        {isMultiHost && run && (
          <Table<ActionHostRun>
            cols={[
              { k: "host", label: "host", w: "minmax(140px,1fr)", sortable: false, cell: (hr) => <span className="mono trunc">{hostLabel(hr)}{hr.host_id === null && <span className="text-text-faint"> (deleted)</span>}</span> },
              { k: "status", label: "status", w: "120px", right: true, sortable: false, cell: (hr) => <RunStatus s={hr.status} reason={hr.pending_reason} /> },
            ]}
            rows={run.host_runs}
            keyOf={(hr) => hr.id}
            onRowClick={(hr) => selectHostRun(hr.id)}
            activeKey={selectedHostRunId ?? undefined}
            footer={selectedHostRunId !== null && <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelectedHostRunId(null)}>show all hosts</button>}
          />
        )}

        <CodeBlock
          title={selectedLabel ? `ansible output — ${selectedLabel}` : "ansible output"}
          actions={
            <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-text-3">
              <input type="checkbox" checked={pinToBottom} onChange={(e) => setPinToBottom(e.target.checked)} /> pin to bottom
            </label>
          }
          maxH="60vh"
          preRef={outputRef}
        >
          {paneText || paneFallback}
        </CodeBlock>
      </div>
    </>
  )
}
