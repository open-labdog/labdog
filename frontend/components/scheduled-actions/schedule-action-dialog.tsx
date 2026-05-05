"use client"

import { useEffect, useMemo, useReducer } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ActionParameterForm } from "@/components/action-parameter-form"
import { HostCombobox } from "@/components/host-combobox"
import { CronInput } from "@/components/scheduled-actions/cron-input"
import {
  WizardStepIndicator,
  type ScheduleStep,
} from "@/components/scheduled-actions/wizard-step-indicator"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import type {
  ActionDefinition,
  HostGroup,
  ScheduledAction,
  ScheduledActionCreate,
  ScheduledActionTargetKind,
  ScheduledActionUpdate,
} from "@/lib/types"

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
  scheduleCron: string
  enabled: boolean
  snapshotEnabled: boolean
  verifyEnabled: boolean
  autoRollback: boolean
  batchSize: number
}

type Action =
  | { type: "SET_STEP"; step: ScheduleStep }
  | { type: "SET_ACTION_KEY"; key: string | null }
  | {
      type: "SET_TARGET"
      kind: ScheduledActionTargetKind | null
      id: number | null
    }
  | { type: "SET_PARAMS"; params: Record<string, unknown> }
  | { type: "SET_CRON"; cron: string }
  | { type: "SET_ENABLED"; enabled: boolean }
  | {
      type: "SET_OPTIONS"
      snapshotEnabled?: boolean
      verifyEnabled?: boolean
      autoRollback?: boolean
      batchSize?: number
    }
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

function buildInitialState(
  preselected: ScheduleActionDialogProps["preselected"],
  scheduledAction: ScheduleActionDialogProps["scheduledAction"],
): State {
  if (scheduledAction) {
    return {
      step: "parameters",
      actionKey: scheduledAction.action_key,
      targetKind: scheduledAction.target_kind,
      targetId: scheduledAction.target_id,
      parameters: { ...scheduledAction.parameters },
      scheduleCron: scheduledAction.schedule_cron ?? "",
      enabled: scheduledAction.enabled,
      snapshotEnabled: scheduledAction.snapshot_enabled,
      verifyEnabled: scheduledAction.verify_enabled,
      autoRollback: scheduledAction.auto_rollback,
      batchSize: scheduledAction.batch_size,
    }
  }
  const startStep: ScheduleStep =
    preselected?.action_key && preselected?.target ? "parameters" : "picker"
  return {
    step: startStep,
    actionKey: preselected?.action_key ?? null,
    targetKind: preselected?.target?.kind ?? null,
    targetId: preselected?.target?.id ?? null,
    parameters: {},
    scheduleCron: "",
    enabled: false,
    snapshotEnabled: true,
    verifyEnabled: true,
    autoRollback: true,
    batchSize: 1,
  }
}

