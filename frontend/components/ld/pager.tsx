"use client"

/** The page-size choices a pager offers; `0` is "all". */
export const PAGE_SIZES = [10, 25, 50, 0] as const

/** Rows `page` of a list shows at `size` per page (`0` = all of them). */
export function pageSlice<T>(rows: T[], page: number, size: number): T[] {
  return size === 0 ? rows : rows.slice(page * size, page * size + size)
}

/** The last page index for `total` rows, so a smaller list can clamp to it. */
export function lastPage(total: number, size: number): number {
  return size === 0 ? 0 : Math.max(0, Math.ceil(total / size) - 1)
}

/**
 * Previous / next and how many per page, for a table footer:
 * `11–17 of 17 · ‹ › · 10 per page`. Renders nothing for a list that fits
 * on the smallest page.
 */
export function Pager({
  total,
  page,
  size,
  onPage,
  onSize,
  noun = "rows",
}: {
  total: number
  page: number
  size: number
  onPage: (page: number) => void
  onSize: (size: number) => void
  noun?: string
}) {
  if (total <= PAGE_SIZES[0]) return null
  const last = lastPage(total, size)
  const from = size === 0 ? 1 : page * size + 1
  const to = size === 0 ? total : Math.min(total, (page + 1) * size)
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-text-3" role="navigation" aria-label={`${noun} pages`}>
      <span className="mono num" data-testid="pager-range">
        {from}–{to} of {total}
      </span>
      <button type="button" className="btn btn-sm btn-ghost" aria-label="Previous page" disabled={page <= 0} onClick={() => onPage(page - 1)}>
        ‹
      </button>
      <button type="button" className="btn btn-sm btn-ghost" aria-label="Next page" disabled={page >= last} onClick={() => onPage(page + 1)}>
        ›
      </button>
      <select
        className="inp mono"
        style={{ height: 22, padding: "0 4px", fontSize: 11, width: "auto" }}
        aria-label={`${noun} per page`}
        value={size}
        onChange={(e) => onSize(Number(e.target.value))}
      >
        {PAGE_SIZES.map((n) => (
          <option key={n} value={n}>
            {n === 0 ? "all" : `${n} per page`}
          </option>
        ))}
      </select>
    </div>
  )
}
