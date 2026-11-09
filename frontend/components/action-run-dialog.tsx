"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Banner, Field, Modal } from "@/components/ld"
import { ActionParameterForm } from "@/components/action-parameter-form"
import { apiFetch } from "@/lib/api"
import { nextCodename } from "@/lib/os-upgrade-paths"
import { useActionTray } from "@/lib/action-tray"
import { toast } from "sonner"
import type {
  ActionDefinition,
  ActionRun,
  GrafanaInstance,
  GrafanaKind,
  Host,
} from "@/lib/types"

interface ActionRunDialogProps {
  action: ActionDefinition | null
  scope: "host" | "group"
  targetId: number
  open: boolean
  onClose: () => void
  hostOsCodename?: string | null
  /**
   * When opened from a target rather than from an action ("Run action…"
   * on a host or group), the dialog also picks the action: pass every
   * action that supports the target and take the choice back here.
   */
  actions?: ActionDefinition[]
  onPickAction?: (action: ActionDefinition) => void
  /** Shown under the title — the target's name, so the dialog says what it will run against. */
  targetLabel?: string
  /**
   * Re-run: start the form from a past run's settings instead of the
   * manifest defaults. Every field stays editable. Read once, when the
   * dialog mounts, so mount it only while it is open.
   */
  rerunOf?: ActionRun
}

/** Keys the API adds to a run's stored parameters; never manifest fields. */
const INTERNAL_PREFIX = "__"
const DRY_RUN_PARAM = "__dry_run"
const METRICS_DESTINATIONS_PARAM = "__metrics_destinations"
const PARALLELISM_OPTIONS: { value: number; label: string }[] = [
  { value: -1, label: "All at once" },
  { value: 1, label: "Rolling — 1 at a time" },
  { value: 2, label: "Rolling — 2 at a time" },
  { value: 5, label: "Rolling — 5 at a time" },
]

/**
 * A past run's parameters as the form's values: only the keys the
 * action's manifest still declares (the API rejects any other), and the
 * metrics destinations it picked put back on their pickers as ids.
 */
export function formValuesFromRun(action: ActionDefinition, run: ActionRun): { values: Record<string, unknown>; dropped: string[] } {
  const declared = new Set(action.parameters.map((p) => p.key))
  const values: Record<string, unknown> = {}
  const dropped: string[] = []
  for (const [k, v] of Object.entries(run.parameters ?? {})) {
    if (k.startsWith(INTERNAL_PREFIX)) continue
    if (declared.has(k)) values[k] = v
    else dropped.push(k)
  }
  const mb = action.metrics_backend
  const dest = run.parameters?.[METRICS_DESTINATIONS_PARAM] as Partial<Record<GrafanaKind, number>> | undefined
  if (mb && dest) {
    if (mb.prometheus_push_var && dest.mimir !== undefined) values[mb.prometheus_push_var] = String(dest.mimir)
    if (mb.loki_push_var && dest.loki !== undefined) values[mb.loki_push_var] = String(dest.loki)
  } else if (mb) {
    // No explicit pick: the run went to each host's own destination,
    // which is the picker's "" — not the URL the run log recorded.
    if (mb.prometheus_push_var) delete values[mb.prometheus_push_var]
    if (mb.loki_push_var) delete values[mb.loki_push_var]
  }
  return { values, dropped }
}

const NO_PARAMS: Record<string, unknown> = {}