export function ScheduleActionDialog({
  open,
  onOpenChange,
  preselected,
  scheduledAction,
}: ScheduleActionDialogProps) {
  const isEdit = !!scheduledAction
  const initial = useMemo(
    () => buildInitialState(preselected, scheduledAction),
    [preselected, scheduledAction],
  )
  const [state, dispatch] = useReducer(reducer, initial)

  // Reset state when the dialog re-opens with new props.
  useEffect(() => {
    if (open) dispatch({ type: "RESET", initial })
  }, [open, initial])

  const { data: actions } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    enabled: open,
    staleTime: 60_000,
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
    enabled: open,
  })

  const action = useMemo(
    () => actions?.find((a) => a.key === state.actionKey) ?? null,
    [actions, state.actionKey],
  )

  const createMutation = useApiMutation<ScheduledAction, ScheduledActionCreate>({
    mutationFn: (body) =>
      apiFetch<ScheduledAction>("/api/scheduled-actions", {
        method: "POST",
        json: body,
      }),
    invalidateKeys: [
      ["scheduled-actions"],
      ["scheduled-actions-by-target"],
    ],
    onSuccess: () => {
      showSuccess("Schedule created")
      onOpenChange(false)
    },
  })

  const updateMutation = useApiMutation<
    ScheduledAction,
    { id: number; body: ScheduledActionUpdate }
  >({
    mutationFn: ({ id, body }) =>
      apiFetch<ScheduledAction>(`/api/scheduled-actions/${id}`, {
        method: "PUT",
        json: {
          target_kind: state.targetKind,
          target_id: state.targetId,
          action_key: state.actionKey,
          ...body,
        },
      }),
    invalidateKeys: [
      ["scheduled-actions"],
      ["scheduled-actions-by-target"],
    ],
    onSuccess: () => {
      showSuccess("Schedule updated")
      onOpenChange(false)
    },
  })

  function gotoStep(step: ScheduleStep) {
    dispatch({ type: "SET_STEP", step })
  }

  function canAdvance(): boolean {
    switch (state.step) {
      case "picker":
        if (!state.actionKey || !state.targetKind) return false
        if (state.targetKind === "fleet") return true
        return state.targetId !== null
      case "parameters":
        return action !== null
      case "schedule":
        return state.scheduleCron.trim().length > 0
      case "review":
        return true
    }
  }

  function handleSubmit() {
    if (!state.actionKey || !state.targetKind) return
    if (isEdit && scheduledAction) {
      updateMutation.mutate({
        id: scheduledAction.id,
        body: {
          parameters: state.parameters,
          schedule_cron: state.scheduleCron,
          enabled: state.enabled,
          snapshot_enabled: state.snapshotEnabled,
          verify_enabled: state.verifyEnabled,
          auto_rollback: state.autoRollback,
          batch_size: state.batchSize,
        },
      })
    } else {
      createMutation.mutate({
        action_key: state.actionKey,
        target_kind: state.targetKind,
        target_id: state.targetKind === "fleet" ? null : state.targetId,
        parameters: state.parameters,
        schedule_cron: state.scheduleCron,
        enabled: state.enabled,
        snapshot_enabled: state.snapshotEnabled,
        verify_enabled: state.verifyEnabled,
        auto_rollback: state.autoRollback,
        batch_size: state.batchSize,
      })
    }
  }

  const submitting = createMutation.isPending || updateMutation.isPending
  const submitError = createMutation.error || updateMutation.error

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit scheduled action" : "Schedule an action"}
          </DialogTitle>
        </DialogHeader>

        <div className="mt-2">
          <WizardStepIndicator current={state.step} />
        </div>

        {state.step === "picker" && (
          <PickerStep
            actions={actions ?? []}
            groups={groups ?? []}
            actionKey={state.actionKey}
            targetKind={state.targetKind}
            targetId={state.targetId}
            onActionChange={(k) => dispatch({ type: "SET_ACTION_KEY", key: k })}
            onTargetChange={(kind, id) =>
              dispatch({ type: "SET_TARGET", kind, id })
            }
            actionLocked={!!preselected?.action_key}
            targetLocked={!!preselected?.target}
          />
        )}

        {state.step === "parameters" && action && (
          <div className="mt-4">
            <ActionPickerSummary action={action} state={state} groups={groups ?? []} />
            <div className="mt-4">
              <ActionParameterForm
                action={action}
                values={state.parameters}
                onChange={(params) => dispatch({ type: "SET_PARAMS", params })}
              />
              {action.parameters.length === 0 && (
                <p className="text-sm text-slate-500">
                  This action takes no parameters.
                </p>
              )}
            </div>
          </div>
        )}

        {state.step === "schedule" && action && (
          <div className="mt-4 space-y-4">
            <ActionPickerSummary action={action} state={state} groups={groups ?? []} />
            <div className="space-y-2">
              <Label className="text-sm font-medium text-slate-200">
                Cron expression
              </Label>
              <CronInput
                value={state.scheduleCron}
                onChange={(cron) => dispatch({ type: "SET_CRON", cron })}
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                id="schedule-enabled"
                type="checkbox"
                checked={state.enabled}
                onChange={(e) =>
                  dispatch({ type: "SET_ENABLED", enabled: e.target.checked })
                }
                className="h-4 w-4 rounded border-slate-600"
              />
              <Label htmlFor="schedule-enabled" className="text-sm text-slate-300">
                Enable immediately (start firing on the next due tick)
              </Label>
            </div>
          </div>
        )}

        {state.step === "review" && action && (
          <div className="mt-4 space-y-4">
            <ActionPickerSummary action={action} state={state} groups={groups ?? []} />
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-1.5 text-sm">
              <SummaryRow label="Schedule" value={state.scheduleCron} />
              <SummaryRow label="Enabled" value={state.enabled ? "Yes" : "No"} />
              {Object.keys(state.parameters).length > 0 && (
                <SummaryRow
                  label="Parameters"
                  value={JSON.stringify(state.parameters)}
                />
              )}
            </div>

            {action.destructive && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-950/20 p-4 space-y-3">
                <p className="text-sm font-medium text-amber-300">
                  Destructive action options
                </p>
                <Toggle
                  label="Pre-run snapshot"
                  checked={state.snapshotEnabled}
                  onChange={(v) =>
                    dispatch({ type: "SET_OPTIONS", snapshotEnabled: v })
                  }
                />
                <Toggle
                  label="Post-run verify"
                  checked={state.verifyEnabled}
                  onChange={(v) =>
                    dispatch({ type: "SET_OPTIONS", verifyEnabled: v })
                  }
                />
                <Toggle
                  label="Auto-rollback on failure"
                  checked={state.autoRollback}
                  onChange={(v) =>
                    dispatch({ type: "SET_OPTIONS", autoRollback: v })
                  }
                />
                {state.targetKind !== "host" && (
                  <div className="flex items-center gap-3">
                    <Label className="text-sm text-slate-300">Batch size</Label>
                    <Input
                      type="number"
                      min={1}
                      value={state.batchSize}
                      onChange={(e) =>
                        dispatch({
                          type: "SET_OPTIONS",
                          batchSize: Math.max(1, Number(e.target.value)),
                        })
                      }
                      className="w-20"
                    />
                  </div>
                )}
              </div>
            )}

            {submitError && (
              <p className="text-sm text-red-400">{submitError.message}</p>
            )}
          </div>
        )}

        <DialogFooter className="mt-4">
          <div className="flex w-full items-center justify-between gap-2">
            <div>
              {state.step !== "picker" && state.step !== getInitialStep(state, isEdit) && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => gotoStep(prevStep(state.step))}
                >
                  Back
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              {state.step === "review" ? (
                <Button
                  type="button"
                  onClick={handleSubmit}
                  disabled={submitting}
                  data-testid="schedule-submit"
                >
                  {submitting
                    ? "Saving…"
                    : isEdit
                    ? "Save changes"
                    : "Create schedule"}
                </Button>
              ) : (
                <Button
                  type="button"
                  onClick={() => gotoStep(nextStep(state.step))}
                  disabled={!canAdvance()}
                >
                  Continue
                </Button>
              )}
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PickerStep({
  actions,
  groups,
  actionKey,
  targetKind,
  targetId,
  onActionChange,
  onTargetChange,
  actionLocked,
  targetLocked,
}: {
  actions: ActionDefinition[]
  groups: HostGroup[]
  actionKey: string | null
  targetKind: ScheduledActionTargetKind | null
  targetId: number | null
  onActionChange: (k: string | null) => void
  onTargetChange: (
    kind: ScheduledActionTargetKind | null,
    id: number | null,
  ) => void
  actionLocked: boolean
  targetLocked: boolean
}) {
  const action = actions.find((a) => a.key === actionKey) ?? null
  const builtin = actions.filter((a) => a.key.startsWith("_builtin."))
  const packs = actions.filter((a) => !a.key.startsWith("_builtin."))

  return (
    <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="space-y-2">
        <Label className="text-sm font-medium text-slate-200">Action</Label>
        <select
          value={actionKey ?? ""}
          disabled={actionLocked}
          onChange={(e) => onActionChange(e.target.value || null)}
          className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white disabled:opacity-60"
          data-testid="action-picker"
        >
          <option value="">Select an action…</option>
          {builtin.length > 0 && (
            <optgroup label="Built-in">
              {builtin.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.name}
                </option>
              ))}
            </optgroup>
          )}
          {packs.length > 0 && (
            <optgroup label="Pack-supplied">
              {packs.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.name} — {a.pack_name}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        {action && (
          <p className="text-xs text-slate-500">{action.description}</p>
        )}
      </div>

      <div className="space-y-2">
        <Label className="text-sm font-medium text-slate-200">Target</Label>
        <div className="flex gap-3 text-sm">
          {(["host", "group", "fleet"] as const).map((kind) => {
            const disabled =
              targetLocked ||
              (kind === "fleet" && (action ? !action.supports_fleet : false)) ||
              (kind === "group" && (action ? !action.supports_group : false)) ||
              (kind === "host" && (action ? !action.supports_host : false))
            return (
              <label
                key={kind}
                className={`flex items-center gap-1.5 cursor-pointer ${
                  disabled ? "opacity-40 cursor-not-allowed" : ""
                }`}
                title={disabled ? "Action does not support this target kind" : ""}
              >
                <input
                  type="radio"
                  name="target-kind"
                  checked={targetKind === kind}
                  disabled={disabled}
                  onChange={() => onTargetChange(kind, null)}
                  data-testid={`target-${kind}`}
                />
                <span className="text-slate-300 capitalize">{kind}</span>
              </label>
            )
          })}
        </div>

        {targetKind === "host" && (
          <HostCombobox
            value={targetId}
            onChange={(id) => onTargetChange("host", id)}
            disabled={targetLocked}
          />
        )}

        {targetKind === "group" && (
          <select
            value={targetId ?? ""}
            disabled={targetLocked}
            onChange={(e) =>
              onTargetChange(
                "group",
                e.target.value ? Number(e.target.value) : null,
              )
            }
            className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white disabled:opacity-60"
          >
            <option value="">Select a group…</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        )}

        {targetKind === "fleet" && (
          <p className="text-xs text-amber-400">
            This will run against every host in the inventory.
          </p>
        )}
      </div>
    </div>
  )
}

