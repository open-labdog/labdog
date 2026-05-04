"use client"

import { ArrowUpFromLine, Layers, Network, Play, Zap } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { ActionDefinition } from "@/lib/types"

const ICON_MAP: Record<string, LucideIcon> = {
  ArrowUpFromLine,
  Layers,
  Network,
  Play,
  Zap,
}

interface ActionCardProps {
  action: ActionDefinition
  onRun: (action: ActionDefinition) => void
  lastRun?: { status: string; started_at: string } | null
}

function PackBadge({ action }: { action: ActionDefinition }) {
  const overridden = action.overridden_from ?? []
  const hasOverride = overridden.length > 0
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

export function ActionCard({ action, onRun, lastRun }: ActionCardProps) {
  const Icon = ICON_MAP[action.icon] ?? Zap
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-slate-700 bg-slate-800/50 p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-700">
          <Icon className="h-5 w-5 text-slate-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-white">{action.name}</span>
            <PackBadge action={action} />
          </div>
          <p className="mt-1 text-xs text-slate-400">{action.description}</p>
          <p className="mt-1 text-xs text-slate-500">~{action.estimated_duration}</p>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2">
        {lastRun ? (
          <span className="text-xs text-slate-500">
            Last run: {new Date(lastRun.started_at).toLocaleDateString()}
          </span>
        ) : (
          <span className="text-xs text-slate-600">Never run</span>
        )}
        <Button size="sm" onClick={() => onRun(action)} className="gap-1.5">
          <Play className="h-3 w-3" />
          Run
        </Button>
      </div>
    </div>
  )
}
