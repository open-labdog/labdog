"use client"

import Link from "next/link"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { ActionDefinition, ActionParameter, GrafanaInstance, GrafanaKind } from "@/lib/types"

export interface ActionParameterFormProps {
  action: ActionDefinition
  values: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  /** Optional: per-parameter placeholder overrides (e.g. host-OS-aware
   *  defaults in the run-now dialog). The schedule dialog skips this —
   *  a schedule firing weeks later doesn't know which host's codename
   *  applies. */
  placeholderFor?: (param: ActionParameter) => string | undefined
  /** Param keys that should render as a registered-Grafana-instance picker
   *  (value = the chosen instance's URL) instead of a text input, keyed to
   *  the instance kind to list. Driven by the action's metrics_backend. */
  instancePickers?: Record<string, GrafanaKind>
  /** Registered Grafana instances, used to populate the pickers above. */
  grafanaInstances?: GrafanaInstance[]
}

export function ActionParameterForm({
  action,
  values,
  onChange,
  placeholderFor,
  instancePickers,
  grafanaInstances,
}: ActionParameterFormProps) {
  if (action.parameters.length === 0) return null

  function set(key: string, val: unknown) {
    onChange({ ...values, [key]: val })
  }

  return (
    <div className="space-y-4 py-2">
      {action.parameters.map((p) => {
        const placeholder = placeholderFor?.(p)
        const pickerKind = instancePickers?.[p.key]
        const pickerOptions = pickerKind
          ? (grafanaInstances ?? []).filter((i) => i.kind === pickerKind)
          : []
        return (
          <div key={p.key} className="space-y-1.5">
            <Label className="text-sm font-medium text-slate-200">
              {p.label}
              {p.required && <span className="text-red-400 ml-1">*</span>}
            </Label>

            {pickerKind ? (
              pickerOptions.length === 0 ? (
                <p className="text-sm text-amber-400">
                  No {pickerKind} destination configured.{" "}
                  <Link href="/grafana" className="text-sky-400 underline hover:text-sky-300">
                    Set one up under Integrations → Grafana
                  </Link>
                  .
                </p>
              ) : (
                <select
                  value={String(values[p.key] ?? "")}
                  onChange={(e) => set(p.key, e.target.value)}
                  className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white"
                >
                  {pickerOptions.map((i) => (
                    <option key={i.id} value={i.url}>
                      {i.name} — {i.url}
                    </option>
                  ))}
                </select>
              )
            ) : p.type === "bool" ? (
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id={p.key}
                  checked={
                    values[p.key] !== undefined
                      ? Boolean(values[p.key])
                      : Boolean(p.default)
                  }
                  onChange={(e) => set(p.key, e.target.checked)}
                  className="h-4 w-4 rounded border-slate-600"
                />
                {p.help_text && (
                  <label htmlFor={p.key} className="text-sm text-slate-400">
                    {p.help_text}
                  </label>
                )}
              </div>
            ) : p.type === "choice" && p.choices ? (
              <select
                value={String(values[p.key] ?? p.default ?? "")}
                onChange={(e) => set(p.key, e.target.value)}
                className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white"
              >
                {p.choices.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                type={p.type === "int" ? "number" : "text"}
                placeholder={placeholder ?? String(p.default ?? "")}
                value={
                  values[p.key] !== undefined
                    ? String(values[p.key])
                    : (placeholder ?? "")
                }
                onChange={(e) =>
                  set(
                    p.key,
                    p.type === "int" ? Number(e.target.value) : e.target.value,
                  )
                }
              />
            )}

            {p.help_text && p.type !== "bool" && (
              <p className="text-xs text-slate-500">{p.help_text}</p>
            )}
          </div>
        )
      })}
    </div>
  )
}