function ActionPickerSummary({
  action,
  state,
  groups,
}: {
  action: ActionDefinition
  state: State
  groups: HostGroup[]
}) {
  let targetLabel = "Fleet (all hosts)"
  if (state.targetKind === "host") {
    targetLabel = `Host #${state.targetId}`
  } else if (state.targetKind === "group") {
    const g = groups.find((x) => x.id === state.targetId)
    targetLabel = g ? `Group: ${g.name}` : `Group #${state.targetId}`
  }
  return (
    <div className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-400">
      <span className="text-slate-200">{action.name}</span>
      {" → "}
      <span className="text-slate-300">{targetLabel}</span>
    </div>
  )
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-200 font-mono break-all">{value}</span>
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-slate-600"
      />
      <span className="text-sm text-slate-300">{label}</span>
    </label>
  )
}

// ---------------------------------------------------------------------------
// Step navigation helpers
// ---------------------------------------------------------------------------

function nextStep(step: ScheduleStep): ScheduleStep {
  const order: ScheduleStep[] = ["picker", "parameters", "schedule", "review"]
  const i = order.indexOf(step)
  return order[Math.min(i + 1, order.length - 1)]
}

function prevStep(step: ScheduleStep): ScheduleStep {
  const order: ScheduleStep[] = ["picker", "parameters", "schedule", "review"]
  const i = order.indexOf(step)
  return order[Math.max(i - 1, 0)]
}

function getInitialStep(state: State, isEdit: boolean): ScheduleStep {
  if (isEdit) return "parameters"
  if (state.actionKey && state.targetKind) return "parameters"
  return "picker"
}
