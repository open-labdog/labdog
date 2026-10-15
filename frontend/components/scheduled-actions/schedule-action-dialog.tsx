"use client"

import { useEffect, useMemo, useReducer, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import { ActionParameterForm } from "@/components/action-parameter-form"
import { CronInput } from "@/components/scheduled-actions/cron-input"
import { Banner, Facts, Field, Modal, Picker, Steps } from "@/components/ld"
import type {
  ActionDefinition,
  AlertTriggerMatch,
  Host,
  HostGroup,
  ScheduledAction,
  ScheduledActionCreate,
  ScheduledActionTargetKind,
  ScheduledActionTrigger,
  ScheduledActionUpdate,
} from "@/lib/types"

export type ScheduleStep = "picker" | "parameters" | "schedule" | "review"
const STEPS: { k: ScheduleStep; label: string }[] = [
  { k: "picker", label: "Action & target" },
  { k: "parameters", label: "Parameters" },
  { k: "schedule", label: "When" },
  { k: "review", label: "Review" },
]

interface ScheduleActionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  preselected?: {
    action_key?: string
    target?: { kind: ScheduledActionTargetKind; id: number | null }
  }
  scheduledAction?: ScheduledAction
}

interface State {
  step: ScheduleStep
  actionKey: string | null
  targetKind: ScheduledActionTargetKind | null
  targetId: number | null
  parameters: Record<string, unknown>
  trigger: ScheduledActionTrigger
  scheduleCron: string
  alertMatch: string
  cooldown: number
  investigateOnFailure: boolean
  enabled: boolean
  snapshotEnabled: boolean
  verifyEnabled: boolean
  autoRollback: boolean
  batchSize: number
}

type Action =
  | { type: "SET_STEP"; step: ScheduleStep }
  | { type: "SET_ACTION_KEY"; key: string | null }
  | { type: "SET_TARGET"; kind: ScheduledActionTargetKind | null; id: number | null }
  | { type: "SET_PARAMS"; params: Record<string, unknown> }
  | { type: "SET_CRON"; cron: string }
  | { type: "SET_TRIGGER"; trigger: ScheduledActionTrigger }
  | { type: "SET_ALERT"; match?: string; cooldown?: number; investigateOnFailure?: boolean }
  | { type: "SET_ENABLED"; enabled: boolean }
  | { type: "SET_OPTIONS"; snapshotEnabled?: boolean; verifyEnabled?: boolean; autoRollback?: boolean; batchSize?: number }
  | { type: "RESET"; initial: State }

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "SET_STEP":
      return { ...state, step: action.step }
    case "SET_ACTION_KEY":
      return { ...state, actionKey: action.key, parameters: {} }
    case "SET_TARGET":
      return { ...state, targetKind: action.kind, targetId: action.id }
    case "SET_PARAMS":
      return { ...state, parameters: action.params }
    case "SET_CRON":
      return { ...state, scheduleCron: action.cron }
    case "SET_TRIGGER":
      // The target kinds allowed differ (an alert trigger's run is always
      // on one host), so the target is picked again.
      return { ...state, trigger: action.trigger, targetKind: state.trigger === action.trigger ? state.targetKind : null, targetId: state.trigger === action.trigger ? state.targetId : null }
    case "SET_ALERT":
      return {
        ...state,
        alertMatch: action.match ?? state.alertMatch,
        cooldown: action.cooldown ?? state.cooldown,
        investigateOnFailure: action.investigateOnFailure ?? state.investigateOnFailure,
      }
    case "SET_ENABLED":
      return { ...state, enabled: action.enabled }
    case "SET_OPTIONS":
      return {
        ...state,
        snapshotEnabled: action.snapshotEnabled ?? state.snapshotEnabled,
        verifyEnabled: action.verifyEnabled ?? state.verifyEnabled,
        autoRollback: action.autoRollback ?? state.autoRollback,
        batchSize: action.batchSize ?? state.batchSize,
      }
    case "RESET":
      return action.initial
  }
}

