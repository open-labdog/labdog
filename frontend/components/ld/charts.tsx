"use client"

import { STATUS, STATUS_ORDER, type StatusCounts } from "@/lib/fleet"
import type { SyncStatus } from "@/lib/types"
import { Dot } from "./atoms"
import { toneSoft, toneVar, type Tone } from "./tone"

/* ── small charts (data, not decoration) ────────────────────────── */

export function Spark({
  data,
  tone = "warn",
  h = 30,
  fill = true,
  labelLast,
}: {
  data: number[]
  tone?: Tone
  h?: number
  fill?: boolean
  labelLast?: boolean
}) {
  if (data.length === 0) return null
  const max = Math.max(...data, 1)
  const min = Math.min(...data, 0)
  const span = Math.max(data.length - 1, 1)
  const pts = data.map((v, i) => [(i / span) * 100, 100 - ((v - min) / (max - min || 1)) * 88 - 6] as const)
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")
  return (
    <div className="flex min-w-0 flex-1 items-end gap-2">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block" }} aria-hidden>
        {fill && <path d={`${d} L100,100 L0,100 Z`} fill={toneSoft(tone)} opacity="0.85" />}
        <path d={d} fill="none" stroke={toneVar(tone)} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      </svg>
      {labelLast && (
        <span className="mono num text-[13px] font-semibold" style={{ color: toneVar(tone) }}>
          {data[data.length - 1]}
        </span>
      )}
    </div>
  )
}

export function Meter({
  pct,
  tone,
  label,
  value,
  h = 5,
}: {
  pct: number
  tone?: Tone
  label: string
  value?: string
  h?: number
}) {
  const t: Tone = tone ?? (pct > 90 ? "danger" : pct > 75 ? "warn" : "ok")
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <div className="flex justify-between gap-2">
        <span className="tt">{label}</span>
        <span className="mono num text-[11px] text-text">{value ?? `${Math.round(pct)}%`}</span>
      </div>
      <div className="overflow-hidden bg-surface-3" style={{ height: h, borderRadius: h }}>
        <div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, height: "100%", background: toneVar(t), transition: "width var(--dur)" }} />
      </div>
    </div>
  )
}

/* fleet status bar — every segment is a filter into the hosts list */
export function StatusBar({
  counts,
  onPick,
  active,
}: {
  counts: StatusCounts
  onPick?: (k: SyncStatus) => void
  active?: SyncStatus | null
}) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return (
    <div className="flex shrink-0 flex-wrap gap-px overflow-hidden rounded-r border border-line bg-line">
      {STATUS_ORDER.map((k) => {
        const n = counts[k] ?? 0
        const m = STATUS[k]
        const on = active === k
        return (
          <button
            key={k}
            type="button"
            onClick={() => onPick?.(k)}
            title={`${n} ${m.label} — click to filter the hosts list`}
            className="flex min-w-[104px] flex-col gap-1 border-0 px-2.5 py-2 text-left"
            style={{
              flex: Math.min(Math.max(n, 4), 14),
              background: on ? toneSoft(m.tone) : "var(--surface)",
              boxShadow: on ? `inset 0 -2px 0 ${toneVar(m.tone)}` : "none",
              transition: "background var(--dur)",
            }}
          >
            <span className="flex items-center gap-1.5">
              <Dot tone={m.tone} pulse={k === "pending"} />
              <span className="tt text-text-3">{m.label}</span>
            </span>
            <span
              className="mono num text-[17px] font-semibold leading-none"
              style={{ color: n === 0 ? "var(--text-faint)" : toneVar(m.tone) }}
            >
              {n}
              <span className="text-[10px] font-normal text-text-faint"> / {total}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
