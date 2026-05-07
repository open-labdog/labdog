"use client"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { ActionDefinition, ActionParameter } from "@/lib/types"

export interface ActionParameterFormProps {
  action: ActionDefinition
  values: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  /** Optional: per-parameter placeholder overrides (e.g. host-OS-aware
   *  defaults in the run-now dialog). The schedule dialog skips this —
   *  a schedule firing weeks later doesn't know which host's codename
   *  applies. */
  placeholderFor?: (param: ActionParameter) => string | undefined
}

export function ActionParameterForm({
  action,
  values,
  onChange,
  placeholderFor,
}: ActionParameterFormProps) {
  if (action.parameters.length === 0) return null

  function set(key: string, val: unknown) {
    onChange({ ...values, [key]: val })
  }

  return (
    <div className="space-y-4 py-2">
      {action.parameters.map((p) => {
        const placeholder = placeholderFor?.(p)
        return (
          <div key={p.key} className="space-y-1.5">
            <Label className="text-sm font-medium text-slate-200">
              {p.label}
              {p.required && <span className="text-red-400 ml-1">*</span>}
            </Label>

            {p.type === "bool" ? (
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