function buildInitialState(preselected: ScheduleActionDialogProps["preselected"], scheduledAction: ScheduleActionDialogProps["scheduledAction"]): State {
  if (scheduledAction) {
    // Always start edit mode at the picker step too — both fields are
    // locked, so the operator sees what they're editing before touching
    // parameters. The explanation banner is rendered in PickerStep when
    // both locks are active.
    return {
      step: "picker",
      actionKey: scheduledAction.action_key,
      targetKind: scheduledAction.target_kind,
      targetId: scheduledAction.target_id,
      parameters: { ...scheduledAction.parameters },
      trigger: scheduledAction.trigger ?? "cron",
      scheduleCron: scheduledAction.schedule_cron ?? "",
      alertMatch: scheduledAction.alert_match ?? "",
      cooldown: scheduledAction.alert_cooldown_minutes ?? 60,
      investigateOnFailure: scheduledAction.investigate_on_failure ?? true,
      enabled: scheduledAction.enabled,
      snapshotEnabled: scheduledAction.snapshot_enabled,
      verifyEnabled: scheduledAction.verify_enabled,
      autoRollback: scheduledAction.auto_rollback,
      batchSize: scheduledAction.batch_size,
    }
  }
  // Always start at picker — even when preselected. The picker shows the
  // locked fields and gives the operator one beat to confirm context
  // before they're asked for parameters.
  return {
    step: "picker",
    actionKey: preselected?.action_key ?? null,
    targetKind: preselected?.target?.kind ?? null,
    targetId: preselected?.target?.id ?? null,
    parameters: {},
    trigger: "cron",
    scheduleCron: "",
    alertMatch: "",
    cooldown: 60,
    investigateOnFailure: true,
    enabled: false,
    snapshotEnabled: true,
    verifyEnabled: true,
    autoRollback: true,
    batchSize: 1,
  }
}

function nextStep(step: ScheduleStep): ScheduleStep {
  const order: ScheduleStep[] = ["picker", "parameters", "schedule", "review"]
  return order[Math.min(order.indexOf(step) + 1, order.length - 1)]
}
function prevStep(step: ScheduleStep): ScheduleStep {
  const order: ScheduleStep[] = ["picker", "parameters", "schedule", "review"]
  return order[Math.max(order.indexOf(step) - 1, 0)]
}

