"use client"

import { useMemo, useState, useRef, useEffect, useCallback, type ReactNode } from "react"
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { TableFilterCell } from "@/components/ui/table-filter-cell"
import { useTableState, type ColumnDef, type FilterSpec } from "@/hooks/use-table-state"
import { useColumnResize } from "@/hooks/use-column-resize"
import { cn } from "@/lib/utils"
import { Filter } from "lucide-react"

export type { ColumnDef, FilterSpec } from "@/hooks/use-table-state"

type Props<T> = {
  tableId: string
  columns: ColumnDef<T>[]
  data: T[] | undefined
  emptyMessage?: ReactNode
  loading?: boolean
  loadingSkeleton?: ReactNode
  getRowKey?: (row: T, index: number) => string | number
  rowClassName?: (row: T) => string | undefined
  onRowClick?: (row: T) => void
  /** Custom row renderer for dnd-kit integrations. Receives the default cells. */
  renderRow?: (row: T, index: number, defaultCells: ReactNode) => ReactNode
  className?: string
}

export function DataTable<T>({
  tableId,
  columns,
  data,
  emptyMessage,
  loading,
  loadingSkeleton,
  getRowKey,
  rowClassName,
  onRowClick,
  renderRow,
  className,
}: Props<T>) {
  const state = useTableState<T>(tableId, columns, data)
  const { startResize } = useColumnResize(state.setColumnWidth)

  const [openFilterKey, setOpenFilterKey] = useState<string | null>(null)
  const [popoverPos, setPopoverPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const popoverRefs = useRef<Map<string, HTMLElement | null>>(new Map())
  const triggerRefs = useRef<Map<string, HTMLButtonElement | null>>(new Map())

  const computePos = useCallback((key: string) => {
    const btn = triggerRefs.current.get(key)
    if (!btn) return
    const rect = btn.getBoundingClientRect()
    const popover = popoverRefs.current.get(key)
    const popW = popover?.offsetWidth ?? 220
    const popH = popover?.offsetHeight ?? 200

    // Right-align the popover to the trigger: popover right edge == trigger right edge.
    let left = rect.right - popW
    // Clamp to viewport
    if (left < 8) left = 8
    if (left + popW > window.innerWidth - 8) left = window.innerWidth - popW - 8

    const top = rect.bottom + popH > window.innerHeight
      ? Math.max(8, rect.top - popH - 4)
      : rect.bottom + 4

    setPopoverPos({ top, left })
  }, [])

  useEffect(() => {
    if (!openFilterKey) return
    // Defer so the popover has rendered and we can use its actual size.
    const raf = requestAnimationFrame(() => computePos(openFilterKey))
    function onReposition() { computePos(openFilterKey!) }
    window.addEventListener("resize", onReposition)
    window.addEventListener("scroll", onReposition, true)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", onReposition)
      window.removeEventListener("scroll", onReposition, true)
    }
  }, [openFilterKey, computePos])

  useEffect(() => {
    if (!openFilterKey) return
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Node
      const popover = popoverRefs.current.get(openFilterKey!)
      const trigger = triggerRefs.current.get(openFilterKey!)
      if (
        (popover && popover.contains(target)) ||
        (trigger && trigger.contains(target))
      ) return
      setOpenFilterKey(null)
    }
    document.addEventListener("mousedown", onMouseDown)
    return () => document.removeEventListener("mousedown", onMouseDown)
  }, [openFilterKey])

  function isFilterActive(col: ColumnDef<T>): boolean {
    if (!col.filter) return false
    const v = state.filters[col.key]
    if (v == null) return false
    if (col.filter.type === "text" || col.filter.type === "boolean") return (v as string) !== ""
    if (col.filter.type === "enum") return (v as string[]).length > 0
    if (col.filter.type === "dateRange") {
      const dr = v as { from: string; to: string }
      return dr.from !== "" || dr.to !== ""
    }
    return false
  }

  function resetFilter(col: ColumnDef<T>) {
    if (!col.filter) return
    const type = col.filter.type
    if (type === "text" || type === "boolean") state.setFilter(col.key, "" as never)
    else if (type === "enum") state.setFilter(col.key, [] as never)
    else if (type === "dateRange") state.setFilter(col.key, { from: "", to: "" } as never)
  }

  const autoOptionsByCol = useMemo(() => {
    const map = new Map<string, { label: string; value: string }[]>()
    if (!data) return map
    for (const col of columns) {
      if (!col.filter) continue
      if (col.filter.type !== "enum") continue
      if (!col.filter.from && col.filter.options) continue
      const values = new Set<string>()
      for (const row of data) {
        const v = col.accessor ? col.accessor(row) : undefined
        if (v == null) continue
        values.add(String(v))
      }
      const spec = col.filter
      map.set(col.key, Array.from(values).sort().map(v => ({
        label: spec.formatOption ? spec.formatOption(v) : v,
        value: v,
      })))
    }
    return map
  }, [columns, data])

  const useFixedLayout = state.hasUserResized

  function headerContent(col: ColumnDef<T>) {
    const sortable = col.sortable !== false && col.filter !== undefined
    const isSorted = state.sortKey === col.key && state.sortDir != null
    const arrow = isSorted ? (state.sortDir === "asc" ? "↑" : "↓") : ""
    return (
      <span className="inline-flex items-center gap-1 w-full">
        <span className="truncate flex-1">{col.header ?? col.label}</span>
        {sortable && arrow && <span className="text-slate-400 text-xs">{arrow}</span>}
        {col.filter && (
          <span className="relative">
            <button
              type="button"
              ref={(el) => { triggerRefs.current.set(col.key, el) }}
              data-filter-trigger={col.key}
              onClick={(e) => {
                e.stopPropagation()
                setOpenFilterKey(openFilterKey === col.key ? null : col.key)
              }}
              className={cn(
                "inline-flex items-center justify-center rounded p-0.5 transition-colors",
                isFilterActive(col) ? "text-primary" : "text-slate-500 hover:text-slate-300"
              )}
              aria-label={`Filter ${col.label}`}
            >
              <Filter className="size-3.5" />
            </button>
            {openFilterKey === col.key && (
              <div
                ref={(el) => { popoverRefs.current.set(col.key, el) }}
                onClick={(e) => e.stopPropagation()}
                style={{ position: "fixed", top: popoverPos.top, left: popoverPos.left }}
                className="z-50 w-max min-w-[180px] max-w-[320px] rounded-lg border border-slate-700 bg-slate-900 shadow-lg"
              >
                <div className="p-2">
                  <TableFilterCell
                    spec={col.filter as FilterSpec}
                    value={state.filters[col.key]}
                    onChange={(v) => state.setFilter(col.key, v as never)}
                    autoOptions={autoOptionsByCol.get(col.key)}
                  />
                </div>
                <div className="flex items-center justify-between border-t border-slate-800 px-2 py-1.5">
                  {col.filter.type === "text" || col.filter.type === "dateRange" ? (
                    <Button type="button" size="xs" variant="ghost" onClick={() => resetFilter(col)}>
                      Reset
                    </Button>
                  ) : <span />}
                  <Button type="button" size="xs" onClick={() => setOpenFilterKey(null)}>
                    OK
                  </Button>
                </div>
              </div>
            )}
          </span>
        )}
      </span>
    )
  }

  const totalCols = columns.length

  return (
    <div className={cn("rounded-lg border border-slate-700 bg-slate-900", className)}>
      <Table className={useFixedLayout ? "table-fixed" : undefined}>
        <TableHeader>
          <TableRow className="border-slate-700">
            {columns.map(col => {
              const width = state.columnWidths[col.key] ?? col.defaultWidth
              const resizable = col.resizable !== false && col.defaultWidth != null
              const sortable = col.sortable !== false
              // Width acts as a *preferred* width, not a hard floor. After the
              // user resizes anything we switch to fixed-layout mode (see
              // useFixedLayout below) and pin minWidth = width so dragged
              // sizes hold; until then we let auto-layout shrink columns to
              // fit the container, otherwise narrow viewports force a
              // horizontal scrollbar even when there's nothing to scroll to.
              const style = width
                ? state.hasUserResized
                  ? { width, minWidth: width }
                  : { width }
                : undefined
              return (
                <TableHead
                  key={col.key}
                  style={style}
                  className={cn(
                    "relative select-none",
                    col.align === "right" && "text-right",
                    col.align === "center" && "text-center",
                    sortable && "cursor-pointer hover:bg-muted/30",
                    col.headerClassName,
                  )}
                  onClick={sortable ? () => state.toggleSort(col.key) : undefined}
                >
                  {headerContent(col)}
                  {resizable && (
                    <span
                      role="separator"
                      aria-orientation="vertical"
                      onMouseDown={startResize(col.key, width ?? 120)}
                      onClick={(e) => e.stopPropagation()}
                      title="Drag to resize"
                      className="absolute right-0 top-0 h-full w-2 cursor-col-resize select-none hover:bg-slate-500/40 active:bg-slate-500/70"
                    />
                  )}
                </TableHead>
              )
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading && loadingSkeleton && (
            <TableRow className="border-slate-700">
              <TableCell colSpan={totalCols}>{loadingSkeleton}</TableCell>
            </TableRow>
          )}

          {!loading && (data == null || data.length === 0) && (
            <TableRow className="border-slate-700">
              <TableCell colSpan={totalCols} className="text-center py-8 text-slate-400">
                {emptyMessage ?? "No data."}
              </TableCell>
            </TableRow>
          )}

          {!loading && data && data.length > 0 && state.visibleRows.length === 0 && (
            <TableRow className="border-slate-700">
              <TableCell colSpan={totalCols} className="text-center py-8">
                <div className="text-slate-400 text-sm mb-2">No rows match the current filters.</div>
                <Button size="sm" variant="ghost" onClick={state.clearFilters}>
                  Clear all filters
                </Button>
              </TableCell>
            </TableRow>
          )}

          {!loading && state.visibleRows.map((row, idx) => {
            const key = getRowKey ? getRowKey(row, idx) : idx
            const cls = rowClassName?.(row)
            const cells = columns.map(col => (
              <TableCell
                key={col.key}
                className={cn(
                  col.align === "right" && "text-right",
                  col.align === "center" && "text-center",
                  col.className,
                )}
              >
                {col.cell(row)}
              </TableCell>
            ))
            if (renderRow) return renderRow(row, idx, cells)
            return (
              <TableRow
                key={key}
                className={cn("border-slate-700", onRowClick && "cursor-pointer", cls)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {cells}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
