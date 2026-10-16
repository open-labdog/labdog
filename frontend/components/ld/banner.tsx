"use client"

import type { ReactNode } from "react"
import { Dot } from "./atoms"
import { toneSoft, toneVar, type Tone } from "./tone"

/* ── strips ─────────────────────────────────────────────────────── */

/**
 * A tinted strip with a dot, a sentence and an optional action on the
 * right — the design's way of saying "something about this page needs
 * a word": nothing is managed until approved, drift checking is off,
 * this module is imported from Git, no provider is enabled. `flush`
 * makes it a full-width band under the page head (bottom border in the
 * tone); the default is a boxed strip inside a body.
 */
export function Banner({
  tone = "idle",
  children,
  action,
  flush,
  pulse,
  role,
  className,
}: {
  tone?: Tone
  children: ReactNode
  action?: ReactNode
  flush?: boolean
  pulse?: boolean
  /** A danger banner is an alert by default; `null` drops the role for a
   *  banner that sits inside a live region of its own. */
  role?: "alert" | "status" | null
  className?: string
}) {
  return (
    <div
      className={`flex shrink-0 flex-wrap items-center gap-[9px] px-3.5 py-[9px] text-[11.5px] leading-[1.45] text-text ${
        flush ? "border-b" : "rounded-r border"
      } ${className ?? ""}`}
      style={{ background: toneSoft(tone), borderColor: toneVar(tone) }}
      role={role === undefined ? (tone === "danger" ? "alert" : undefined) : (role ?? undefined)}
    >
      <Dot tone={tone} pulse={pulse} />
      <span className="min-w-0 flex-1">{children}</span>
      {action && <div className="ml-auto flex flex-wrap items-center gap-[7px]">{action}</div>}
    </div>
  )
}

/**
 * The header strip of an embedded editor or a tab body: a `.tt` caption
 * on the left ("12 rules declared here"), the module's small controls
 * beside it, actions on the right.
 */
export function Toolbar({ children, actions, className }: { children?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={`flex shrink-0 flex-wrap items-center gap-2.5 border-b border-line bg-surface-2 px-[13px] py-[9px] ${className ?? ""}`}>
      {children}
      {actions && <div className="ml-auto flex flex-wrap items-center gap-[7px]">{actions}</div>}
    </div>
  )
}

/**
 * The selection bar under a table: how many are selected, clear, and
 * the bulk actions on the right. Renders nothing at zero so a screen
 * can leave it in place unconditionally.
 */
export function BulkBar({
  n,
  onClear,
  status,
  children,
  noun = "selected",
}: {
  n: number
  onClear: () => void
  /** Progress text beside the count while a bulk operation runs. */
  status?: ReactNode
  children?: ReactNode
  noun?: string
}) {
  if (n === 0) return null
  return (
    <div className="fade flex shrink-0 flex-wrap items-center gap-[9px] border-t border-ld-accent-line bg-ld-accent-soft px-3.5 py-[9px]">
      <span className="mono num text-xs font-semibold">
        {n} {noun}
      </span>
      <button type="button" className="btn btn-sm btn-ghost" onClick={onClear}>
        clear
      </button>
      {status && <span className="text-xs text-text-2">{status}</span>}
      <div className="ml-auto flex flex-wrap gap-[7px]">{children}</div>
    </div>
  )
}
