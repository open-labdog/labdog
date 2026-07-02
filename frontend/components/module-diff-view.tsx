"use client"

import { useState } from "react"
import type { DiffChange, DiffOp, ModuleDiff } from "@/lib/types"

// Human-readable labels for canonical module names (see backend
// CANONICAL_ORDER). Falls back to the raw name for anything unmapped.
const MODULE_LABELS: Record<string, string> = {
  firewall: "Firewall",
  services: "Services",
  packages: "Packages",
  "hosts-file": "/etc/hosts",
  cron: "Cron Jobs",
  "linux-users": "Linux Users",
  resolver: "DNS Resolver",
}

export function moduleLabel(module: string): string {
  return MODULE_LABELS[module] ?? module
}

function countByOp(changes: DiffChange[], op: DiffOp): number {
  return changes.reduce((n, c) => (c.op === op ? n + 1 : n), 0)
}

// Marker glyph + color per diff op. Unchanged lines carry no background and a
// brighter-than-before text tone so they stay legible on the slate-900 card.
const DIFF_OP_STYLES: Record<DiffOp, { marker: string; className: string }> = {
  add: { marker: "+", className: "text-green-400 bg-green-950/30" },
  remove: { marker: "-", className: "text-red-400 bg-red-950/30" },
  update: { marker: "~", className: "text-amber-400 bg-amber-950/30" },
  unchanged: { marker: "", className: "text-slate-300" },
}

function DiffChangeLine({ change }: { change: DiffChange }) {
  const { marker, className } = DIFF_OP_STYLES[change.op]
  // Flex row so the marker stays in a fixed gutter and long, unbreakable tokens
  // (CIDRs, FQDNs) wrap with a hanging indent instead of sliding under the marker.
  return (
    <div className={`flex gap-1.5 font-mono text-xs px-3 py-1 rounded ${className}`}>
      <span className="select-none shrink-0 w-3 text-center opacity-70">{marker}</span>
      <span className="min-w-0 break-words whitespace-pre-wrap">{change.summary}</span>
    </div>
  )
}

/** Inline counts/badge summary for a module's changes (e.g. "+2 -1 ~3"). */
export function DiffSummary({ diff }: { diff: ModuleDiff }) {
  if (diff.error) {
    return <span className="text-xs text-red-400">error</span>
  }
  if (!diff.has_changes) {
    return <span className="text-xs text-slate-500">no changes</span>
  }
  const add = countByOp(diff.changes, "add")
  const remove = countByOp(diff.changes, "remove")
  const update = countByOp(diff.changes, "update")
  return (
    <span className="flex items-center gap-2">
      {add > 0 && <span className="text-xs text-green-400">+{add}</span>}
      {remove > 0 && <span className="text-xs text-red-400">-{remove}</span>}
      {update > 0 && <span className="text-xs text-amber-400">~{update}</span>}
    </span>
  )
}

/**
 * Collapsible card rendering a single module's normalized diff in the
 * firewall-preview visual style (green add / red remove / amber update /
 * gray unchanged). Used for both per-module sync previews and inside the
 * "Sync All" modal.
 */
export function ModuleDiffView({
  diff,
  defaultExpanded = true,
  showHeader = true,
}: {
  diff: ModuleDiff
  defaultExpanded?: boolean
  showHeader?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [showUnchanged, setShowUnchanged] = useState(false)
  const changed = diff.changes.filter((c) => c.op !== "unchanged")
  const unchanged = diff.changes.filter((c) => c.op === "unchanged")

  // Only the collapsible Sync-All cards cap their own height; the standalone
  // per-module preview (no header) lets the dialog's own scroll region govern,
  // avoiding a scrollbar-inside-a-scrollbar.
  const body = (
    <div className={`space-y-1 ${showHeader ? "max-h-64 overflow-y-auto" : ""}`}>
      {diff.error ? (
        <div className="text-red-400 text-xs px-3 py-2">{diff.error}</div>
      ) : diff.changes.length === 0 ? (
        <div className="text-slate-500 text-xs px-3 py-2">Nothing configured for this module</div>
      ) : (
        <>
          {changed.map((c, i) => (
            <DiffChangeLine key={`c${i}`} change={c} />
          ))}
          {unchanged.length > 0 && (
            <>
              {showUnchanged &&
                unchanged.map((c, i) => <DiffChangeLine key={`u${i}`} change={c} />)}
              <button
                type="button"
                onClick={() => setShowUnchanged((v) => !v)}
                className="font-mono text-xs text-slate-500 hover:text-slate-300 px-3 py-1"
              >
                {showUnchanged
                  ? "Hide unchanged"
                  : `Show ${unchanged.length} unchanged ${unchanged.length === 1 ? "line" : "lines"}`}
              </button>
            </>
          )}
        </>
      )}
    </div>
  )

  if (!showHeader) {
    return body
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-hidden">
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-800 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-white">{moduleLabel(diff.module)}</span>
          <DiffSummary diff={diff} />
        </div>
        <svg
          className={`h-4 w-4 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && <div className="border-t border-slate-700 p-3">{body}</div>}
    </div>
  )
}
