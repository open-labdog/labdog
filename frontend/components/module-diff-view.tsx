"use client"

import { useState } from "react"
import { Tag, toneInk, type Tone } from "@/components/ld"
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

// The Plan screen's vocabulary, so a host's sync preview reads like the
// fleet-wide one: add/del tints for add and remove, the warn tint for an
// update, and no tint for context lines.
const OP: Record<DiffOp, { s: string; tone: Tone; bg: string }> = {
  add: { s: "+", tone: "add", bg: "var(--add-bg)" },
  remove: { s: "−", tone: "del", bg: "var(--del-bg)" },
  update: { s: "~", tone: "warn", bg: "var(--warn-soft)" },
  unchanged: { s: "·", tone: "idle", bg: "transparent" },
}

function DiffChangeLine({ change }: { change: DiffChange }) {
  const op = OP[change.op]
  // Flex row so the marker stays in a fixed gutter and long, unbreakable tokens
  // (CIDRs, FQDNs) wrap with a hanging indent instead of sliding under the marker.
  return (
    <div className="mono flex items-start gap-[9px] rounded-[3px] px-[7px] py-[3px] text-[11.5px]" style={{ background: op.bg }}>
      <span className="w-[9px] shrink-0 select-none font-bold" style={{ color: toneInk(op.tone) }}>
        {op.s}
      </span>
      <span className={`min-w-0 flex-1 whitespace-pre-wrap break-words ${change.op === "unchanged" ? "text-text-3" : "text-text"}`}>{change.summary}</span>
    </div>
  )
}

/** Inline counts for a module's changes, as the Plan screen tags them. */
export function DiffSummary({ diff }: { diff: ModuleDiff }) {
  if (diff.error) return <Tag tone="danger">error</Tag>
  if (!diff.has_changes) return <span className="text-[10.5px] text-text-faint">no changes</span>
  return (
    <span className="flex gap-[5px]">
      {(["add", "remove", "update"] as const).map((op) => {
        const n = countByOp(diff.changes, op)
        return n ? (
          <Tag key={op} tone={OP[op].tone}>
            {OP[op].s}
            {n}
          </Tag>
        ) : null
      })}
    </span>
  )
}

/**
 * One module's normalized diff. With a header (the Sync-all preview) it is
 * a collapsible box per module; without one (a single module's preview)
 * it is just the lines.
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

  // Only the collapsible Sync-All boxes cap their own height; the standalone
  // per-module preview (no header) lets the dialog's own scroll region govern,
  // avoiding a scrollbar-inside-a-scrollbar.
  const body = (
    <div className={`flex flex-col gap-0.5 ${showHeader ? "scroll max-h-64" : ""}`}>
      {diff.error ? (
        <div className="px-[7px] py-1.5 text-[11.5px] text-danger">{diff.error}</div>
      ) : diff.changes.length === 0 ? (
        <div className="px-[7px] py-1.5 text-[11.5px] text-text-3">Nothing configured for this module</div>
      ) : (
        <>
          {changed.map((c, i) => (
            <DiffChangeLine key={`c${i}`} change={c} />
          ))}
          {unchanged.length > 0 && (
            <>
              {showUnchanged && unchanged.map((c, i) => <DiffChangeLine key={`u${i}`} change={c} />)}
              <button type="button" onClick={() => setShowUnchanged((v) => !v)} className="btn btn-sm btn-ghost self-start">
                {showUnchanged ? "hide unchanged" : `show ${unchanged.length} unchanged ${unchanged.length === 1 ? "line" : "lines"}`}
              </button>
            </>
          )}
        </>
      )}
    </div>
  )

  if (!showHeader) return body

  return (
    <div className="overflow-hidden rounded-r border border-line bg-surface">
      <button
        type="button"
        aria-expanded={expanded}
        className="row-hover flex w-full items-center gap-2.5 border-0 bg-transparent px-[11px] py-2 text-left"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="text-[12.5px] font-medium text-text">{moduleLabel(diff.module)}</span>
        <DiffSummary diff={diff} />
        <span className="ml-auto text-[10px] text-text-faint" aria-hidden>
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && <div className="border-t border-line p-2">{body}</div>}
    </div>
  )
}
