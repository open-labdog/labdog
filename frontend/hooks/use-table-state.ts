"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

export type FilterSpec<T = unknown> =
  | { type: "text"; placeholder?: string }
  | { type: "enum"; options?: { label: string; value: string }[]; from?: "accessor"; formatOption?: (raw: string) => string }
  | { type: "boolean"; trueLabel?: string; falseLabel?: string }
  | { type: "dateRange" }
  | {
      type: "custom"
      render: (value: unknown, onChange: (v: unknown) => void) => React.ReactNode
      predicate: (row: T, value: unknown) => boolean
    }

export type ColumnDef<T> = {
  key: string
  label: string
  accessor?: (row: T) => unknown
  cell: (row: T) => React.ReactNode
  defaultWidth?: number
  resizable?: boolean
  sortable?: boolean
  filter?: FilterSpec<T>
  align?: "left" | "right" | "center"
  className?: string
  headerClassName?: string
}

export type SortDir = "asc" | "desc" | null

type FilterValue =
  | string                        // text
  | string[]                      // enum multi-select
  | "yes" | "no" | ""             // boolean tri-state
  | { from: string; to: string }  // dateRange
  | unknown                       // custom

export type FilterValues = Record<string, FilterValue>

function widthsKey(tableId: string) {
  return `labdog:table-widths:${tableId}`
}

function loadWidths(tableId: string): Record<string, number> {
  try {
    const raw = typeof window !== "undefined" ? localStorage.getItem(widthsKey(tableId)) : null
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (typeof parsed !== "object" || parsed === null) return {}
    const out: Record<string, number> = {}
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === "number" && v >= 40 && v <= 2000) out[k] = v
    }
    return out
  } catch {
    return {}
  }
}

function saveWidths(tableId: string, widths: Record<string, number>) {
  try {
    if (typeof window === "undefined") return
    localStorage.setItem(widthsKey(tableId), JSON.stringify(widths))
  } catch { /* ignore quota / private mode */ }
}

function compareValues(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0
  if (a == null) return -1
  if (b == null) return 1
  if (typeof a === "number" && typeof b === "number") return a - b
  if (typeof a === "boolean" && typeof b === "boolean") return (a ? 1 : 0) - (b ? 1 : 0)
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" })
}

function matchesFilter<T>(row: T, col: ColumnDef<T>, value: FilterValue): boolean {
  if (!col.filter) return true
  const spec = col.filter
  const raw = col.accessor ? col.accessor(row) : undefined

  if (spec.type === "text") {
    const q = String(value ?? "").trim().toLowerCase()
    if (!q) return true
    return String(raw ?? "").toLowerCase().includes(q)
  }

  if (spec.type === "enum") {
    const selected = Array.isArray(value) ? value : []
    if (selected.length === 0) return true
    return selected.includes(String(raw ?? ""))
  }

  if (spec.type === "boolean") {
    if (value === "" || value == null) return true
    const boolish = Boolean(raw)
    return (value === "yes" && boolish) || (value === "no" && !boolish)
  }

  if (spec.type === "dateRange") {
    const range = (value ?? { from: "", to: "" }) as { from: string; to: string }
    if (!range.from && !range.to) return true
    const iso = typeof raw === "string" ? raw : raw instanceof Date ? raw.toISOString() : ""
    if (!iso) return false
    const d = iso.slice(0, 10)
    if (range.from && d < range.from) return false
    if (range.to && d > range.to) return false
    return true
  }

  if (spec.type === "custom") {
    return spec.predicate(row, value)
  }

  return true
}

export function useTableState<T>(
  tableId: string,
  columns: ColumnDef<T>[],
  data: T[] | undefined,
) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>(null)
  const [filters, setFilters] = useState<FilterValues>({})
  const [columnWidths, setColumnWidthsState] = useState<Record<string, number>>(() => loadWidths(tableId))
  const [hasUserResized, setHasUserResized] = useState<boolean>(() => Object.keys(loadWidths(tableId)).length > 0)

  const tableIdRef = useRef(tableId)
  useEffect(() => { tableIdRef.current = tableId }, [tableId])

  const setColumnWidth = useCallback((key: string, width: number) => {
    setColumnWidthsState(prev => {
      const next = { ...prev, [key]: Math.max(60, Math.round(width)) }
      saveWidths(tableIdRef.current, next)
      return next
    })
    setHasUserResized(true)
  }, [])

  const toggleSort = useCallback((key: string) => {
    setSortKey(prevKey => {
      if (prevKey !== key) { setSortDir("asc"); return key }
      setSortDir(prevDir => prevDir === "asc" ? "desc" : prevDir === "desc" ? null : "asc")
      return prevKey
    })
  }, [])

  const setFilter = useCallback((key: string, value: FilterValue) => {
    setFilters(prev => ({ ...prev, [key]: value }))
  }, [])

  const clearFilters = useCallback(() => setFilters({}), [])

  const activeFilterCount = useMemo(() => {
    let n = 0
    for (const col of columns) {
      if (!col.filter) continue
      const v = filters[col.key]
      if (v == null || v === "") continue
      if (Array.isArray(v) && v.length === 0) continue
      if (typeof v === "object" && !Array.isArray(v)) {
        const r = v as { from?: string; to?: string }
        if (!r.from && !r.to) continue
      }
      n++
    }
    return n
  }, [filters, columns])

  const visibleRows = useMemo(() => {
    if (!data) return [] as T[]
    const filtered = data.filter(row =>
      columns.every(col => matchesFilter(row, col, filters[col.key] as FilterValue))
    )
    if (!sortKey || !sortDir) return filtered
    const col = columns.find(c => c.key === sortKey)
    if (!col || col.sortable === false) return filtered
    const sorted = [...filtered].sort((a, b) => {
      const av = col.accessor ? col.accessor(a) : undefined
      const bv = col.accessor ? col.accessor(b) : undefined
      return sortDir === "asc" ? compareValues(av, bv) : compareValues(bv, av)
    })
    return sorted
  }, [data, columns, filters, sortKey, sortDir])

  return {
    sortKey,
    sortDir,
    toggleSort,
    filters,
    setFilter,
    clearFilters,
    activeFilterCount,
    visibleRows,
    columnWidths,
    setColumnWidth,
    hasUserResized,
  }
}
