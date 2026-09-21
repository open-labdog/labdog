"use client"

import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { itemIsActive, zoneDef, type ShellCounts, type ZoneKey } from "./zones"

/**
 * The contextual pane: one zone's destinations, sized for the next five
 * features rather than the current ones. Objects (a host, a group) are
 * never listed here — they are one keystroke away in the palette.
 */
export function Pane({
  zone,
  counts,
  overlay,
  onClose,
}: {
  zone: ZoneKey
  counts: ShellCounts
  overlay: boolean
  onClose: () => void
}) {
  const z = zoneDef(zone)
  const pathname = usePathname()
  const search = useSearchParams()

  if (z.items.length === 0) return null

  return (
    <aside
      aria-label={`${z.label} navigation`}
      className={`pane-enter flex w-[208px] shrink-0 flex-col border-r border-line bg-surface ${overlay ? "absolute bottom-0 top-0 z-30 shadow-ld" : "relative"}`}
      style={overlay ? { left: 50 } : undefined}
    >
      <header className="flex items-center gap-2 px-3 pb-[9px] pt-[11px]">
        <span className="text-[12.5px] font-semibold">{z.label}</span>
        <button
          type="button"
          onClick={onClose}
          className="btn btn-sm btn-ghost ml-auto"
          style={{ padding: "2px 5px" }}
          title={overlay ? "Close" : "Collapse pane — ["}
          aria-label={overlay ? "Close pane" : "Collapse pane"}
        >
          <span className="mono text-[10px] text-text-faint">{overlay ? "✕" : "‹"}</span>
        </button>
      </header>
      <div className="scroll flex flex-1 flex-col gap-px px-2 pb-2">
        {z.items.map((it) => {
          const on = itemIsActive(it, pathname, search)
          const n = it.n?.(counts)
          const badge = it.badge?.(counts)
          return (
            <Link
              key={it.href}
              href={it.href}
              onClick={() => overlay && onClose()}
              aria-current={on ? "page" : undefined}
              className="flex items-center gap-2 rounded-r px-[9px] py-1.5 text-left text-[12.3px] hover:no-underline"
              style={{
                background: on ? "var(--surface-3)" : "transparent",
                color: on ? "var(--text)" : "var(--text-2)",
                fontWeight: on ? 600 : 400,
              }}
            >
              <span className="trunc flex-1">{it.label}</span>
              {n != null && <span className="mono num text-[10px] text-text-faint">{n}</span>}
              {badge != null && (
                <span className="mono num grid h-[15px] min-w-[15px] place-items-center rounded-lg bg-hold-soft px-[3px] text-[9.5px] font-bold text-hold">
                  {badge}
                </span>
              )}
            </Link>
          )
        })}
      </div>
    </aside>
  )
}
