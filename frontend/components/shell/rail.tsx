"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useTheme } from "next-themes"
import { Glyph } from "./glyph"
import { AccountMenu } from "./account-menu"
import { SETTINGS_ZONE, ZONE_DEFS, type ShellCounts, type ZoneKey } from "./zones"

function Badge({ n }: { n: number }) {
  return (
    <span
      className="mono num absolute flex h-[14px] min-w-[14px] items-center justify-center rounded-[7px] border border-hold bg-hold-soft px-[3px] text-[9px] font-bold text-hold-ink"
      style={{ top: 1, right: 1 }}
    >
      {n > 99 ? "99+" : n}
    </span>
  )
}

/** The Overview badge when the rail is expanded: after the name, not on the icon. */
function InlineBadge({ n }: { n: number }) {
  return (
    <span className="mono num flex h-[16px] min-w-[16px] items-center justify-center rounded-[8px] border border-hold bg-hold-soft px-[4px] text-[9.5px] font-bold text-hold-ink">
      {n > 99 ? "99+" : n}
    </span>
  )
}

export function ThemeToggle({ compact }: { compact?: boolean }) {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme !== "light"
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      title={`Switch to ${dark ? "light" : "dark"} theme — t`}
      aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
      className="btn btn-sm btn-ghost"
      style={{ padding: compact ? 5 : 7 }}
    >
      <Glyph shape="contrast" size={compact ? 12 : 15} />
    </button>
  )
}

/**
 * Rail widths: icons only, and icons with their names. The expanded width
 * fits the widest row (Overview with a "99+" badge, a few px wider than
 * Operations) with a little slack for font differences between platforms.
 * Widen it if a longer zone name is added.
 */
export const RAIL_W = 50
export const RAIL_W_EXPANDED = 150

/**
 * Pattern A shell: a rail of four zones at a fixed cost, plus Settings and
 * the account at the foot. The rail never grows with the feature count —
 * new destinations go in a zone's pane.
 *
 * Collapsed it shows icons only (names on hover); expanded it shows each
 * icon with its name. The operator's choice is remembered in this browser
 * (see `AppShell`), and `]` toggles it.
 */