export function ScheduleActionDialog({ open, onOpenChange, preselected, scheduledAction }: ScheduleActionDialogProps) {
  const isEdit = !!scheduledAction
  const initial = useMemo(() => buildInitialState(preselected, scheduledAction), [preselected, scheduledAction])
  const [state, dispatch] = useReducer(reducer, initial)

  useEffect(() => {
    if (open) dispatch({ type: "RESET", initial })
  }, [open, initial])

  const { data: actions } = useQuery<ActionDefinition[]>({ queryKey: ["actions-catalog"], queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"), enabled: open, staleTime: 60_000 })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups"), enabled: open })
  const { data: hosts } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), enabled: open })

  const action = useMemo(() => actions?.find((a) => a.key === state.actionKey) ?? null, [actions, state.actionKey])

  const createMutation = useApiMutation<ScheduledAction, ScheduledActionCreate>({
    mutationFn: (body) => apiFetch<ScheduledAction>("/api/scheduled-actions", { method: "POST", json: body }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"]],
    onSuccess: () => { showSuccess("Schedule created"); onOpenChange(false) },
  })
  const updateMutation = useApiMutation<ScheduledAction, { id: number; body: ScheduledActionUpdate }>({
    mutationFn: ({ id, body }) => apiFetch<ScheduledAction>(`/api/scheduled-actions/${id}`, { method: "PUT", json: { target_kind: state.targetKind, target_id: state.targetId, action_key: state.actionKey, ...body } }),
    invalidateKeys: [["scheduled-actions"], ["scheduled-actions-by-target"]],
    onSuccess: () => { showSuccess("Schedule updated"); onOpenChange(false) },
  })

  function canAdvance(): boolean {
    switch (state.step) {
      case "picker":
        if (!state.actionKey || !state.targetKind) return false
        if (state.targetKind === "fleet") return true
        return state.targetId !== null
      case "parameters":
        return action !== null
      case "schedule":
        return state.trigger === "alert" ? state.alertMatch.trim().length > 0 : state.scheduleCron.trim().length > 0
      case "review":
        return true
    }
  }

  function handleSubmit() {
    if (!state.actionKey || !state.targetKind) return
    const alert = state.trigger === "alert"
    const when = {
      trigger: state.trigger,
      schedule_cron: alert ? null : state.scheduleCron,
      alert_match: alert ? state.alertMatch : null,
      alert_cooldown_minutes: state.cooldown,
      investigate_on_failure: state.investigateOnFailure,
    }
    const options = { snapshot_enabled: state.snapshotEnabled, verify_enabled: state.verifyEnabled, auto_rollback: state.autoRollback, batch_size: alert ? 1 : state.batchSize }
    if (isEdit && scheduledAction) {
      updateMutation.mutate({
        id: scheduledAction.id,
        body: { parameters: state.parameters, enabled: state.enabled, ...when, ...options },
      })
    } else {
      createMutation.mutate({
        action_key: state.actionKey, target_kind: state.targetKind, target_id: state.targetKind === "fleet" ? null : state.targetId,
        parameters: state.parameters, enabled: state.enabled, ...when, ...options,
      })
    }
  }

  const submitting = createMutation.isPending || updateMutation.isPending
  const submitError = createMutation.error || updateMutation.error

  const targetLabel = (() => {
    if (state.targetKind === "host") return hosts?.find((h) => h.id === state.targetId)?.hostname ?? (state.targetId ? `host #${state.targetId}` : "—")
    if (state.targetKind === "group") return groups?.find((g) => g.id === state.targetId)?.name ?? (state.targetId ? `group #${state.targetId}` : "—")
    return "fleet (all hosts)"
  })()

  return (
    <Modal
      title={isEdit ? "Edit scheduled action" : "Schedule an action"}
      w={640}
      onClose={() => onOpenChange(false)}
      footer={
        <>
          {state.step !== "picker" && (
            <button type="button" className="btn" onClick={() => dispatch({ type: "SET_STEP", step: prevStep(state.step) })}>Back</button>
          )}
          <button type="button" className="btn ml-auto" onClick={() => onOpenChange(false)}>Cancel</button>
          {state.step === "review" ? (
            <button type="button" className="btn btn-primary" disabled={submitting} data-testid="schedule-submit" onClick={handleSubmit}>
              {submitting ? "Saving…" : isEdit ? "Save changes" : "Create schedule"}
            </button>
          ) : (
            <button type="button" className="btn btn-primary" disabled={!canAdvance()} onClick={() => dispatch({ type: "SET_STEP", step: nextStep(state.step) })}>Continue</button>
          )}
        </>
      }
    >
      <Steps steps={STEPS} current={state.step} />

      {state.step === "picker" && (
        <PickerStep
          actions={actions ?? []}
          groups={groups ?? []}
          hosts={hosts ?? []}
          actionKey={state.actionKey}
          targetKind={state.targetKind}
          targetId={state.targetId}
          onActionChange={(k) => dispatch({ type: "SET_ACTION_KEY", key: k })}
          onTargetChange={(kind, id) => dispatch({ type: "SET_TARGET", kind, id })}
          trigger={state.trigger}
          onTriggerChange={(t) => dispatch({ type: "SET_TRIGGER", trigger: t })}
          actionLocked={isEdit || !!preselected?.action_key}
          targetLocked={isEdit || !!preselected?.target}
          isEdit={isEdit}
        />
      )}

      {state.step === "parameters" && action && (
        <>
          <ActionSummary action={action} targetLabel={targetLabel} />
          <ActionParameterForm action={action} values={state.parameters} onChange={(params) => dispatch({ type: "SET_PARAMS", params })} />
          {action.parameters.length === 0 && <p className="text-[11.5px] text-text-3">This action takes no parameters.</p>}
        </>
      )}

      {state.step === "schedule" && action && state.trigger === "cron" && (
        <>
          <ActionSummary action={action} targetLabel={targetLabel} />
          <CronInput value={state.scheduleCron} onChange={(cron) => dispatch({ type: "SET_CRON", cron })} />
          <label className="flex items-center gap-2 text-xs text-text">
            <input type="checkbox" checked={state.enabled} onChange={(e) => dispatch({ type: "SET_ENABLED", enabled: e.target.checked })} />
            Enable immediately (start firing on the next due tick)
          </label>
        </>
      )}

      {state.step === "schedule" && action && state.trigger === "alert" && (
        <>
          <ActionSummary action={action} targetLabel={targetLabel} />
          <AlertWhen
            targetKind={state.targetKind}
            targetId={state.targetId}
            match={state.alertMatch}
            cooldown={state.cooldown}
            investigateOnFailure={state.investigateOnFailure}
            onChange={(patch) => dispatch({ type: "SET_ALERT", ...patch })}
          />
          <label className="flex items-center gap-2 text-xs text-text">
            <input type="checkbox" checked={state.enabled} onChange={(e) => dispatch({ type: "SET_ENABLED", enabled: e.target.checked })} />
            Enable immediately (react to the next matching alert)
          </label>
        </>
      )}

      {state.step === "review" && action && (
        <>
          <ActionSummary action={action} targetLabel={targetLabel} />
          <Facts
            items={[
              ...(state.trigger === "alert"
                ? [
                    { k: "when an alert", v: <span className="whitespace-pre-wrap">{state.alertMatch.trim()}</span>, mono: true },
                    { k: "runs on", v: "the host the alert names" },
                    { k: "cooldown", v: `${state.cooldown} min` },
                    { k: "if it doesn't fix it", v: state.investigateOnFailure ? "hand the alert to the AI" : "nothing more" },
                  ]
                : [{ k: "schedule", v: state.scheduleCron, mono: true }]),
              { k: "enabled", v: state.enabled ? "yes" : "no" },
              ...Object.entries(state.parameters).map(([k, v]) => ({ k, v: v === null || v === undefined ? "—" : String(v), mono: true })),
            ]}
          />

          {action.destructive && (
            <div className="flex flex-col gap-2 rounded-r border border-line bg-surface-2 p-[11px]">
              <span className="tt">destructive action options</span>
              <Toggle label="Pre-run snapshot" checked={state.snapshotEnabled} onChange={(v) => dispatch({ type: "SET_OPTIONS", snapshotEnabled: v })} />
              <Toggle label="Post-run verify" checked={state.verifyEnabled} onChange={(v) => dispatch({ type: "SET_OPTIONS", verifyEnabled: v })} />
              <Toggle label="Auto-rollback on failure" checked={state.autoRollback} onChange={(v) => dispatch({ type: "SET_OPTIONS", autoRollback: v })} />
              {state.targetKind !== "host" && state.trigger === "cron" && (
                <Field label="batch size" htmlFor="schedule-batch-size" className="max-w-[120px]">
                  <input id="schedule-batch-size" type="number" min={1} className="inp mono num" value={state.batchSize} onChange={(e) => dispatch({ type: "SET_OPTIONS", batchSize: Math.max(1, Number(e.target.value)) })} />
                </Field>
              )}
            </div>
          )}

          {submitError && <Banner tone="danger">{submitError.message}</Banner>}
        </>
      )}
    </Modal>
  )
}

