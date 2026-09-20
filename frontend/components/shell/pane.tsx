"use client"

import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { Host, HostGroup } from "@/lib/types"
import { itemIsActive, zoneDef, type ShellCounts, type ZoneKey } from "./zones"

/**
 * The contextual pane: one zone's destinations, sized for the next five
 * features rather than the current ones. In the Config zone it also
 * carries the scope switcher — module × scope is a matrix, and neither
 * axis belongs in the rail.
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
  const router = useRouter()
  const scope = search.get("scope") ?? "fleet"

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
    enabled: zone === "config",
  })
  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    enabled: zone === "config",
  })

  if (z.items.length === 0) return null

  const hrefFor = (href: string, module?: string) =>
    module && scope !== "fleet" ? `${href}?scope=${encodeURIComponent(scope)}` : href

  const setScope = (k: string) => {
    const mod = pathname.startsWith("/config/") ? pathname : "/config/firewall"
    router.push(k === "fleet" ? mod : `${mod}?scope=${encodeURIComponent(k)}`)
    if (overlay) onClose()
  }

  const hostScope = scope.startsWith("host:") ? hosts?.find((h) => String(h.id) === scope.slice(5)) : undefined
  const sortedGroups = [...(groups ?? [])].sort((a, b) => b.priority - a.priority)
  const hostCount = (gid: number) => hosts?.filter((h) => h.group_ids.includes(gid)).length

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
              href={hrefFor(it.href, it.module)}
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

        {zone === "config" && (
          <div className="mt-3 flex flex-col gap-1.5 border-t border-line pt-[11px]">
            <span className="tt">scope</span>
            <div className="flex flex-col gap-[3px]">
              <ScopeButton on={scope === "fleet"} label="Fleet — all scopes" meta={hosts ? `${hosts.length} hosts` : "every scope"} onClick={() => setScope("fleet")} />
              {sortedGroups.map((g) => {
                const k = `group:${g.id}`
                const hc = hostCount(g.id)
                return (
                  <ScopeButton
                    key={k}
                    on={scope === k}
                    label={`group: ${g.name}`}
                    meta={`p${g.priority}${hc != null ? ` · ${hc} host${hc === 1 ? "" : "s"}` : ""}`}
                    onClick={() => setScope(k)}
                  />
                )
              })}
              {hostScope && (
                <ScopeButton on label={`host: ${hostScope.hostname}`} meta="effective" onClick={() => setScope(scope)} />
              )}
            </div>
            <span className="text-[10.5px] leading-[1.45] text-text-faint">
              Module × scope is a matrix. Pick a module above and a scope here; a host&apos;s effective state opens from the host itself.
            </span>
          </div>
        )}
      </div>
    </aside>
  )
}

function ScopeButton({ on, label, meta, onClick }: { on: boolean; label: string; meta: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className="flex w-full flex-col items-start gap-0.5 rounded-r px-2 py-[5px] text-left"
      style={{
        border: `1px solid ${on ? "var(--accent-line)" : "var(--border)"}`,
        background: on ? "var(--accent-soft)" : "var(--surface-2)",
      }}
    >
      <span className="mono trunc w-full text-[11px]" style={{ color: on ? "var(--text)" : "var(--text-2)", fontWeight: on ? 600 : 400 }}>
        {label}
      </span>
      <span className="tt text-[9px]">{meta}</span>
    </button>
  )
}
