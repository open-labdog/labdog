"use client"

import { useCallback, useEffect, useState } from "react"
import { usePathname } from "next/navigation"
import { useTheme } from "next-themes"
import { can, useAuth } from "@/lib/auth"
import { useViewportWidth } from "@/hooks/use-viewport"
import { MobileRail, RAIL_W, RAIL_W_EXPANDED, Rail, ThemeToggle } from "@/components/shell/rail"
import { useLocalPref } from "@/lib/local-pref"
import { Pane } from "@/components/shell/pane"
import { Palette } from "@/components/shell/palette"
import { AccountMenu } from "@/components/shell/account-menu"
import { ForcedPasswordChange } from "@/components/shell/forced-password-change"
import { useShellCounts } from "@/components/shell/use-shell-counts"
import { zoneDef, zoneForPath } from "@/components/shell/zones"

const AUTH_ROUTES = ["/login", "/register"]

/** Below this the pane overlays the content instead of sitting beside it. */
const NARROW = 1180
/** Below this the rail moves to the bottom edge and the pane goes away. */
const MOBILE = 640

/**
 * Pattern A: icon rail (four zones, fixed cost) + contextual pane +
 * content. At 1024 the pane becomes an overlay; at 390 the rail is a
 * bottom tab bar. Keyboard: ⌘K palette, `[` toggles the pane, `]`
 * expands or collapses the rail (icons only ↔ icons with names), `t`
 * toggles the theme.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const isAuthPage = AUTH_ROUTES.some((r) => pathname.startsWith(r))
  const { user } = useAuth()
  const { resolvedTheme, setTheme } = useTheme()
  const w = useViewportWidth()
  const mobile = w < MOBILE
  const narrow = w < NARROW
  const zone = zoneForPath(pathname)
  const hasPane = zoneDef(zone).items.length > 0

  // The pane is open beside the content when there is room and closed
  // when there is not; the operator's toggle overrides that, but the
  // override only lasts while its context does — crossing the narrow
  // breakpoint resets it, and an overlay pane closes on navigation while a
  // docked one stays. Deriving it this way needs no effect.
  const paneKey = narrow ? `overlay:${pathname}` : "docked"
  const [paneOverride, setPaneOverride] = useState<{ key: string; open: boolean } | null>(null)
  const paneOpen = paneOverride?.key === paneKey ? paneOverride.open : !narrow
  const setPaneOpen = useCallback(
    (v: boolean | ((prev: boolean) => boolean)) =>
      setPaneOverride((o) => {
        const prev = o?.key === paneKey ? o.open : !narrow
        return { key: paneKey, open: typeof v === "function" ? v(prev) : v }
      }),
    [paneKey, narrow],
  )
  const [palette, setPalette] = useState(false)
  // Expanded or icon-only rail: a viewer convenience, remembered per browser.
  const [railPref, setRailPref] = useLocalPref("labdog.rail", { expanded: false })
  const railExpanded = railPref.expanded
  const toggleRail = useCallback(() => setRailPref({ expanded: !railExpanded }), [setRailPref, railExpanded])
  const railW = railExpanded ? RAIL_W_EXPANDED : RAIL_W
  // Shell queries only run once someone is signed in and off the auth pages,
  // and not while a forced password change refuses every one of them.
  const signedIn = !isAuthPage && !!user && !user.must_change_password
  const counts = useShellCounts(signedIn, can(user, "ai.use"))

  const toggleTheme = useCallback(
    () => setTheme(resolvedTheme === "light" ? "dark" : "light"),
    [resolvedTheme, setTheme],
  )

  useEffect(() => {
    if (isAuthPage) return
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setPalette((p) => !p)
        return
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === "[") setPaneOpen((p) => !p)
      if (e.key === "]") toggleRail()
      if (e.key === "t") toggleTheme()
    }
    window.addEventListener("keydown", h)
    return () => window.removeEventListener("keydown", h)
  }, [isAuthPage, toggleTheme, setPaneOpen, toggleRail])

  if (isAuthPage) {
    return <>{children}</>
  }

  const showPane = !mobile && hasPane && paneOpen

  return (
    <div className={`relative flex h-full overflow-hidden ${mobile ? "flex-col" : "flex-row"}`}>
      {!mobile && (
        <Rail
          zone={zone}
          counts={counts}
          onPalette={() => setPalette(true)}
          expanded={railExpanded}
          onToggleExpanded={toggleRail}
        />
      )}

      {showPane && <Pane zone={zone} counts={counts} overlay={narrow} left={railW} onClose={() => setPaneOpen(false)} />}
      {showPane && narrow && (
        <div className="absolute inset-0 z-20" style={{ left: railW, background: "var(--scrim)" }} onClick={() => setPaneOpen(false)} aria-hidden />
      )}
      {!mobile && hasPane && !paneOpen && (
        <button
          type="button"
          onClick={() => setPaneOpen(true)}
          title="Open pane — ["
          aria-label="Open pane"
          className="w-[13px] shrink-0 border-0 border-r border-line bg-surface text-[9px] text-text-faint"
        >
          ›
        </button>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-bg">
        {mobile && (
          <header className="flex shrink-0 items-center gap-[9px] border-b border-line bg-surface px-3 py-[9px]">
            {/* eslint-disable-next-line @next/next/no-img-element -- static svg, no optimisation needed */}
            <img src="/logo.svg" alt="unbork" width={22} height={22} className="block" />
            <span className="text-[12.5px] font-semibold">{zoneDef(zone).label}</span>
            <button type="button" className="btn btn-sm btn-ghost ml-auto" onClick={() => setPalette(true)} aria-label="Search">
              search
            </button>
            <ThemeToggle compact />
            <AccountMenu compact />
          </header>
        )}
        {/* Every screen owns its header and scroll region (PageHead + a
            `.scroll` body), so the shell hands over the whole column. */}
        {/* Nothing behind the forced password change would load: every API
            call is refused until it is done. */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">{user?.must_change_password ? null : children}</main>
        <ForcedPasswordChange />
      </div>

      {mobile && <MobileRail zone={zone} counts={counts} />}
      <Palette open={palette} onClose={() => setPalette(false)} />
    </div>
  )
}
