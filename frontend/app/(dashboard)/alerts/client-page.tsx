"use client"

import { useCallback, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { apiFetch, ApiError } from "@/lib/api"
import { toast } from "sonner"
import { shortAgo } from "@/lib/fleet"
import { def, ALERT_SEVERITY, ALERT_STATUS, AI_AUTONOMY, AI_ROLLBACK, AI_SESSION_STATUS, REMEDIATION_OUTCOME } from "@/lib/status"
import { PageHead, RunStatus, Seg, Table, Tabs, Tag, type Tone } from "@/components/ld"
import { AlertHandling } from "@/components/alerts/alert-handling"
import type { AlertDispatch, AlertEvent, AlertIntakeStatus } from "@/lib/types"

/** Why no investigation ran, in words. Every one of these is a decision
 *  LabDog made on the operator's behalf, and each has a different fix —
 *  a setting to flip, a label to add, a budget to raise. */
const OUTCOME_LABEL: Record<string, string> = {
  started: "investigating",
  waiting_on_action: "waiting on action",
  handled_by_action: "handled by action",
  skipped_disabled: "auto-investigate off",
  skipped_no_label: "not labelled for the AI",
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

/** What the investigation was allowed to change. Shown when that is more
 *  than looking, or when the alert is marked for full auto and a
 *  safeguard held it back — a plain read-only row says nothing, because
 *  that is every row until remediation is switched on. The note (why this
 *  level) is on hover, like the outcome detail beside it. */
function AutonomyTag({ alert }: { alert: AlertEvent }) {
  const level = alert.investigation_autonomy
  if (!level || (level === "read_only" && !alert.investigation_autonomy_note)) return null
  const { label, tone } = def(AI_AUTONOMY, level)
  return <Tag tone={tone} title={alert.investigation_autonomy_note ?? undefined}>{label}</Tag>
}

/** What LabDog found when it checked a full-auto fix afterwards, and any
 *  rollback that followed. The evidence — which alert fired, what the SSH
 *  probe said, what Proxmox answered — is on hover. */
function RemediationTags({ alert }: { alert: AlertEvent }) {
  if (!alert.remediation_outcome) return null
  const outcome = def(REMEDIATION_OUTCOME, alert.remediation_outcome)
  const rollback = alert.rollback_status ? def(AI_ROLLBACK, alert.rollback_status) : null
  return (
    <>
      <Tag tone={outcome.tone} title={alert.remediation_detail ?? undefined}>{outcome.label}</Tag>
      {rollback && <Tag tone={rollback.tone} title={alert.rollback_detail ?? undefined}>{rollback.label}</Tag>}
    </>
  )
}

/** Why the action the alert's labdog_action label named did not run. */
const ACTION_SKIP: Record<string, string> = {
  not_allowed: "not allowed",
  unknown_action: "unknown action",
  skipped_no_host: "no host",
  skipped_token: "token too short",
  skipped_in_flight: "already running",
  skipped_cooldown: "cooldown",
  failed: "could not run",
}

/** What LabDog found when it judged the run afterwards. */
const ACTION_CHECK: Record<string, { label: string; tone: Tone }> = {
  fixed: { label: "fixed", tone: "ok" },
  not_effective: { label: "did not fix it", tone: "warn" },
  run_failed: { label: "run failed", tone: "danger" },
}

/** What the alert's labdog_action label came to: the run it started and
 *  how that went, or why it did not run. */
function ActionTags({ actions }: { actions: AlertDispatch[] }) {
  if (actions.length === 0) return <span className="text-text-faint">—</span>
  return (
    <span className="flex min-w-0 flex-col gap-0.5" data-testid="alert-actions">
      {actions.map((x) => {
        const name = x.action_name ?? x.action_key
        if (x.outcome !== "started") {
          return (
            <span key={x.id} className="flex items-center gap-1">
              <span className="trunc text-[11px] text-text-2">{name}</span>
              <Tag tone={x.outcome === "not_allowed" || x.outcome === "unknown_action" ? "warn" : undefined} title={x.detail ?? undefined}>{ACTION_SKIP[x.outcome] ?? x.outcome}</Tag>
            </span>
          )
        }
        const check = x.check_outcome ? ACTION_CHECK[x.check_outcome] : null
        return (
          <span key={x.id} className="flex items-center gap-1">
            <Link href={`/actions/runs/${x.action_run_id}`} className="trunc text-[11px] text-ld-accent hover:no-underline">
              {name} #{x.action_run_id}
            </Link>
            {check ? <Tag tone={check.tone}>{check.label}</Tag> : x.run_status ? <RunStatus s={x.run_status} /> : null}
          </span>
        )
      })}
    </span>
  )
}

type Tab = "received" | "handling"

/**
 * Alerts — Received · Handling. What arrived and what each alert set off;
 * and what alerts are allowed to do, which their Grafana labels ask for.
 */
export default function AlertsPage() {
  const router = useRouter()
  const search = useSearchParams()
  const tab = (search.get("tab") as Tab | null) ?? "received"
  const setTab = (t: string) => router.push(t === "received" ? "/alerts" : `/alerts?tab=${t}`)
  const { data: intake } = useQuery<AlertIntakeStatus>({ queryKey: ["alert-intake"], queryFn: () => apiFetch<AlertIntakeStatus>("/api/alerts/intake"), retry: false })

  return (
    <>
      <PageHead
        crumbs={[{ label: "operations" }]}
        title="Alerts"
        sub={<>Received from Grafana and Alertmanager. The alert rule&apos;s labels say what each one sets off — an action, the AI, or nothing; see <a className="underline hover:text-text-2" href="https://open-labdog.github.io/labdog/ui/alerts/">the guide</a>.</>}
      >
        <Tabs
          tabs={[
            { k: "received", label: "Received" },
            { k: "handling", label: "Handling" },
          ]}
          value={tab}
          onChange={setTab}
          counts={{ handling: intake?.allowed_actions || undefined }}
        />
      </PageHead>
      {tab === "handling" ? <AlertHandling /> : <Received focus={Number(search.get("alert")) || null} />}
    </>
  )
}

/** The alerts that arrived, newest first. ``focus`` is the alert a run
 *  page linked to: it is highlighted and scrolled to, resolved or not. */
function Received({ focus }: { focus: number | null }) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [showResolved, setShowResolved] = useState(focus !== null)
  const scrolled = useRef(false)
  const scrollTo = useCallback((el: HTMLElement | null) => {
    if (el && !scrolled.current) {
      scrolled.current = true
      el.scrollIntoView({ block: "center" })
    }
  }, [])

  const { data: alerts, isLoading } = useQuery<AlertEvent[]>({
    queryKey: ["alerts", showResolved],
    queryFn: () => apiFetch<AlertEvent[]>(showResolved ? "/api/alerts" : "/api/alerts?status=firing"),
    refetchInterval: 30_000,
  })

  const investigate = useMutation({
    mutationFn: (id: number) => apiFetch<AlertEvent>(`/api/alerts/${id}/investigate`, { method: "POST" }),
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
      <div className="flex shrink-0 items-center gap-[7px] border-b border-line bg-surface px-3.5 py-2">
        <Seg value={showResolved ? "all" : "firing"} onChange={(k) => setShowResolved(k === "all")} options={[{ k: "firing", label: "Firing" }, { k: "all", label: "All" }]} />
      </div>

      <Table<AlertEvent>
        cols={[
          { k: "when", label: "when", w: "84px", sortable: false, cell: (a) => <span className="mono num text-[11px] text-text-3">{shortAgo(a.created_at)} ago</span> },
          { k: "status", label: "status", w: "84px", sortable: false, cell: (a) => <Tag tone={def(ALERT_STATUS, a.status).tone}>{a.status}</Tag> },
          { k: "severity", label: "severity", w: "84px", sortable: false, cell: (a) => (a.severity ? <Tag tone={def(ALERT_SEVERITY, a.severity.toLowerCase()).tone}>{a.severity}</Tag> : <span className="text-text-faint">—</span>) },
          {
            k: "alert", label: "alert", w: "minmax(140px,1.2fr)", sortable: false,
            cell: (a) => (
              <span className="flex items-center gap-1.5" ref={a.id === focus ? scrollTo : undefined}>
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
          { k: "action", label: "action", w: "minmax(140px,1fr)", sortable: false, cell: (a) => <ActionTags actions={a.actions ?? []} /> },
          {
            k: "investigation", label: "investigation", w: "minmax(150px,1fr)", sortable: false,
            cell: (a) => (
              <span className="flex flex-wrap items-center gap-1">
                {a.investigation_status ? (
                  <Tag tone={def(AI_SESSION_STATUS, a.investigation_status).tone} title={a.investigation_detail ?? undefined}>{INVESTIGATION_LABEL[a.investigation_status] ?? a.investigation_status}</Tag>
                ) : a.investigation_outcome ? (
                  <Tag title={a.investigation_detail ?? undefined}>{OUTCOME_LABEL[a.investigation_outcome] ?? a.investigation_outcome}</Tag>
                ) : <span className="text-text-faint">—</span>}
                <AutonomyTag alert={a} />
                <RemediationTags alert={a} />
              </span>
            ),
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
        activeKey={focus ?? undefined}
        rowTone={(a) => (a.status === "firing" && a.severity?.toLowerCase() === "critical" ? "danger" : undefined)}
        loading={isLoading}
        empty={
          <span>
            No alerts recorded. LabDog accepts alerts once <span className="mono">alerts.intake_enabled</span> is on and a Grafana contact point points at <span className="mono">/api/webhooks/grafana-alerts</span> with the configured token. Nothing here means nothing has arrived — not that nothing is wrong.
          </span>
        }
      />
    </>
  )
}
