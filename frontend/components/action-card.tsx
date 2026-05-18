"use client"

import Link from "next/link"
import { AlertTriangle, ArrowUpFromLine, CalendarClock, Layers, Network, Play, Zap } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip } from "@/components/ui/tooltip"
import { PackBadge } from "@/components/pack-badge"
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
  onSchedule?: (action: ActionDefinition) => void
  lastRun?: { status: string; started_at: string } | null
}

export function ActionCard({ action, onRun, onSchedule, lastRun }: ActionCardProps) {
  const Icon = ICON_MAP[action.icon] ?? Zap
  const unresolved = action.unresolved
  return (
    <div
      className={`flex flex-col gap-3 rounded-lg border p-4 ${
        unresolved
          ? "border-amber-800 bg-amber-950/20"
          : "border-slate-700 bg-slate-800/50"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-700">
          <Icon className="h-5 w-5 text-slate-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-white">{action.name}</span>
            <PackBadge action={action} />
            {unresolved && (
              <span
                title="Multiple packs declare this action key. Pick a winner on /action-packs before running."
                className="inline-flex items-center gap-1 rounded border border-amber-700/60 bg-amber-950/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-300"
              >
                <AlertTriangle className="h-2.5 w-2.5" />
                Unresolved
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-400">{action.description}</p>
          {unresolved ? (
            <p className="mt-1 text-xs text-amber-300">
              Pick a winning pack on{" "}
              <Link href="/action-packs" className="underline hover:text-amber-200">
                /action-packs
              </Link>{" "}
              to enable this action.
            </p>
          ) : (
            <p className="mt-1 text-xs text-slate-500">~{action.estimated_duration}</p>
          )}
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
        <div className="flex items-center gap-2">
          {onSchedule && (
            <Tooltip content="Add a recurring schedule for this action">
              <Button
                size="sm"
                variant="outline"
                onClick={() => onSchedule(action)}
                className="gap-1.5"
                disabled={unresolved}
                data-testid="schedule-action-button"
              >
                <CalendarClock className="h-3 w-3" />
                Schedule…
              </Button>
            </Tooltip>
          )}
          <Tooltip
            content={
              unresolved
                ? "Pick a winning pack first"
                : "Run this action"
            }
          >
            <Button
              size="sm"
              onClick={() => onRun(action)}
              disabled={unresolved}
              className="gap-1.5"
            >
              <Play className="h-3 w-3" />
              Run
            </Button>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}
