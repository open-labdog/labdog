"use client"

import type { ReactNode } from "react"
import { toneSoft, type Tone } from "./tone"

export interface Col<T> {
  k: string
  label: ReactNode
  /** CSS grid track — "120px", "minmax(120px,1fr)". Defaults to 1fr. */
  w?: string
  cell: (row: T) => ReactNode
  sortable?: boolean
  right?: boolean
  nowrap?: boolean
}

export interface Sort {
  k: string
  dir: 1 | -1
}

/* ── data table — sort, select, sticky head, dense rows ─────────── */
export function Table<T>({
  cols,
  rows,
  keyOf,
  sort,
  onSort,
  selected,
  onSelect,
  onRowClick,
  activeKey,
  dense = true,
  empty = "Nothing here",
  rowTone,
  loading,
  footer,
}: {
  cols: Col<T>[]
  rows: T[]
  keyOf: (row: T) => string | number
  sort?: Sort
  onSort?: (k: string) => void
  selected?: Set<string | number>
  onSelect?: (s: Set<string | number>) => void
  onRowClick?: (row: T) => void
  activeKey?: string | number
  dense?: boolean
  empty?: ReactNode
  rowTone?: (row: T) => Tone | undefined
  loading?: boolean
  /** A strip under the rows — totals, "showing N so far". */
  footer?: ReactNode
}) {
  const allSel = !!selected && rows.length > 0 && rows.every((r) => selected.has(keyOf(r)))
  const pad = dense ? "5px 10px" : "8px 10px"
  /* An fr track cannot shrink below a px minimum, so minmax(200px,1.6fr)
     imposes a hard width floor on the whole table. Proportions come from the
     fr; the floor is what breaks narrow containers — drop it. Cells already
     truncate. */
  const track = (w?: string) => (w || "1fr").replace(/minmax\(\s*[\d.]+px\s*,/g, "minmax(0,")
  const grid = (selected ? "28px " : "") + cols.map((c) => track(c.w)).join(" ")
  // A CSS grid rather than <table>, so columns can be sized with fr tracks
  // and rows can be one click target — but with the table roles, so it
  // reads as a table to assistive tech and to tests.
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="scroll min-h-0 flex-1" role="table">
        <div
          role="row"
          className="sticky top-0 z-[2] grid border-b border-line bg-surface-2"
          style={{ gridTemplateColumns: grid }}
        >
          {selected && onSelect && (
            <label role="columnheader" className="flex items-center" style={{ padding: pad }}>
              <input
                type="checkbox"
                aria-label="select all"
                checked={allSel}
                onChange={(e) => onSelect(e.target.checked ? new Set(rows.map(keyOf)) : new Set())}
                style={{ accentColor: "var(--accent)" }}
              />
            </label>
          )}
          {cols.map((c) => {
            const on = sort && sort.k === c.k
            const sortable = c.sortable !== false && !!onSort
            return (
              <div key={c.k} role="columnheader" aria-sort={on ? (sort!.dir === 1 ? "ascending" : "descending") : undefined} className="flex min-w-0">
                <button
                  type="button"
                  onClick={sortable ? () => onSort!(c.k) : undefined}
                  tabIndex={sortable ? 0 : -1}
                  className="flex w-full items-center gap-1 border-0 bg-transparent"
                  style={{
                    padding: pad,
                    textAlign: c.right ? "right" : "left",
                    justifyContent: c.right ? "flex-end" : "flex-start",
                    cursor: sortable ? "pointer" : "default",
                  }}
                >
                  <span className="tt" style={{ color: on ? "var(--text)" : "var(--text-3)" }}>
                    {c.label}
                  </span>
                  {on && <span className="text-[8px] text-ld-accent">{sort!.dir === 1 ? "▲" : "▼"}</span>}
                </button>
              </div>
            )
          })}
        </div>
        {loading && rows.length === 0 && (
          <div className="flex flex-col gap-px p-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-7 animate-pulse rounded bg-surface-2" style={{ opacity: 1 - i * 0.15 }} />
            ))}
          </div>
        )}
        {!loading && rows.length === 0 && (
          <div className="px-3 py-[26px] text-center text-xs text-text-3">{empty}</div>
        )}
        {rows.map((r) => {
          const k = keyOf(r)
          const sel = selected?.has(k)
          const active = activeKey === k
          const tone = rowTone?.(r)
          return (
            <div
              key={k}
              role="row"
              className="row-hover grid border-b border-line-faint"
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              style={{
                gridTemplateColumns: grid,
                cursor: onRowClick ? "pointer" : "default",
                background: active ? "var(--accent-soft)" : sel ? "var(--surface-3)" : tone ? toneSoft(tone) : "transparent",
                boxShadow: active ? "inset 2px 0 0 var(--accent)" : "none",
              }}
            >
              {selected && onSelect && (
                <label role="cell" onClick={(e) => e.stopPropagation()} className="flex items-center" style={{ padding: pad }}>
                  <input
                    type="checkbox"
                    aria-label="select row"
                    checked={!!sel}
                    onChange={(e) => {
                      const n = new Set(selected)
                      if (e.target.checked) n.add(k)
                      else n.delete(k)
                      onSelect(n)
                    }}
                    style={{ accentColor: "var(--accent)" }}
                  />
                </label>
              )}
              {cols.map((c) => (
                <div
                  key={c.k}
                  role="cell"
                  className="flex min-w-0 items-center text-xs text-text-2"
                  style={{ padding: pad, justifyContent: c.right ? "flex-end" : "flex-start" }}
                >
                  <div className={c.nowrap === false ? "" : "trunc"} style={{ minWidth: 0, width: c.right ? "auto" : "100%" }}>
                    {c.cell(r)}
                  </div>
                </div>
              ))}
            </div>
          )
        })}
      </div>
      {footer && <div className="tt shrink-0 border-t border-line bg-surface-2 px-3 py-[7px]">{footer}</div>}
    </div>
  )
}