export function Rail({
  zone,
  counts,
  onPalette,
  expanded,
  onToggleExpanded,
}: {
  zone: ZoneKey
  counts: ShellCounts
  onPalette: () => void
  expanded: boolean
  onToggleExpanded: () => void
}) {
  const router = useRouter()
  const pendingTotal = counts.pendingBlocking ?? 0
  // One row shape for zones and Settings: a fixed icon cell so the icons
  // stay put when the names slide in, then the name, then any badge.
  const rowClass = expanded
    ? "relative flex h-[34px] w-full items-center gap-2 rounded-md border-0 pl-[10px] pr-2 text-left"
    : "relative grid h-[34px] w-[38px] place-items-center rounded-md border-0"
  const label = (text: string, on: boolean) =>
    expanded && (
      <span
        className="trunc min-w-0 flex-1 text-[12.5px] font-medium"
        style={{ color: on ? "var(--accent)" : "var(--text-2)" }}
      >
        {text}
      </span>
    )
  return (
    <nav
      aria-label="Zones"
      data-expanded={expanded || undefined}
      className={`flex shrink-0 flex-col gap-[3px] border-r border-line bg-rail pb-2.5 pt-[9px] ${expanded ? "items-stretch px-1.5" : "items-center"}`}
      style={{ width: expanded ? RAIL_W_EXPANDED : RAIL_W, transition: "width var(--dur)" }}
    >
      <Link
        href="/overview"
        title="LabDog"
        className={`mb-[9px] flex items-center gap-2 hover:no-underline ${expanded ? "pl-[6px]" : ""}`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- static svg, no optimisation needed */}
        <img src="/logo.svg" alt="LabDog" width={26} height={26} className="block" />
        {expanded && <span className="text-[13px] font-semibold text-text">LabDog</span>}
      </Link>
      {ZONE_DEFS.map((z) => {
        const on = z.k === zone
        const badge = z.k === "overview" && pendingTotal > 0
        return (
          <button
            key={z.k}
            type="button"
            onClick={() => router.push(z.href)}
            title={expanded ? undefined : z.label}
            aria-label={z.label}
            aria-current={on ? "page" : undefined}
            className={rowClass}
            style={{ background: on ? "var(--accent-soft)" : "transparent", transition: "background var(--dur)" }}
          >
            <Glyph shape={z.glyph} on={on} />
            {label(z.label, on)}
            {badge && (expanded ? <InlineBadge n={pendingTotal} /> : <Badge n={pendingTotal} />)}
            {on && <span className="absolute w-[2px] rounded-[2px] bg-ld-accent" style={{ left: -6, top: 8, bottom: 8 }} />}
          </button>
        )
      })}
      <div className={`mt-auto flex gap-1.5 ${expanded ? "flex-col items-stretch" : "flex-col items-center"}`}>
        <button
          type="button"
          onClick={() => router.push(SETTINGS_ZONE.href)}
          title={expanded ? undefined : "Settings"}
          aria-label="Settings"
          aria-current={zone === "settings" ? "page" : undefined}
          className={rowClass}
          style={{ background: zone === "settings" ? "var(--accent-soft)" : "transparent" }}
        >
          <Glyph shape="gear" on={zone === "settings"} />
          {label("Settings", zone === "settings")}
        </button>
        {expanded ? (
          <button type="button" onClick={onPalette} title="Command palette — ⌘K" aria-label="Command palette" className={rowClass}>
            <Glyph shape="search" />
            {label("Search", false)}
            <span className="mono text-[10px] text-text-faint">⌘K</span>
          </button>
        ) : (
          <button type="button" onClick={onPalette} title="Command palette — ⌘K" aria-label="Command palette" className="btn btn-sm btn-ghost" style={{ padding: 7 }}>
            <Glyph shape="search" />
          </button>
        )}
        <div className={`flex items-center gap-1.5 ${expanded ? "flex-row pl-[6px]" : "flex-col"}`}>
          <ThemeToggle />
          <AccountMenu />
          <button
            type="button"
            onClick={onToggleExpanded}
            title={expanded ? "Collapse rail — ]" : "Expand rail — ]"}
            aria-label={expanded ? "Collapse rail" : "Expand rail"}
            aria-expanded={expanded}
            className={`btn btn-sm btn-ghost ${expanded ? "ml-auto" : ""}`}
            style={{ padding: "4px 7px" }}
          >
            <span className="mono text-[11px] text-text-faint">{expanded ? "«" : "»"}</span>
          </button>
        </div>
      </div>
    </nav>
  )
}

/** The rail at phone width: the four zones along the bottom edge. */
export function MobileRail({ zone, counts }: { zone: ZoneKey; counts: ShellCounts }) {
  const router = useRouter()
  const pendingTotal = counts.pendingBlocking ?? 0
  return (
    <nav aria-label="Zones" className="flex shrink-0 border-t border-line bg-rail pb-1">
      {ZONE_DEFS.map((z) => {
        const on = z.k === zone
        return (
          <button
            key={z.k}
            type="button"
            onClick={() => router.push(z.href)}
            aria-current={on ? "page" : undefined}
            className="relative flex flex-1 flex-col items-center gap-[5px] border-0 bg-transparent pb-[5px] pt-[9px]"
          >
            <Glyph shape={z.glyph} on={on} size={16} />
            <span className="text-[9px] font-semibold" style={{ color: on ? "var(--accent)" : "var(--text-3)" }}>
              {z.label}
            </span>
            {z.k === "overview" && pendingTotal > 0 && (
              <span
                className="mono num absolute flex h-[14px] min-w-[14px] items-center justify-center rounded-[7px] border border-hold bg-hold-soft px-[3px] text-[9px] font-bold text-hold-ink"
                style={{ top: 4, left: "50%", marginLeft: 6 }}
              >
                {pendingTotal}
              </span>
            )}
          </button>
        )
      })}
    </nav>
  )
}
