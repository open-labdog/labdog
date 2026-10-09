/**
 * The four-zone information architecture.
 *
 * The rail holds a fixed set of zones; each zone's pane lists its
 * destinations. Every route in the app maps to exactly one zone (see
 * `zoneForPath`) so the rail can show where you are on any page, not
 * only on the pane's own destinations.
 *
 * Config is not a zone. A module's desired state belongs to the group
 * that declares it, so it is edited on the group's own page (Config tab)
 * and a host's effective state on the host's page — the palette indexes
 * every module × group pair to keep that one keystroke away.
 */
export type ZoneKey = "overview" | "fleet" | "ops" | "assistant" | "settings"

export type GlyphShape = "ring" | "grid" | "stack" | "tri" | "gear" | "search" | "contrast"

/** Live numbers the pane and rail can show next to an item. */
export interface ShellCounts {
  hosts?: number
  groups?: number
  /** Discovered hosts awaiting approval. */
  pendingHosts?: number
  /** Assistant approval gates waiting on a click. */
  approvals?: number
  /** Alerts currently firing. */
  firingAlerts?: number
  /** Hosts whose sync status is out_of_sync. */
  drifted?: number
  schedules?: number
  /** The Overview badge: blocking + expiring only. */
  pendingBlocking?: number
}

export interface PaneItem {
  label: string
  href: string
  /** Neutral number after the label (a size, not a call to action). */
  n?: (c: ShellCounts) => number | undefined
  /** Attention badge — only for things that block or expire. */
  badge?: (c: ShellCounts) => number | undefined
}

export interface ZoneDef {
  k: ZoneKey
  label: string
  glyph: GlyphShape
  href: string
  items: PaneItem[]
}

const nz = (v: number | undefined) => (v && v > 0 ? v : undefined)

export const ZONE_DEFS: ZoneDef[] = [
  {
    k: "overview",
    label: "Overview",
    glyph: "ring",
    href: "/overview",
    items: [
      { label: "Summary", href: "/overview" },
      { label: "Pending", href: "/overview?view=pending", badge: (c) => nz(c.pendingBlocking) },
      { label: "Fleet state", href: "/overview?view=state" },
      { label: "Activity · 24h", href: "/overview?view=activity" },
      { label: "Upcoming", href: "/overview?view=upcoming", n: (c) => nz(c.schedules) },
    ],
  },
  {
    k: "fleet",
    label: "Fleet",
    glyph: "grid",
    href: "/hosts",
    items: [
      { label: "Hosts", href: "/hosts", n: (c) => c.hosts },
      { label: "Groups", href: "/groups", n: (c) => c.groups },
      { label: "Discovery", href: "/discovery", badge: (c) => nz(c.pendingHosts) },
    ],
  },
  {
    k: "ops",
    label: "Operations",
    glyph: "stack",
    href: "/plans",
    items: [
      { label: "Plans", href: "/plans" },
      { label: "Drift", href: "/drift", n: (c) => nz(c.drifted) },
      { label: "Actions", href: "/actions" },
      { label: "Runs", href: "/runs" },
      { label: "Audit", href: "/audit" },
    ],
  },
  {
    k: "assistant",
    label: "Assistant",
    glyph: "tri",
    href: "/assistant",
    items: [
      { label: "Sessions", href: "/assistant", badge: (c) => nz(c.approvals) },
      { label: "Alerts", href: "/alerts", n: (c) => nz(c.firingAlerts) },
    ],
  },
]

/** Settings sits at the bottom of the rail, not as a peer of the work. */
export const SETTINGS_ZONE: ZoneDef = {
  k: "settings",
  label: "Settings",
  glyph: "gear",
  href: "/settings",
  items: [],
}

/**
 * Route → zone. Longest prefix wins. The old URLs that now only redirect
 * (`/dashboard`, `/pending`, `/action-packs`, `/schedules`) stay listed
 * under the zone their content moved to, so the rail does not flicker to
 * Overview for the instant before the redirect lands.
 */
const ROUTE_ZONES: [string, ZoneKey][] = [
  ["/overview", "overview"],
  ["/dashboard", "overview"],
  ["/pending", "overview"],
  ["/hosts", "fleet"],
  ["/groups", "fleet"],
  ["/discovery", "fleet"],
  ["/plans", "ops"],
  ["/drift", "ops"],
  ["/actions", "ops"],
  ["/action-packs", "ops"],
  ["/schedules", "ops"],
  ["/runs", "ops"],
  ["/audit", "ops"],
  ["/assistant", "assistant"],
  ["/alerts", "assistant"],
  ["/settings", "settings"],
  ["/users", "settings"],
  ["/ssh-keys", "settings"],
  ["/git-repos", "settings"],
  ["/hypervisors", "settings"],
  ["/grafana", "settings"],
  ["/ai-providers", "settings"],
  ["/notifications", "settings"],
]

export function zoneForPath(pathname: string): ZoneKey {
  let best: [string, ZoneKey] | null = null
  for (const entry of ROUTE_ZONES) {
    const [prefix] = entry
    if ((pathname === prefix || pathname.startsWith(prefix + "/")) && (!best || prefix.length > best[0].length)) {
      best = entry
    }
  }
  return best ? best[1] : "overview"
}

export function zoneDef(k: ZoneKey): ZoneDef {
  return k === "settings" ? SETTINGS_ZONE : (ZONE_DEFS.find((z) => z.k === k) ?? ZONE_DEFS[0])
}

/** `/overview/` → `/overview`. `trailingSlash: true` gives every pathname one; item hrefs have none. */
function withoutTrailingSlash(path: string): string {
  return path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path
}

/**
 * Which pane item is the current page. Compares path, then the
 * `view`/`tab`/`section` param an item names. `siblings` are the pane's
 * other items: an item with no query is its path's default view, so it
 * gives way only to a sibling on the same path that names the view shown.
 */
export function itemIsActive(
  item: PaneItem,
  pathname: string,
  search: URLSearchParams,
  siblings: readonly PaneItem[] = [],
): boolean {
  const [href, itemQuery] = item.href.split("?")
  const itemPath = withoutTrailingSlash(href)
  const path = withoutTrailingSlash(pathname)
  // A page beneath the item (/groups/3?tab=config) belongs to the item's
  // default entry; its own params say nothing about sibling items.
  if (path !== itemPath) return itemQuery === undefined && path.startsWith(itemPath + "/")
  if (itemQuery === undefined) {
    return !siblings.some((s) => s !== item && s.href.includes("?") && itemIsActive(s, pathname, search))
  }
  const itemParams = new URLSearchParams(itemQuery)
  for (const key of ["view", "tab", "section"]) {
    const want = itemParams.get(key)
    if (want !== null) return search.get(key) === want
  }
  return true
}