export function ActionRunDialog({ action, scope, targetId, open, onClose, hostOsCodename, actions, onPickAction, targetLabel, rerunOf }: ActionRunDialogProps) {
  const router = useRouter()
  const { trackRun } = useActionTray()
  const rerun = useMemo(() => (action && rerunOf ? formValuesFromRun(action, rerunOf) : null), [action, rerunOf])
  // Parameters are kept per action key so switching the picked action
  // never carries one playbook's values into another's form.
  const [paramsByAction, setParamsByAction] = useState<Record<string, Record<string, unknown>>>(() =>
    action && rerun ? { [action.key]: rerun.values } : {},
  )
  const params = (action && paramsByAction[action.key]) ?? NO_PARAMS
  const setParams = (next: Record<string, unknown>) => {
    if (action) setParamsByAction((prev) => ({ ...prev, [action.key]: next }))
  }
  const [parallelism, setParallelism] = useState(() => rerunOf?.parallelism ?? 1)
  const parallelismOptions = PARALLELISM_OPTIONS.some((o) => o.value === parallelism)
    ? PARALLELISM_OPTIONS
    : [...PARALLELISM_OPTIONS, { value: parallelism, label: `Rolling — ${parallelism} at a time` }]
  const [submitting, setSubmitting] = useState(false)

  // Fetch group hosts to show OS codename context for linux-os-upgrade
  const { data: groupHosts } = useQuery<Host[]>({
    queryKey: ["group-hosts", targetId],
    queryFn: () => apiFetch<Host[]>(`/api/groups/${targetId}/hosts`),
    enabled: scope === "group" && open && action?.key === "linux-os-upgrade",
    staleTime: 30_000,
  })

  const uniqueCodenames = [...new Set(
    (groupHosts ?? []).map((h) => h.os_codename).filter((c): c is string => Boolean(c))
  )]
  const groupSingleCodename = uniqueCodenames.length === 1 ? uniqueCodenames[0] : null
  const groupMixedCodenames = uniqueCodenames.length > 1
  const groupCodenameCounts = uniqueCodenames.map((c) => ({
    codename: c,
    count: (groupHosts ?? []).filter((h) => h.os_codename === c).length,
  }))

  // Metrics-backend actions (e.g. alloy-install) render their URL params as
  // registered-Grafana-instance pickers instead of free text. Map each
  // mapped playbook var to the instance kind it should list.
  const instancePickers = useMemo<Record<string, GrafanaKind>>(() => {
    const mb = action?.metrics_backend
    if (!mb) return {}
    const m: Record<string, GrafanaKind> = {}
    if (mb.prometheus_push_var) m[mb.prometheus_push_var] = "mimir"
    if (mb.loki_push_var) m[mb.loki_push_var] = "loki"
    return m
  }, [action])
  const hasPickers = Object.keys(instancePickers).length > 0

  const { data: grafanaInstances } = useQuery<GrafanaInstance[]>({
    queryKey: ["grafana-instances"],
    queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"),
    enabled: open && hasPickers,
    staleTime: 30_000,
  })

  // Pickers start on "each host's assigned destination" (empty value):
  // the backend resolves every target host to its own Mimir / Loki
  // (host, then highest-priority group, then default). Picking a specific
  // instance overrides that for every host in this run.

  // Block running when a required picker has no registered instance.
  const missingDestination =
    hasPickers &&
    grafanaInstances !== undefined &&
    Object.values(instancePickers).some(
      (kind) => !grafanaInstances.some((i) => i.kind === kind),
    )

  if (!action) return null

  async function handleSubmit(dryRun = false) {
    if (!action) return
    setSubmitting(true)
    try {
      // Build params with defaults for unset bool fields
      const currentForPrepop = scope === "host" ? hostOsCodename : groupSingleCodename
      const resolvedParams: Record<string, unknown> = {}
      const metricsDestinations: Record<string, number> = {}
      for (const p of action.parameters) {
        const pickerKind = instancePickers[p.key]
        if (pickerKind) {
          // Picker value is an instance id, or "" for per-host routing.
          // Only an explicit pick becomes a URL param (for older backends
          // and the run log) plus an id the backend trusts over the URL.
          const picked = (grafanaInstances ?? []).find((i) => String(i.id) === String(params[p.key] ?? ""))
          if (picked) {
            resolvedParams[p.key] = picked.url
            metricsDestinations[pickerKind] = picked.id
          }
          continue
        }
        const val = params[p.key]
        if (val !== undefined) {
          resolvedParams[p.key] = val
        } else {
          // Pre-populate for linux-os-upgrade: current_version from host/group
          // codename; next_version derived from the static upgrade-path map.
          let prePopulated: string | undefined
          if (action.key === "linux-os-upgrade" && p.key === "current_version") {
            prePopulated = currentForPrepop ?? undefined
          } else if (action.key === "linux-os-upgrade" && p.key === "next_version") {
            prePopulated = nextCodename(currentForPrepop)
          }
          if (prePopulated !== undefined) {
            resolvedParams[p.key] = prePopulated
          } else if (p.default !== null && p.default !== undefined) {
            resolvedParams[p.key] = p.default
          }
        }
        if (p.required && resolvedParams[p.key] === undefined) {
          toast.error(`${p.label} is required`)
          setSubmitting(false)
          return
        }
      }
      // `parameters` carries only what the manifest declares. The API
      // validates it against that schema and rejects anything else, so a
      // `__dry_run` key smuggled in here failed every preview with
      // "Extra inputs are not permitted". `dry_run` below is the real
      // channel; the API puts it where the Celery task reads it.
      const body: Record<string, unknown> = {
        action_key: action.key,
        parameters: resolvedParams,
        dry_run: dryRun,
      }
      if (Object.keys(metricsDestinations).length > 0) {
        body.metrics_destinations = metricsDestinations
      }
      if (scope === "host") {
        body.host_id = targetId
      } else {
        body.group_id = targetId
        body.parallelism = parallelism
      }

      const run = await apiFetch<ActionRun>("/api/actions/runs", {
        method: "POST",
        json: body,
      })
      trackRun(run)
      toast.success("Action started")
      onClose()
      // Navigate to run page
      const base = scope === "host" ? `/hosts/${targetId}` : `/groups/${targetId}`
      router.push(`${base}/actions/runs/${run.id}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start action"
      toast.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const blocked = submitting || !!action.unresolved || missingDestination
  const current = scope === "host" ? hostOsCodename : groupSingleCodename

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={rerunOf ? `Re-run ${action.name}` : action.name}
      meta={targetLabel ? `${scope}: ${targetLabel}` : scope}
      w={440}
      footer={
        <>
          <button type="button" className="btn ml-auto" disabled={blocked} onClick={() => handleSubmit(true)}>
            Preview (dry-run)
          </button>
          <button type="button" className="btn btn-primary" disabled={blocked} onClick={() => handleSubmit(false)}>
            {submitting ? "Starting…" : "Run"}
          </button>
        </>
      }
    >
      {rerunOf && (
        <div className="text-[11.5px] leading-[1.5] text-text-2" data-testid="rerun-note">
          Same target and settings as run{" "}
          <span className="mono text-text">#{rerunOf.id}</span>
          {rerunOf.parameters?.[DRY_RUN_PARAM] ? " (a dry-run preview)" : ""}. Change anything below before running.
        </div>
      )}

      {rerunOf && (rerunOf.action_version !== action.version || (rerun?.dropped.length ?? 0) > 0) && (
        <Banner tone="warn">
          {rerunOf.action_version !== action.version
            ? `The action changed since that run (v${rerunOf.action_version} → v${action.version}).`
            : "The action's parameters changed since that run."}
          {rerun && rerun.dropped.length > 0 && (
            <> It no longer takes <span className="mono">{rerun.dropped.join(", ")}</span>, so {rerun.dropped.length > 1 ? "those were" : "that was"} left out.</>
          )}
        </Banner>
      )}

      {actions && actions.length > 1 && onPickAction && (
        <Field label="action" htmlFor="run-action-pick">
          <select
            id="run-action-pick"
            className="inp mono"
            value={action.key}
            onChange={(e) => {
              const next = actions.find((a) => a.key === e.target.value)
              if (next) onPickAction(next)
            }}
          >
            {actions.map((a) => (
              <option key={a.key} value={a.key}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      <div className="text-[11.5px] text-text-3">
        {action.pack_name} pack{action.description ? ` · ${action.description}` : ""}
      </div>

      {action.unresolved && (
        <Banner
          tone="warn"
          action={
            <Link href="/actions?tab=packs" className="btn btn-sm hover:no-underline" onClick={onClose}>
              packs →
            </Link>
          }
        >
          Unresolved — several packs declare <span className="mono">{action.key}</span>; pick the winning pack before running.
        </Banner>
      )}

      {scope === "group" && action.key === "linux-os-upgrade" && (
        groupMixedCodenames ? (
          <Banner tone="warn">
            Mixed OS versions: {groupCodenameCounts.map(({ codename, count }) => `${codename} (${count})`).join(", ")}. Check <em>current codename</em> before running.
          </Banner>
        ) : groupSingleCodename ? (
          <div className="text-[11.5px] text-text-2">
            All hosts are on <span className="mono text-text">{groupSingleCodename}</span>.
          </div>
        ) : groupHosts !== undefined ? (
          <div className="text-[11.5px] italic text-text-3">OS facts not yet collected for hosts in this group.</div>
        ) : null
      )}

      <ActionParameterForm
        action={action}
        values={params}
        onChange={setParams}
        placeholderFor={(p) => {
          if (action.key === "linux-os-upgrade" && p.key === "current_version") return current ?? undefined
          if (action.key === "linux-os-upgrade" && p.key === "next_version") return nextCodename(current)
          return undefined
        }}
        instancePickers={instancePickers}
        grafanaInstances={grafanaInstances}
      />

      {scope === "group" && (
        <Field label="parallelism" htmlFor="run-parallelism">
          <select id="run-parallelism" className="inp" value={parallelism} onChange={(e) => setParallelism(Number(e.target.value))}>
            {parallelismOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
      )}

      <div className="text-[11.5px] leading-[1.5] text-text-2">
        Preview runs the same playbook as a dry-run — nothing on any host changes until you review it and click Run.
      </div>
    </Modal>
  )
}