function PickerStep({
  actions,
  groups,
  hosts,
  actionKey,
  targetKind,
  targetId,
  onActionChange,
  onTargetChange,
  trigger,
  onTriggerChange,
  actionLocked,
  targetLocked,
  isEdit,
}: {
  actions: ActionDefinition[]
  groups: HostGroup[]
  hosts: Host[]
  actionKey: string | null
  targetKind: ScheduledActionTargetKind | null
  targetId: number | null
  onActionChange: (k: string | null) => void
  onTargetChange: (kind: ScheduledActionTargetKind | null, id: number | null) => void
  trigger: ScheduledActionTrigger
  onTriggerChange: (t: ScheduledActionTrigger) => void
  actionLocked: boolean
  targetLocked: boolean
  isEdit: boolean
}) {
  const action = actions.find((a) => a.key === actionKey) ?? null
  // Built-in pseudo-actions (sync / drift_check / collect_state) have
  // their own UI entry points — they're not surfaced for new schedules.
  // Legacy rows that already target a builtin still need to render in
  // edit mode so the locked picker can show the current value.
  const packs = actions.filter((a) => !a.key.startsWith("_builtin.") || a.key === actionKey)

  return (
    <>
      {isEdit && (
        <Banner tone="idle">Action, target and when it runs are fixed. To change them, delete this schedule and create a new one.</Banner>
      )}

      <Field as="div" label="runs">
        <div className="flex gap-3.5" role="radiogroup" aria-label="runs">
          {([["cron", "on a schedule"], ["alert", "when an alert fires on a host"]] as const).map(([value, label]) => (
            <label key={value} className="flex items-center gap-1.5 text-xs text-text-2" style={{ opacity: isEdit ? 0.6 : 1 }}>
              <input type="radio" name="trigger" checked={trigger === value} disabled={isEdit} onChange={() => onTriggerChange(value)} data-testid={`trigger-${value}`} style={{ accentColor: "var(--accent)" }} />
              {label}
            </label>
          ))}
        </div>
      </Field>

      <Field as="div" label="action" hint={action?.description}>
        <select value={actionKey ?? ""} disabled={actionLocked} onChange={(e) => onActionChange(e.target.value || null)} className="inp" data-testid="action-picker">
          <option value="">Select an action…</option>
          {packs.map((a) => <option key={a.key} value={a.key}>{a.name} — {a.pack_name}</option>)}
        </select>
      </Field>

      <Field as="div" label={trigger === "alert" ? "whose alerts" : "target"} hint={trigger === "alert" ? "The action runs on the one host the alert names, if it is in this target." : undefined}>
        <div className="flex gap-3.5">
          {(["host", "group", "fleet"] as const).map((kind) => {
            // An alert trigger's run is always a single-host run, so it
            // only needs the action to run on a host, whatever the scope.
            const unsupported = action
              ? trigger === "alert"
                ? !action.supports_host
                : (kind === "fleet" && !action.supports_fleet) || (kind === "group" && !action.supports_group) || (kind === "host" && !action.supports_host)
              : false
            const disabled = targetLocked || unsupported
            return (
              <label key={kind} className="flex items-center gap-1.5 text-xs text-text-2" style={{ opacity: disabled ? 0.4 : 1, cursor: disabled ? "not-allowed" : "pointer" }} title={disabled ? "Action does not support this target kind" : undefined}>
                <input type="radio" name="target-kind" checked={targetKind === kind} disabled={disabled} onChange={() => onTargetChange(kind, null)} data-testid={`target-${kind}`} style={{ accentColor: "var(--accent)" }} />
                <span className="capitalize">{kind}</span>
              </label>
            )
          })}
        </div>

        {targetKind === "host" && (
          <Picker
            className="mt-1.5"
            options={hosts.map((h) => ({ value: h.id, label: h.hostname, meta: h.ip_address }))}
            value={targetId}
            disabled={targetLocked}
            onChange={(id) => onTargetChange("host", id)}
            placeholder="Search hosts by name or IP…"
            empty="No host matches."
            testId="target-host-picker"
          />
        )}

        {targetKind === "group" && (
          <Picker
            className="mt-1.5"
            options={groups.map((g) => ({ value: g.id, label: g.name }))}
            value={targetId}
            disabled={targetLocked}
            onChange={(id) => onTargetChange("group", id)}
            placeholder="Search groups…"
            empty="No group matches."
            testId="target-group-picker"
          />
        )}

        {targetKind === "fleet" && trigger === "cron" && <p className="mt-1.5 text-[11px] text-warn">This will run against every host in the inventory.</p>}
        {targetKind === "fleet" && trigger === "alert" && <p className="mt-1.5 text-[11px] text-text-3">A matching alert on any host runs the action on that host.</p>}
      </Field>
    </>
  )
}

