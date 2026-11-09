"use client"

import Link from "next/link"
import { Field } from "@/components/ld"
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
   *  instead of a text input, keyed to the instance kind to list. Driven by
   *  the action's metrics_backend. The value is the chosen instance's id as
   *  a string, or "" for "each host's assigned destination"; the caller
   *  turns it into the URL param and `metrics_destinations` on submit. */
  instancePickers?: Record<string, GrafanaKind>
  /** Registered Grafana instances, used to populate the pickers above. */
  grafanaInstances?: GrafanaInstance[]
}

/**
 * One `Field` per manifest parameter: a picker for metrics destinations,
 * a checkbox for booleans (its help text is the label), a select for
 * choices, otherwise a text or number input. The help text is the
 * field's hint so it reads in the caption line, not as a paragraph.
 */
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
    // `undefined` removes the key, so the run falls back to the manifest
    // default exactly as for a field nobody touched (BUG-125).
    const next = { ...values }
    if (val === undefined) delete next[key]
    else next[key] = val
    onChange(next)
  }

  return (
    <div className="flex flex-col gap-[11px]">
      {action.parameters.map((p) => {
        const placeholder = placeholderFor?.(p)
        const pickerKind = instancePickers?.[p.key]
        const pickerOptions = pickerKind ? (grafanaInstances ?? []).filter((i) => i.kind === pickerKind) : []
        const label = p.required ? `${p.label} *` : p.label
        const id = `param-${p.key}`

        if (p.type === "bool" && !pickerKind) {
          return (
            <label key={p.key} className="flex items-center gap-2 text-xs text-text">
              <input
                type="checkbox"
                id={id}
                checked={values[p.key] !== undefined ? Boolean(values[p.key]) : Boolean(p.default)}
                onChange={(e) => set(p.key, e.target.checked)}
              />
              <span>{p.label}</span>
              {p.help_text && <span className="text-text-3">— {p.help_text}</span>}
            </label>
          )
        }

        return (
          <Field key={p.key} label={label} hint={p.help_text ?? undefined} htmlFor={id}>
            {pickerKind ? (
              pickerOptions.length === 0 ? (
                <span className="text-[11.5px] text-warn">
                  No {pickerKind} destination configured.{" "}
                  <Link href="/grafana" className="underline">
                    Set one up under Settings › Integrations
                  </Link>
                  .
                </span>
              ) : (
                <select id={id} className="inp mono" value={String(values[p.key] ?? "")} onChange={(e) => set(p.key, e.target.value)}>
                  <option value="">Each host&apos;s assigned destination</option>
                  {pickerOptions.map((i) => (
                    <option key={i.id} value={String(i.id)}>
                      {i.name} — {i.url}
                    </option>
                  ))}
                </select>
              )
            ) : p.type === "choice" && p.choices ? (
              <select id={id} className="inp mono" value={String(values[p.key] ?? p.default ?? "")} onChange={(e) => set(p.key, e.target.value)}>
                {p.choices.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                className="inp mono"
                type={p.type === "int" ? "number" : "text"}
                placeholder={placeholder ?? String(p.default ?? "")}
                value={values[p.key] !== undefined ? String(values[p.key]) : (placeholder ?? "")}
                onChange={(e) => {
                  // An emptied field is unset, not "" (or 0 for an int,
                  // which Number("") is): the default it showed is what runs.
                  const raw = e.target.value
                  set(p.key, raw === "" ? undefined : p.type === "int" ? Number(raw) : raw)
                }}
              />
            )}
          </Field>
        )
      })}
    </div>
  )
}
