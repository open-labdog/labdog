"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { shortAgo } from "@/lib/fleet"
import { CodeBlock, Confirm, Copy, Panel, RunStatus, Table, Tag } from "@/components/ld"
import { AlertActionDialog } from "@/components/alerts/alert-action-dialog"
import type { AlertActionEntry, AlertIntakeStatus } from "@/lib/types"

const SNIPPET = `labdog_action      = restart-nginx
labdog_ai          = true
labdog_ai_automate = {{ if match "^(web-1|web-2)$" $labels.labdog_hostname }}true{{ else }}false{{ end }}`

/** The actions an alert's labdog_action label may start. Starts empty. */
function ActionsPanel() {
  const [editing, setEditing] = useState<AlertActionEntry | "new" | null>(null)
  const [removing, setRemoving] = useState<AlertActionEntry | null>(null)
  const { data: entries, isLoading } = useQuery<AlertActionEntry[]>({ queryKey: ["alert-actions"], queryFn: () => apiFetch<AlertActionEntry[]>("/api/alerts/actions") })
  const remove = useApiMutation<unknown, number>({
    mutationFn: (id) => apiFetch(`/api/alerts/actions/${id}`, { method: "DELETE" }),
    invalidateKeys: [["alert-actions"], ["alert-intake"], ["host-alerts"]],
    successMessage: "Taken off the list",
    onSuccess: () => setRemoving(null),
  })
  const rows = entries ?? []

  return (
    <Panel
      title="actions alerts may run"
      meta={rows.length ? `${rows.filter((r) => r.enabled).length} of ${rows.length} enabled` : "none yet"}
      actions={<button type="button" className="btn btn-sm btn-primary" onClick={() => setEditing("new")} data-testid="alert-action-new">Allow an action…</button>}
    >
      {/* A minimum width with its own sideways scroll, so a phone scrolls the
          table rather than squeezing seven columns into nothing. */}
      <div className="overflow-x-auto">
        <div className="flex min-w-[860px] flex-col">
          <Table<AlertActionEntry>
            cols={[
              {
                k: "action", label: "label value", w: "minmax(150px,1.2fr)", sortable: false,
                cell: (e) => (
                  <span className="flex min-w-0 flex-col">
                    <span className="mono text-text">{e.action_key}</span>
                    <span className="trunc text-[10.5px] text-text-3">{e.action_name ?? "no loaded pack has this action"}</span>
                  </span>
                ),
              },
              { k: "where", label: "where", w: "minmax(110px,1fr)", sortable: false, cell: (e) => <span className="mono text-[11px]">{e.target_kind === "fleet" ? "all hosts" : `${e.target_kind}: ${e.target_name ?? e.target_id}`}</span> },
              {
                k: "params", label: "parameters", w: "minmax(120px,1fr)", sortable: false,
                cell: (e) => {
                  const p = Object.entries(e.parameters)
                  return p.length ? <span className="mono trunc text-[11px]" title={p.map(([k, v]) => `${k}=${String(v)}`).join("\n")}>{p.map(([k, v]) => `${k}=${String(v)}`).join(" ")}</span> : <span className="text-text-faint">defaults</span>
                },
              },
              {
                k: "options", label: "options", w: "170px", sortable: false,
                cell: (e) => (
                  <span className="flex flex-wrap gap-1">
                    {e.destructive && <Tag tone={e.snapshot_enabled ? "ok" : undefined}>snap</Tag>}
                    {e.destructive && <Tag tone={e.verify_enabled ? "ok" : undefined}>verify</Tag>}
                    <Tag title="cooldown per host">{e.cooldown_minutes ? `${e.cooldown_minutes} min` : "no cooldown"}</Tag>
                  </span>
                ),
              },
              { k: "state", label: "state", w: "80px", sortable: false, cell: (e) => <Tag tone={e.enabled ? "ok" : undefined}>{e.enabled ? "enabled" : "paused"}</Tag> },
              {
                k: "last", label: "last run", w: "minmax(120px,0.8fr)", sortable: false,
                cell: (e) => e.last_run_id ? (
                  <Link href={`/actions/runs/${e.last_run_id}`} className="flex items-center gap-1.5 hover:no-underline">
                    {e.last_run_status && <RunStatus s={e.last_run_status} />}
                    <span className="text-[10.5px] text-text-3">{e.last_run_at ? `${shortAgo(e.last_run_at)} ago` : ""}</span>
                  </Link>
                ) : <span className="text-text-faint">never</span>,
              },
              {
                k: "go", label: "", w: "110px", right: true, sortable: false,
                cell: (e) => (
                  <span className="flex gap-0.5" data-testid="alert-action-row" data-action-key={e.action_key}>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEditing(e)}>edit</button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setRemoving(e)}>remove</button>
                  </span>
                ),
              },
            ]}
            rows={rows}
            keyOf={(e) => e.id}
            loading={isLoading}
            empty={
              <span>
                No action is allowed yet, so a <span className="mono">labdog_action</span> label does nothing: the alert is recorded and refused. Allow an action to let alerts run it.
              </span>
            }
          />
        </div>
      </div>
      {editing && <AlertActionDialog entry={editing === "new" ? undefined : editing} allowed={rows.map((r) => r.action_key)} onClose={() => setEditing(null)} />}
      {removing && (
        <Confirm
          open
          onOpenChange={(o) => !o && setRemoving(null)}
          title={`Take ${removing.action_key} off the list?`}
          description="Alerts naming it are refused from now on. What earlier alerts did with it stays on their rows."
          confirmLabel="Remove"
          variant="destructive"
          loading={remove.isPending}
          onConfirm={() => remove.mutate(removing.id)}
        />
      )}
    </Panel>
  )
}