function ActionSummary({ action, targetLabel }: { action: ActionDefinition; targetLabel: string }) {
  return (
    <div className="rounded-r border border-line bg-surface-2 px-2.5 py-2 text-[11.5px]">
      <span className="text-text">{action.name}</span>
      <span className="text-text-faint"> → </span>
      <span className="text-text-2">{targetLabel}</span>
    </div>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs text-text-2">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

/**
 * The alert half of the When step: which alerts, the cooldown, and what
 * happens if the action does not fix it. Underneath, the recent alerts the
 * match and target would have fired on, so a trigger can be checked
 * against what has actually arrived before it is switched on.
 */
function AlertWhen({
  targetKind,
  targetId,
  match,
  cooldown,
  investigateOnFailure,
  onChange,
}: {
  targetKind: ScheduledActionTargetKind | null
  targetId: number | null
  match: string
  cooldown: number
  investigateOnFailure: boolean
  onChange: (patch: { match?: string; cooldown?: number; investigateOnFailure?: boolean }) => void
}) {
  // The preview follows the match a moment after typing stops.
  const [settled, setSettled] = useState(match)
  useEffect(() => {
    const t = setTimeout(() => setSettled(match), 400)
    return () => clearTimeout(t)
  }, [match])
  const preview = useQuery<AlertTriggerMatch[], Error>({
    queryKey: ["alert-trigger-preview", targetKind, targetId, settled],
    queryFn: () =>
      apiFetch<AlertTriggerMatch[]>("/api/scheduled-actions/preview-matches", {
        method: "POST",
        json: { target_kind: targetKind, target_id: targetKind === "fleet" ? null : targetId, alert_match: settled },
      }),
    enabled: !!targetKind && settled.trim().length > 0,
    retry: false,
  })

  return (
    <>
      <Field
        label="alerts"
        htmlFor="alert-match"
        hint="One rule per line; all conditions on a line must hold. An alert name (NginxDown), or labels: severity=critical, severity!=info, labdog_action=restart-nginx. Hosts come from the target."
      >
        <textarea
          id="alert-match"
          rows={4}
          className="inp mono text-[11.5px]"
          spellCheck={false}
          placeholder={"NginxDown\nlabdog_action=restart-nginx"}
          value={match}
          onChange={(e) => onChange({ match: e.target.value })}
          data-testid="alert-match"
        />
      </Field>
      <div className="flex flex-col gap-1 text-[11.5px]" data-testid="alert-preview">
        {!match.trim() ? null : preview.error ? (
          <span className="text-danger">{preview.error.message}</span>
        ) : preview.data && preview.data.length === 0 ? (
          <span className="text-text-3">No recorded alert matches yet.</span>
        ) : preview.data ? (
          <>
            <span className="text-text-3">Would have run for {preview.data.length === 20 ? "the last 20" : preview.data.length} recorded alert{preview.data.length === 1 ? "" : "s"}:</span>
            <ul className="m-0 flex max-h-[110px] list-none flex-col gap-0.5 overflow-auto p-0">
              {preview.data.map((m) => (
                <li key={m.alert_event_id} className="mono text-[11px] text-text-2">
                  {m.alertname} <span className="text-text-faint">on {m.hostname ?? "—"} · {new Date(m.created_at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
      <Field label="cooldown (minutes)" htmlFor="alert-cooldown" hint="No second run of this schedule on the same host within this time." className="max-w-[220px]">
        <input id="alert-cooldown" type="number" min={0} max={10080} className="inp mono num" value={cooldown} onChange={(e) => onChange({ cooldown: Math.max(0, Number(e.target.value)) })} />
      </Field>
      <label className="flex items-center gap-2 text-xs text-text">
        <input type="checkbox" checked={investigateOnFailure} onChange={(e) => onChange({ investigateOnFailure: e.target.checked })} data-testid="alert-investigate-on-failure" />
        If the action does not fix the alert, hand it to the AI
      </label>
    </>
  )
}
