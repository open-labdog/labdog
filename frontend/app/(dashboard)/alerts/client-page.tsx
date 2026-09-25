"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { apiFetch, ApiError } from "@/lib/api"
import { toast } from "sonner"
import { shortAgo } from "@/lib/fleet"
import { def, ALERT_SEVERITY, ALERT_STATUS, AI_SESSION_STATUS } from "@/lib/status"
import { PageHead, Seg, Table, Tag } from "@/components/ld"
import type { AlertEvent } from "@/lib/types"

/** Why no investigation ran, in words. Every one of these is a decision
 *  LabDog made on the operator's behalf, and each has a different fix —
 *  a setting to flip, a threshold to lower, a budget to raise. */
const OUTCOME_LABEL: Record<string, string> = {
  started: "investigating",
  skipped_disabled: "auto-investigate off",
  skipped_severity: "below severity threshold",
  skipped_resolved: "already resolved",
  skipped_duplicate: "already investigated",
  skipped_budget: "AI budget reached",
  failed: "could not start",
}

/** What became of an investigation that did start. `investigation_outcome`
 *  is written once and never again, so these supersede "started" once a
 *  session exists — the only outcome with a life after it is recorded. */
const INVESTIGATION_LABEL: Record<string, string> = {
  queued: "investigation queued",
  running: "investigating",
  waiting_approval: "waiting for approval",
  succeeded: "investigated",
  failed: "investigation failed",
  cancelled: "investigation stopped",
}

export default function AlertsPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [showResolved, setShowResolved] = useState(false)

  const { data: alerts, isLoading } = useQuery<AlertEvent[]>({
    queryKey: ["alerts", showResolved],
    queryFn: () => apiFetch<AlertEvent[]>(showResolved ? "/api/ai/alerts" : "/api/ai/alerts?status=firing"),
    refetchInterval: 30_000,
  })

  const investigate = useMutation({
    mutationFn: (id: number) => apiFetch<AlertEvent>(`/api/ai/alerts/${id}/investigate`, { method: "POST" }),
    onSuccess: (alert) => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] })
      if (alert.investigation_session_id) router.push(`/assistant?session=${alert.investigation_session_id}`)
    },
    onError: (err: unknown) => {
      // The API distinguishes "you cannot" (409, AI off or no provider) from
      // "you cannot right now" (402, budget). Both arrive here as text the
      // operator can act on, so pass it through rather than flattening to
      // "failed".
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "Could not start an investigation")
    },
  })

  const rows = alerts ?? []

  return (
    <>
      <PageHead
        crumbs={[{ label: "assistant", href: "/assistant" }]}
        title="Alerts"
        sub={<>Received from Grafana and Alertmanager. Recording is separate from investigating — see <a className="underline hover:text-text-2" href="https://open-labdog.github.io/labdog/ui/alerts/">the guide</a>.</>}
      >
        <Seg value={showResolved ? "all" : "firing"} onChange={(k) => setShowResolved(k === "all")} options={[{ k: "firing", label: "Firing" }, { k: "all", label: "All" }]} />
      </PageHead>

      <Table<AlertEvent>
        cols={[
          { k: "when", label: "when", w: "84px", sortable: false, cell: (a) => <span className="mono num text-[11px] text-text-3">{shortAgo(a.created_at)} ago</span> },
          { k: "status", label: "status", w: "84px", sortable: false, cell: (a) => <Tag tone={def(ALERT_STATUS, a.status).tone}>{a.status}</Tag> },
          { k: "severity", label: "severity", w: "84px", sortable: false, cell: (a) => (a.severity ? <Tag tone={def(ALERT_SEVERITY, a.severity.toLowerCase()).tone}>{a.severity}</Tag> : <span className="text-text-faint">—</span>) },
          {
            k: "alert", label: "alert", w: "minmax(140px,1.2fr)", sortable: false,
            cell: (a) => (
              <span className="flex items-center gap-1.5">
                <span className="mono font-medium text-text">{a.alertname}</span>
                {a.dedup_count > 1 && <span className="text-[10.5px] text-text-faint" title="How many times LabDog has been told about this same firing">×{a.dedup_count}</span>}
              </span>
            ),
          },
          {
            k: "summary", label: "summary", w: "minmax(160px,1.6fr)", sortable: false,
            cell: (a) => {
              const summary = typeof a.annotations?.summary === "string" ? a.annotations.summary : null
              return (
                <span className="flex min-w-0 flex-col gap-0.5">
                  {summary && <span className="trunc text-text-2">{summary}</span>}
                  {a.investigation_summary && <span className="trunc text-[10.5px] text-text-faint">{a.investigation_summary}</span>}
                </span>
              )
            },
          },
          {
            k: "investigation", label: "investigation", w: "minmax(120px,1fr)", sortable: false,
            cell: (a) => a.investigation_status ? (
              <Tag tone={def(AI_SESSION_STATUS, a.investigation_status).tone} title={a.investigation_detail ?? undefined}>{INVESTIGATION_LABEL[a.investigation_status] ?? a.investigation_status}</Tag>
            ) : a.investigation_outcome ? (
              <Tag title={a.investigation_detail ?? undefined}>{OUTCOME_LABEL[a.investigation_outcome] ?? a.investigation_outcome}</Tag>
            ) : <span className="text-text-faint">—</span>,
          },
          {
            k: "go", label: "", w: "110px", right: true, sortable: false,
            cell: (a) => a.investigation_session_id ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={(e) => { e.stopPropagation(); router.push(`/assistant?session=${a.investigation_session_id}`) }}>view →</button>
            ) : a.status === "firing" ? (
              <button type="button" className="btn btn-sm btn-ghost" disabled={investigate.isPending} onClick={(e) => { e.stopPropagation(); investigate.mutate(a.id) }}>investigate</button>
            ) : null,
          },
        ]}
        rows={rows}
        keyOf={(a) => a.id}
        rowTone={(a) => (a.status === "firing" && a.severity?.toLowerCase() === "critical" ? "danger" : undefined)}
        loading={isLoading}
        empty={
          <span>
            No alerts recorded. LabDog accepts alerts once <span className="mono">ai.alert_intake_enabled</span> is on and a Grafana contact point points at <span className="mono">/api/webhooks/grafana-alerts</span> with the configured token. Nothing here means nothing has arrived — not that nothing is wrong.
          </span>
        }
      />
    </>
  )
}
