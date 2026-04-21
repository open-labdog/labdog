"use client"

import { ArrowUpFromLine, Layers, Network, Play, Zap } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
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
            {action.destructive && (
              <Badge className="bg-red-900/60 text-red-300 border-red-700 text-xs">Destructive</Badge>
            )}
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