function LabelsPanel() {
  const rows: [string, string][] = [
    ["labdog_action=<key>", "Runs that action on the alert's host, if it is on the list above."],
    ["labdog_ai=true", "The AI investigates, when there is no action or it did not fix the alert."],
    ["labdog_ai_automate=true", "The same, and the AI may fix what it finds, behind the full-auto safeguards."],
  ]
  return (
    <Panel title="labels" meta="set on the alert rule in Grafana" pad={11}>
      <div className="flex flex-col gap-2.5 text-[11.5px]" data-testid="alert-labels">
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {rows.map(([label, what]) => (
            <li key={label} className="flex flex-wrap gap-x-2">
              <span className="mono text-text">{label}</span>
              <span className="text-text-3">{what}</span>
            </li>
          ))}
        </ul>
        <span className="text-text-3">
          With an action and an AI label, the action goes first and the AI is asked only if it did not fix the alert. A template makes a label depend on the host:
        </span>
        <CodeBlock title="Grafana alert rule labels" actions={<Copy text={SNIPPET} />} maxH={120}>
          {SNIPPET}
        </CodeBlock>
        <a className="underline hover:text-text-2" href="https://open-labdog.github.io/labdog/ui/alerts/#labels">How the labels combine</a>
      </div>
    </Panel>
  )
}

function IntakePanel({ s }: { s: AlertIntakeStatus | undefined }) {
  const origin = typeof window !== "undefined" ? window.location.origin : ""
  const last = s ? [s.last_webhook_alert_at, s.last_poll_alert_at].filter((t): t is string => !!t).sort().at(-1) : undefined
  const url = s ? `${origin}${s.webhook_path}` : ""
  return (
    <Panel title="intake" meta={!s ? "…" : s.intake_enabled ? "on" : "off"} actions={<Link href="/settings?section=fleet" className="btn btn-sm btn-ghost hover:no-underline">settings</Link>} pad={11}>
      {s && (
        <div className="flex flex-col gap-1.5 text-[11.5px]" data-testid="alert-intake">
          {!s.intake_enabled && <span style={{ color: "var(--warn)" }}>Intake is off: alerts are refused. Turn on alerts.intake_enabled.</span>}
          <span className="text-text-3">Point a Grafana webhook contact point at:</span>
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="mono min-w-0 break-all text-text">POST {url}</span>
            <Copy text={url} />
          </span>
          {!s.token_set ? (
            <span style={{ color: "var(--danger)" }}>No [alerts] webhook_token in labdog.toml: the webhook refuses every alert.</span>
          ) : !s.token_long_enough_for_full_auto ? (
            <span style={{ color: "var(--warn)" }}>The webhook token is under 32 characters: webhook alerts run no action and no full auto.</span>
          ) : (
            <span className="text-text-3">Send the token as a bearer token, or as either half of basic auth.</span>
          )}
          <span className="text-text-3">
            {last ? `Last alert ${shortAgo(last)} ago.` : "No alert received yet."}
            {s.poll_minutes > 0 ? ` Polls Alertmanager every ${s.poll_minutes} min.` : ""}
          </span>
        </div>
      )}
    </Panel>
  )
}

function AIPanel({ s }: { s: AlertIntakeStatus | undefined }) {
  const answers = s && s.ai_enabled && s.auto_investigate_enabled
  return (
    <Panel title="ai" meta={!s ? "…" : answers ? "answers labelled alerts" : "does not answer"} actions={<Link href="/settings?section=ai" className="btn btn-sm btn-ghost hover:no-underline">settings</Link>} pad={11}>
      {s && (
        <div className="flex flex-col gap-1.5 text-[11.5px]" data-testid="alert-ai">
          {!s.ai_enabled ? (
            <span className="text-text-3">The AI is off, so labdog_ai and labdog_ai_automate do nothing. Actions still run.</span>
          ) : !s.auto_investigate_enabled ? (
            <span className="text-text-3">Auto-investigate is off (ai.auto_investigate_enabled), so labdog_ai and labdog_ai_automate do nothing. The investigate button still works.</span>
          ) : (
            <span className="text-text-3">Alerts labelled labdog_ai=true are investigated at ai.alert_autonomy_level.</span>
          )}
          <span className="text-text-3">
            {s.full_auto_rules_enabled
              ? `Full auto: ${s.full_auto_rules} rule${s.full_auto_rules === 1 ? "" : "s"} in ai.alert_full_auto_rules decide, not the label.`
              : "Full auto: alerts labelled labdog_ai_automate=true, behind the safeguards."}
          </span>
        </div>
      )}
    </Panel>
  )
}

/** Alerts › Handling: what alerts are allowed to do, and whether they can. */
export function AlertHandling() {
  const { data: intake } = useQuery<AlertIntakeStatus>({ queryKey: ["alert-intake"], queryFn: () => apiFetch<AlertIntakeStatus>("/api/alerts/intake") })
  return (
    <div className="scroll flex min-h-0 flex-1 flex-col gap-3 p-3.5">
      <ActionsPanel />
      <div className="grid gap-3 lg:grid-cols-3">
        <LabelsPanel />
        <IntakePanel s={intake} />
        <AIPanel s={intake} />
      </div>
    </div>
  )
}
