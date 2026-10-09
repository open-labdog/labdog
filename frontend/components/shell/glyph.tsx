import type { ReactNode } from "react"
import type { GlyphShape } from "./zones"

/**
 * Zone glyphs: single-weight line icons drawn from each zone's menu
 * titles, so the rail reads as an instrument panel rather than a set of
 * abstract marks. One per zone — a gauge for Overview, two server rows
 * for Fleet, a terminal for Operations, a chat bubble with a spark for
 * Assistant. The utility row at the foot of the rail uses the same 1.7
 * stroke: a cog for Settings, a magnifier for the palette, a half-filled
 * circle for the theme toggle. Stroke colour follows the active state, so
 * they take the accent in either theme.
 */
const PATHS: Record<GlyphShape, (c: string) => ReactNode> = {
  ring: (c) => (
    <g fill="none" stroke={c} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 16a8 8 0 0 1 16 0" />
      <path d="M12 16l4.5-4" />
      <circle cx="12" cy="16" r="1.3" fill={c} stroke="none" />
      <path d="M4 16h1M19 16h1M6.5 9.5l.7.7M17.5 9.5l-.7.7" />
    </g>
  ),
  grid: (c) => (
    <g fill="none" stroke={c} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4.5" width="16" height="6" rx="1.6" />
      <rect x="4" y="13.5" width="16" height="6" rx="1.6" />
      <circle cx="7.4" cy="7.5" r=".95" fill={c} stroke="none" />
      <circle cx="7.4" cy="16.5" r=".95" fill={c} stroke="none" />
      <line x1="11" y1="7.5" x2="16.5" y2="7.5" />
      <line x1="11" y1="16.5" x2="16.5" y2="16.5" />
    </g>
  ),
  stack: (c) => (
    <g fill="none" stroke={c} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3.5" y="5" width="17" height="14" rx="2.2" />
      <path d="M7 10l2.6 2-2.6 2" />
      <line x1="12.5" y1="14.6" x2="16" y2="14.6" />
    </g>
  ),
  tri: (c) => (
    <g fill="none" stroke={c} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 7A2 2 0 0 1 6 5h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-8l-4 3.4V15H6A2 2 0 0 1 4 13z" />
      <path d="M13.2 8.4l.7 1.7 1.7.7-1.7.7-.7 1.7-.7-1.7-1.7-.7 1.7-.7z" fill={c} stroke="none" />
    </g>
  ),
  gear: (c) => (
    <g fill="none" stroke={c} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="6.3" strokeWidth="1.7" />
      <circle cx="12" cy="12" r="2.3" strokeWidth="1.7" />
      <path
        strokeWidth="2.4"
        d="M18.3 12h2M16.46 16.46l1.41 1.41M12 18.3v2M7.54 16.46l-1.41 1.41M5.7 12h-2M7.54 7.54L6.13 6.13M12 5.7v-2M16.46 7.54l1.41-1.41"
      />
    </g>
  ),
  search: (c) => (
    <g fill="none" stroke={c} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10.3" cy="10.3" r="5.8" />
      <line x1="14.6" y1="14.6" x2="19.5" y2="19.5" />
    </g>
  ),
  contrast: (c) => (
    <g fill="none" stroke={c} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="7.2" />
      <path d="M12 4.8a7.2 7.2 0 0 1 0 14.4z" fill={c} stroke="none" />
    </g>
  ),
}

export function Glyph({ shape, on, size = 15 }: { shape: GlyphShape; on?: boolean; size?: number }) {
  const c = on ? "var(--accent)" : "var(--text-3)"
  const px = size * 1.28
  return (
    <svg aria-hidden viewBox="0 0 24 24" width={px} height={px} style={{ flexShrink: 0, display: "block" }}>
      {PATHS[shape](c)}
    </svg>
  )
}
