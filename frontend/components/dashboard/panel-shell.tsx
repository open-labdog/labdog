import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** Shared body height token for every dashboard panel — chart canvases,
 *  the activity feed's scroll area, and the scheduled-runs scroll area
 *  must all use exactly this value, in every state (loading / error /
 *  empty / populated), so panel outer heights match by construction. */
export const PANEL_BODY_HEIGHT = "h-[240px]"

export function PanelShell({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("h-full flex flex-col rounded-lg border border-slate-700 bg-slate-900 p-4", className)}>
      {children}
    </div>
  )
}

export function PanelHeader({ title, meta, action }: { title: string; meta?: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <span className="text-sm font-semibold text-white">{title}</span>
      {action ?? (meta && <span className="text-xs text-slate-400">{meta}</span>)}
    </div>
  )
}

/** Fixed-height (h-4) single-line caption slot, ALWAYS rendered by every
 *  panel — even when empty — so a panel that sometimes shows a caveat
 *  (DriftTrendChart's "no drift", SyncSuccessChart's "only one data
 *  point") never changes total height relative to a panel that never
 *  shows one. Pass `undefined`/`null` children to render it blank. */
export function PanelFootnote({ children }: { children?: ReactNode }) {
  return <div className="mb-3 h-4 text-xs leading-4">{children}</div>
}
