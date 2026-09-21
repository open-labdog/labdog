"use client"

import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react"
import { toneVar, type Tone } from "./tone"

/* ── key/value, numbers, code ───────────────────────────────────── */

export interface Fact {
  k: string
  v: ReactNode
  /** Copyable values — hostnames, paths, hashes — are mono. */
  mono?: boolean
  title?: string
  /** Grid columns to span; a long value can take the whole row. */
  span?: number
}

/**
 * The design's facts grid: a `.tt` caption over each value, as many
 * columns as fit. Host overview, repository detail, build info.
 */
export function Facts({ items, min = 140, className, style }: { items: Fact[]; min?: number; className?: string; style?: CSSProperties }) {
  return (
    <div
      className={`grid gap-x-3 gap-y-2 ${className ?? ""}`}
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, ...style }}
    >
      {items.map((f) => (
        <div key={f.k} className="flex min-w-0 flex-col gap-0.5" style={f.span ? { gridColumn: `span ${f.span}` } : undefined} title={f.title}>
          <span className="tt text-[8.5px]">{f.k}</span>
          <span className={`trunc text-[11.5px] text-text-2 ${f.mono ? "mono" : ""}`}>{f.v ?? "—"}</span>
        </div>
      ))}
    </div>
  )
}

/** One number that matters, in a card: label, the value in its tone, a note. */
export function Stat({ label, value, sub, tone, className }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; className?: string }) {
  return (
    <div className={`flex flex-col gap-[3px] rounded-r border border-line bg-surface-2 p-[9px] ${className ?? ""}`}>
      <span className="tt text-[8.5px]">{label}</span>
      <span className="mono num text-[17px] font-semibold leading-tight" style={{ color: tone ? toneVar(tone) : "var(--text)" }}>
        {value}
      </span>
      {sub && <span className="text-[11px] text-text-3">{sub}</span>}
    </div>
  )
}

/**
 * A command, a log, a config snippet: a bordered box with an optional
 * surface-2 header (tag + mono title + actions) over a `<pre>` at the
 * design's log measure — 11px mono, 1.75 line height. `preRef` lets a
 * live log pin itself to the bottom.
 */
export function CodeBlock({
  title,
  tag,
  actions,
  children,
  maxH = 420,
  wrap = true,
  preRef,
  className,
}: {
  title?: ReactNode
  tag?: ReactNode
  actions?: ReactNode
  children: ReactNode
  maxH?: number | string
  wrap?: boolean
  preRef?: RefObject<HTMLPreElement | null>
  className?: string
}) {
  const hasHead = title != null || tag != null || actions != null
  return (
    <div className={`flex min-w-0 flex-col overflow-hidden rounded-r border border-line bg-surface ${className ?? ""}`}>
      {hasHead && (
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface-2 px-2.5 py-1.5">
          {tag}
          {title != null && <span className="mono trunc text-[11.5px] text-text">{title}</span>}
          {actions && <div className="ml-auto flex items-center gap-1.5">{actions}</div>}
        </div>
      )}
      <pre
        ref={preRef}
        className="mono scroll m-0 px-2.5 py-2 text-[11px] leading-[1.75] text-text-2"
        style={{ maxHeight: maxH, whiteSpace: wrap ? "pre-wrap" : "pre", wordBreak: wrap ? "break-word" : undefined }}
      >
        {children}
      </pre>
    </div>
  )
}

/**
 * Copy to clipboard as a button whose label says what happened — the
 * design swaps labels ("copied") rather than showing an icon.
 */
export function Copy({ text, label = "copy", className = "btn btn-sm btn-ghost" }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  return (
    <button
      type="button"
      className={className}
      title="copy to clipboard"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setDone(true)
          if (timer.current) clearTimeout(timer.current)
          timer.current = setTimeout(() => setDone(false), 1500)
        } catch {
          /* clipboard unavailable (insecure context) — the text is on screen to select */
        }
      }}
    >
      {done ? "copied" : label}
    </button>
  )
}
