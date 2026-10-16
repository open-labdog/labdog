"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import { ActionParameterForm } from "@/components/action-parameter-form"
import { Field, Modal, Picker } from "@/components/ld"
import type { ActionDefinition, AlertActionEntry, AlertActionEntryIn, Host, HostGroup } from "@/lib/types"

type Kind = AlertActionEntry["target_kind"]

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs text-text-2">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

/**
 * Allow alerts to run an action, or change how they may. One form, not a
 * wizard: an entry is an action, where it may run and what it runs with.
 * The action is fixed once saved, because Grafana rules point at it by
 * its key.
 */
export function AlertActionDialog({ entry, allowed, onClose }: { entry?: AlertActionEntry; allowed: string[]; onClose: () => void }) {
  const isEdit = !!entry
  const [actionKey, setActionKey] = useState<string>(entry?.action_key ?? "")
  const [kind, setKind] = useState<Kind>(entry?.target_kind ?? "fleet")
  const [targetId, setTargetId] = useState<number | null>(entry?.target_id ?? null)
  const [parameters, setParameters] = useState<Record<string, unknown>>(entry?.parameters ?? {})
  const [snapshot, setSnapshot] = useState(entry?.snapshot_enabled ?? true)
  const [verify, setVerify] = useState(entry?.verify_enabled ?? true)
  const [rollback, setRollback] = useState(entry?.auto_rollback ?? true)
  const [cooldown, setCooldown] = useState(entry?.cooldown_minutes ?? 60)
  const [enabled, setEnabled] = useState(entry?.enabled ?? true)

  const { data: actions } = useQuery<ActionDefinition[]>({ queryKey: ["actions-catalog"], queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"), staleTime: 60_000 })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: hosts } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })

  // A run an alert starts is always the one host it names, so only actions
  // that run on a single host can be allowed — and not one already listed.
  const choices = useMemo(
    () => (actions ?? []).filter((a) => a.supports_host && (a.key === entry?.action_key || !allowed.includes(a.key))),
    [actions, allowed, entry?.action_key],
  )
  const action = (actions ?? []).find((a) => a.key === actionKey) ?? null

  const save = useApiMutation<AlertActionEntry, AlertActionEntryIn>({
    mutationFn: (body) =>
      isEdit
        ? apiFetch<AlertActionEntry>(`/api/alerts/actions/${entry!.id}`, { method: "PUT", json: body })
        : apiFetch<AlertActionEntry>("/api/alerts/actions", { method: "POST", json: body }),
    invalidateKeys: [["alert-actions"], ["alert-intake"], ["host-alerts"]],
    onSuccess: () => {
      showSuccess(isEdit ? "Saved" : `Alerts may now run ${actionKey}`)
      onClose()
    },
  })

  const ready = !!action && (kind === "fleet" || targetId !== null)
  const submit = () =>
    save.mutate({
      action_key: actionKey,
      target_kind: kind,
      target_id: kind === "fleet" ? null : targetId,
      parameters,
      snapshot_enabled: snapshot,
      verify_enabled: verify,
      auto_rollback: rollback,
      cooldown_minutes: cooldown,
      enabled,
    })

  return (
    <Modal
      title={isEdit ? `Alerts running ${entry!.action_key}` : "Allow an action"}
      description="An alert labelled labdog_action=<this action's key> runs it on the host the alert names, if that host is in Where."
      w={620}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ml-auto" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={!ready || save.isPending} onClick={submit} data-testid="alert-action-submit">
            {save.isPending ? "Saving…" : isEdit ? "Save changes" : "Allow"}
          </button>
        </>
      }
    >
      <Field as="div" label="action" hint={action ? <>Grafana label: <span className="mono">labdog_action={action.key}</span>{action.description ? ` — ${action.description}` : ""}</> : "Only actions that run on a single host."}>
        <select className="inp" value={actionKey} disabled={isEdit} onChange={(e) => { setActionKey(e.target.value); setParameters({}) }} data-testid="alert-action-picker">
          <option value="">Pick an action…</option>
          {choices.map((a) => (
            <option key={a.key} value={a.key}>{a.name} ({a.key})</option>
          ))}
        </select>
      </Field>

      <Field as="div" label="where" hint="Alerts on hosts outside this are refused.">
        <div className="flex gap-3.5" role="radiogroup" aria-label="where">
          {(["fleet", "group", "host"] as const).map((k) => (
            <label key={k} className="flex items-center gap-1.5 text-xs text-text-2">
              <input type="radio" name="alert-action-where" checked={kind === k} onChange={() => { setKind(k); setTargetId(null) }} data-testid={`alert-action-where-${k}`} style={{ accentColor: "var(--accent)" }} />
              {k === "fleet" ? "All hosts" : k === "group" ? "Group" : "Host"}
            </label>
          ))}
        </div>
        {kind === "group" && (
          <Picker className="mt-1.5" options={(groups ?? []).map((g) => ({ value: g.id, label: g.name }))} value={targetId} onChange={setTargetId} placeholder="Search groups…" empty="No group matches." testId="alert-action-group" />
        )}
        {kind === "host" && (
          <Picker className="mt-1.5" options={(hosts ?? []).map((h) => ({ value: h.id, label: h.hostname, meta: h.ip_address }))} value={targetId} onChange={setTargetId} placeholder="Search hosts by name or IP…" empty="No host matches." testId="alert-action-host" />
        )}
      </Field>

      {action && action.parameters.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="tt">parameters</span>
          <ActionParameterForm action={action} values={parameters} onChange={setParameters} />
        </div>
      )}

      {action?.destructive && (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          <Toggle label="Pre-run snapshot" checked={snapshot} onChange={setSnapshot} />
          <Toggle label="Post-run verify" checked={verify} onChange={setVerify} />
          <Toggle label="Auto-rollback on failure" checked={rollback} onChange={setRollback} />
        </div>
      )}

      <Field label="cooldown (minutes)" htmlFor="alert-action-cooldown" hint="No second alert run of this action on the same host within this time. 0 means none." className="max-w-[260px]">
        <input id="alert-action-cooldown" className="inp" type="number" min={0} max={10080} value={cooldown} onChange={(e) => setCooldown(Math.max(0, Number(e.target.value) || 0))} />
      </Field>

      <Toggle label="Enabled (off pauses it: alerts naming it are refused)" checked={enabled} onChange={setEnabled} />
    </Modal>
  )
}
