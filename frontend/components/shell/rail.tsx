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

export function ThemeToggle({ size = 12 }: { size?: number }) {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme !== "light"
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      title={`Switch to ${dark ? "light" : "dark"} theme — t`}
      aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
      className="btn btn-sm btn-ghost"
      style={{ padding: 7 }}
    >
      <div
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          border: "1.6px solid var(--text-3)",
          background: dark ? "transparent" : "var(--text-3)",
        }}
      />
    </button>
  )
}

/**
 * Pattern A shell: an icon rail of five zones at a fixed cost, plus
 * Settings and the account at the foot. The rail never grows with the
 * feature count — new destinations go in a zone's pane.
 */
export function Rail({
  zone,
  counts,
  onPalette,
}: {
  zone: ZoneKey
  counts: ShellCounts
  onPalette: () => void
}) {
  const router = useRouter()
  const pendingTotal = counts.pendingBlocking ?? 0
  return (
    <nav
      aria-label="Zones"
      className="flex w-[50px] shrink-0 flex-col items-center gap-[3px] border-r border-line bg-rail pb-2.5 pt-[9px]"
    >
      <Link href="/overview" title="LabDog" className="mb-[9px] block">
        {/* eslint-disable-next-line @next/next/no-img-element -- static svg, no optimisation needed */}
        <img src="/logo.svg" alt="LabDog" width={26} height={26} className="block" />
      </Link>
      {ZONE_DEFS.map((z) => {
        const on = z.k === zone
        return (
          <button
            key={z.k}
            type="button"
            onClick={() => router.push(z.href)}
            title={z.label}
            aria-label={z.label}
            aria-current={on ? "page" : undefined}
            className="relative grid h-[34px] w-[38px] place-items-center rounded-md border-0"
            style={{ background: on ? "var(--accent-soft)" : "transparent", transition: "background var(--dur)" }}
          >
            <Glyph shape={z.glyph} on={on} />
            {z.k === "overview" && pendingTotal > 0 && <Badge n={pendingTotal} />}
            {on && <span className="absolute w-[2px] rounded-[2px] bg-ld-accent" style={{ left: -6, top: 8, bottom: 8 }} />}
          </button>
        )
      })}
      <div className="mt-auto flex flex-col items-center gap-1.5">
        <button type="button" onClick={onPalette} title="Command palette — ⌘K" aria-label="Command palette" className="btn btn-sm btn-ghost" style={{ padding: 6 }}>
          <span className="mono text-[10px]">⌘K</span>
        </button>
        <ThemeToggle />
        <button
          type="button"
          onClick={() => router.push(SETTINGS_ZONE.href)}
          title="Settings"
          aria-label="Settings"
          aria-current={zone === "settings" ? "page" : undefined}
          className="grid h-8 w-[38px] place-items-center rounded-md border-0"
          style={{ background: zone === "settings" ? "var(--accent-soft)" : "transparent" }}
        >
          <Glyph shape="gear" on={zone === "settings"} />
        </button>
        <AccountMenu />
      </div>
    </nav>
  )
}

/** The rail at phone width: five zones along the bottom edge. */
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
            <span className="mono text-[9px] font-semibold" style={{ color: on ? "var(--accent)" : "var(--text-3)" }}>
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
