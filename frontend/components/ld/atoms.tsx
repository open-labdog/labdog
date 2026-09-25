"use client"

import type { CSSProperties, MouseEvent, ReactNode } from "react"
import { statusDef } from "@/lib/fleet"
import { toneInk, toneSoft, toneVar, type Tone } from "./tone"

/* ── status vocabulary — one component, used everywhere ─────────── */

export function Dot({ tone, pulse }: { tone?: Tone; pulse?: boolean }) {
  return (
    <span
      className={pulse ? "pulse" : undefined}
      style={{ width: 6, height: 6, borderRadius: 3, background: toneVar(tone), flexShrink: 0, display: "inline-block" }}
    />
  )
}

/** A host's sync status as the word the design uses for it, with its dot. */
export function Status({ s, dim }: { s: string | null | undefined; dim?: boolean }) {
  const m = statusDef(s)
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11.5px] font-medium"
      style={{ color: dim ? "var(--text-2)" : toneVar(m.tone) }}
    >
      <Dot tone={m.tone} pulse={s === "pending"} />
      {m.label}
    </span>
  )
}

export function Tag({
  children,
  tone,
  mono = true,
  title,
  onClick,
  shrink,
  className,
}: {
  children: ReactNode
  tone?: Tone
  mono?: boolean
  title?: string
  onClick?: (e: MouseEvent<HTMLSpanElement>) => void
  shrink?: boolean
  className?: string
}) {
  const style: CSSProperties = {
    color: tone ? toneInk(tone) : "var(--text-2)",
    background: tone ? toneSoft(tone) : "var(--surface-3)",
    border: `1px solid ${tone ? toneVar(tone) : "var(--border)"}`,
    cursor: onClick ? "pointer" : "default",
    ...(shrink ? { minWidth: 0, flexShrink: 1, overflow: "hidden", textOverflow: "ellipsis", display: "inline-block" } : null),
  }
  return (
    <span
      title={title}
      onClick={onClick}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-[3px] px-1.5 py-px text-[10.5px] font-medium ${mono ? "mono" : ""} ${className ?? ""}`}
      style={style}
    >
      {children}
    </span>
  )
}

/* provenance — the sentence "this comes from group X, priority 40" as a chip */
export function Provenance({
  origin,
  label,
  priority,
  shadowed,
}: {
  origin: "group" | "host" | "system"
  label: string
  priority?: number | null
  shadowed?: boolean
}) {
  const tone: Tone | undefined = origin === "host" ? "accent" : origin === "system" ? "hold" : undefined
  const title =
    origin === "group"
      ? `declared by group ${label}, priority ${priority}`
      : origin === "host"
        ? "declared on this host, wins over every group"
        : "LabDog control-plane rule, cannot be removed"
  return (
    <span className="inline-flex items-center gap-1.5" style={{ opacity: shadowed ? 0.45 : 1 }}>
      <Tag tone={tone} title={title}>
        {label}
      </Tag>
      {origin === "group" && priority != null && (
        <span className="mono num text-[10px] text-text-faint">p{priority}</span>
      )}
      {shadowed && <span className="tt text-text-faint">shadowed</span>}
    </span>
  )
}

export const Kbd = ({ children }: { children: ReactNode }) => (
  <span className="mono rounded-[3px] border border-line bg-surface-3 px-1 py-px text-[10px] text-text-3">{children}</span>
)

export function Empty({ title, note, action }: { title: string; note?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-[7px] p-7 text-center">
      <div className="h-[22px] w-[22px] rounded border-[1.5px] border-line-strong" />
      <div className="text-[13px] font-semibold">{title}</div>
      {note && <div className="max-w-[340px] text-xs text-text-3">{note}</div>}
      {action}
    </div>
  )
}
