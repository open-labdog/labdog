"use client"

import type { ActionDefinition } from "@/lib/types"

/**
 * Provenance hint for an action: shows which pack supplied it and (if
 * relevant) how many lower-priority packs it overrides. Built-ins live
 * in the synthetic ``_builtin`` pack; for them we render a slightly
 * different pill so operators can spot at a glance that the action is
 * code-driven, not playbook-driven.
 */
export function PackBadge({ action }: { action: ActionDefinition }) {
  const isBuiltin = action.key.startsWith("_builtin.") || action.pack_name === "_builtin"
  const overridden = action.overridden_from ?? []
  const hasOverride = overridden.length > 0

  if (isBuiltin) {
    return (
      <span
        title="Built-in action — wired into LabDog's internals, no Ansible playbook on disk"
        className="inline-flex items-center rounded border border-blue-700/60 bg-blue-950/40 px-1.5 py-0.5 text-[10px] font-medium text-blue-300"
      >
        built-in
      </span>
    )
  }

  const tooltip = hasOverride
    ? `Loaded from pack "${action.pack_name}". Overrides: ${overridden.join(", ")}`
    : `Loaded from pack "${action.pack_name}"`
  const classes = hasOverride
    ? "border-amber-700/60 bg-amber-950/40 text-amber-300"
    : "border-slate-700 bg-slate-900 text-slate-400"
  const label = hasOverride
    ? `from ${action.pack_name} (overrides ${overridden.length})`
    : `from ${action.pack_name}`
  return (
    <span
      title={tooltip}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${classes}`}
    >
      {label}
    </span>
  )
}
