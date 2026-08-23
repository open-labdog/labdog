"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { apiFetch, ApiError } from "@/lib/api"
import { toast } from "sonner"
import type { AlertEvent } from "@/lib/types"

const STATUS_STYLE: Record<string, string> = {
  firing: "bg-red-600 text-white",
  resolved: "bg-slate-600 text-slate-300",
}

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-red-600 text-white",
  warning: "bg-amber-600 text-white",
  info: "bg-blue-600 text-white",
}

/**
 * Why no investigation ran, in words.
 *
 * Every one of these is a decision LabDog made on the operator's behalf,
 * and each has a different fix — a setting to flip, a threshold to lower,
 * a budget to raise. Rendering them all as a bare "skipped" would make
 * the page useless for the question it exists to answer.
 */
const OUTCOME_LABEL: Record<string, string> = {
  started: "Investigating",
  skipped_disabled: "Auto-investigate off",
  skipped_severity: "Below severity threshold",
  skipped_resolved: "Already resolved",
  skipped_duplicate: "Already investigated",
  skipped_budget: "AI budget reached",
  failed: "Could not start",
}

/**
 * What became of an investigation that did start.
 *
 * `investigation_outcome` is written once, when the decision to start is
 * made, and never again — so on its own the badge reads "Investigating"
 * forever, whatever happened next. A session that finished an hour ago, one
 * still running, and one that failed all looked identical from this page.
 *
 * These supersede the "started" label once a session exists, which is the
 * only outcome that has a life after it is recorded.
 */
const INVESTIGATION_LABEL: Record<string, string> = {
  queued: "Investigation queued",
  running: "Investigating",
  waiting_approval: "Waiting for approval",
  succeeded: "Investigated",
  failed: "Investigation failed",
  cancelled: "Investigation stopped",
}

const INVESTIGATION_STYLE: Record<string, string> = {
  queued: "bg-slate-700 text-slate-200",
  running: "bg-blue-600 text-white",
  waiting_approval: "bg-amber-600 text-white",
  succeeded: "bg-emerald-700 text-emerald-50",
  failed: "bg-red-700 text-red-50",
  cancelled: "bg-slate-600 text-slate-200",
}

function relative(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export default function AlertsPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [showResolved, setShowResolved] = useState(false)

  const { data: alerts, isLoading } = useQuery<AlertEvent[]>({
    queryKey: ["alerts", showResolved],
    queryFn: () =>
      apiFetch<AlertEvent[]>(
        showResolved ? "/api/ai/alerts" : "/api/ai/alerts?status=firing",
      ),
    refetchInterval: 30_000,
  })

  const investigate = useMutation({
    mutationFn: (id: number) =>
      apiFetch<AlertEvent>(`/api/ai/alerts/${id}/investigate`, { method: "POST" }),
    onSuccess: (alert) => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] })
      if (alert.investigation_session_id) {
        router.push(`/assistant?session=${alert.investigation_session_id}`)
      }
    },
    onError: (err: unknown) => {
      // The API distinguishes "you cannot" (409, AI off or no provider)
      // from "you cannot right now" (402, budget). Both arrive here as
      // text the operator can act on, so pass it through rather than
      // flattening to "failed".
      const msg =
        err instanceof ApiError || err instanceof Error
          ? err.message
          : "Could not start an investigation"
      toast.error(msg)
    },
  })

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">Alerts</h1>
          <p className="mt-1 text-sm text-slate-400">
            Alerts received from Grafana and Alertmanager. Recording them is
            separate from investigating them — see{" "}
            <a
              className="underline hover:text-slate-200"
              href="https://open-labdog.github.io/labdog/ui/alerts/"
            >
              the guide
            </a>
            .
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => setShowResolved((v) => !v)}
        >
          {showResolved ? "Firing only" : "Show resolved"}
        </Button>
      </div>

      {isLoading && <p className="text-sm text-slate-400">Loading…</p>}

      {!isLoading && (alerts ?? []).length === 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-6 text-sm text-slate-400">
          <p className="font-medium text-slate-300">No alerts recorded.</p>
          <p className="mt-2">
            LabDog accepts alerts once <code>ai.alert_intake_enabled</code> is on
            and a Grafana contact point points at{" "}
            <code>/api/webhooks/grafana-alerts</code> with the configured token.
            Nothing here means nothing has arrived — not that nothing is wrong.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {(alerts ?? []).map((alert) => (
          <div
            key={alert.id}
            className="rounded-lg border border-slate-800 bg-slate-900/40 p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={STATUS_STYLE[alert.status] ?? STATUS_STYLE.resolved}>
                {alert.status}
              </Badge>
              {alert.severity && (
                <Badge
                  className={
                    SEVERITY_STYLE[alert.severity.toLowerCase()] ??
                    "bg-slate-700 text-slate-200"
                  }
                >
                  {alert.severity}
                </Badge>
              )}
              <span className="font-medium text-white">{alert.alertname}</span>
              {alert.dedup_count > 1 && (
                <span
                  className="text-xs text-slate-400"
                  title="How many times LabDog has been told about this same firing"
                >
                  ×{alert.dedup_count}
                </span>
              )}
              <span className="ml-auto text-xs text-slate-500">
                {relative(alert.created_at)} · {alert.source.replace("_", " ")}
              </span>
            </div>

            {typeof alert.annotations?.summary === "string" && (
              <p className="mt-2 text-sm text-slate-300">
                {alert.annotations.summary as string}
              </p>
            )}

            {/*
              What the assistant concluded, on the row.
              The alert's own summary says what fired; this says what came
              of it, which is the question an operator scanning this page is
              actually asking. Set apart with a rule and a quieter colour so
              the two are not read as one sentence — one is the monitoring
              system talking, the other is LabDog.
            */}
            {alert.investigation_summary && (
              <p className="mt-2 border-l-2 border-slate-700 pl-3 text-sm text-slate-400">
                {alert.investigation_summary}
              </p>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              {alert.investigation_status ? (
                <Badge
                  className={
                    INVESTIGATION_STYLE[alert.investigation_status] ??
                    "bg-slate-700 text-slate-200"
                  }
                >
                  {INVESTIGATION_LABEL[alert.investigation_status] ??
                    alert.investigation_status}
                </Badge>
              ) : (
                alert.investigation_outcome && (
                  <Badge className="bg-slate-700 text-slate-200">
                    {OUTCOME_LABEL[alert.investigation_outcome] ??
                      alert.investigation_outcome}
                  </Badge>
                )
              )}
              {alert.investigation_detail && (
                <span className="text-slate-400">{alert.investigation_detail}</span>
              )}

              <span className="ml-auto flex gap-2">
                {alert.investigation_session_id ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      router.push(
                        `/assistant?session=${alert.investigation_session_id}`,
                      )
                    }
                  >
                    View investigation
                  </Button>
                ) : (
                  alert.status === "firing" && (
                    <Button
                      disabled={investigate.isPending}
                      onClick={() => investigate.mutate(alert.id)}
                    >
                      Investigate
                    </Button>
                  )
                )}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
