"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"

export interface FilterOption {
  k: string
  label: ReactNode
  n?: number
}

/**
 * A dropdown filter that reads as a sentence when set — "status · drifted"
 * — and as a plain label when it is not. `all` is the unset value.
 */
export function Filter({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: FilterOption[]
  onChange: (k: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", h)
    return () => document.removeEventListener("mousedown", h)
  }, [])
  const active = value !== "" && value !== "all"
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="btn btn-sm"
        style={active ? { borderColor: "var(--accent-line)", color: "var(--text)", background: "var(--accent-soft)" } : undefined}
      >
        <span style={{ color: active ? "var(--text-2)" : "var(--text-3)" }}>{label}</span>
        {active && <span className="font-semibold">{options.find((o) => o.k === value)?.label ?? value}</span>}
        <span className="text-[8px] text-text-faint">▾</span>
      </button>
      {open && (
        <div className="fade scroll absolute left-0 top-[calc(100%+4px)] z-40 max-h-[280px] min-w-[168px] rounded-r border border-line-strong bg-surface p-1 shadow-ld">
          {[{ k: "all", label: `all ${label}` } as FilterOption, ...options].map((o) => (
            <button
              key={o.k}
              type="button"
              onClick={() => {
                onChange(o.k)
                setOpen(false)
              }}
              className="flex w-full items-center justify-between gap-2 rounded-[3px] border-0 px-2 py-[5px] text-left text-xs"
              style={{
                background: o.k === value ? "var(--surface-3)" : "none",
                color: o.k === value ? "var(--text)" : "var(--text-2)",
              }}
            >
              <span className="trunc">{o.label}</span>
              {o.n != null && <span className="mono num text-[10.5px] text-text-faint">{o.n}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
