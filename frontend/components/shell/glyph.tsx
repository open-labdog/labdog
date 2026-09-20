import type { CSSProperties } from "react"
import type { GlyphShape } from "./zones"

/**
 * Zone glyphs drawn from borders and boxes rather than an icon font, so
 * they take the accent colour when active and read at 15px in either
 * theme. One shape per zone: a ring for the whole (Overview), a grid of
 * things (Fleet), a diamond for declared state (Config), a stack of
 * events (Operations), a triangle for the newest surface (Assistant), a
 * gear at the bottom of the rail.
 */
export function Glyph({ shape, on, size = 15 }: { shape: GlyphShape; on?: boolean; size?: number }) {
  const c = on ? "var(--accent)" : "var(--text-3)"
  const st: CSSProperties = { width: size, height: size, flexShrink: 0 }
  if (shape === "ring")
    return (
      <div
        aria-hidden
        style={{ ...st, border: `1.6px solid ${c}`, borderRadius: "50%", boxShadow: on ? "inset 0 0 0 2.5px var(--accent-soft)" : "none" }}
      />
    )
  if (shape === "diamond")
    return <div aria-hidden style={{ ...st, border: `1.6px solid ${c}`, borderRadius: 2, transform: "rotate(45deg) scale(0.82)" }} />
  if (shape === "stack")
    return (
      <div aria-hidden style={{ ...st, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{ height: 2.6, background: c, borderRadius: 1, width: i === 2 ? "62%" : "100%" }} />
        ))}
      </div>
    )
  if (shape === "tri")
    return (
      <div
        aria-hidden
        style={{
          width: 0,
          height: 0,
          borderLeft: `${size / 2}px solid transparent`,
          borderRight: `${size / 2}px solid transparent`,
          borderBottom: `${size * 0.85}px solid ${c}`,
          flexShrink: 0,
        }}
      />
    )
  if (shape === "gear")
    return (
      <div aria-hidden style={{ ...st, border: `1.6px solid ${c}`, borderRadius: 3, position: "relative" }}>
        <div style={{ position: "absolute", inset: 3, border: `1.6px solid ${c}`, borderRadius: "50%" }} />
      </div>
    )
  return (
    <div aria-hidden style={{ ...st, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2 }}>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} style={{ background: c, borderRadius: 1 }} />
      ))}
    </div>
  )
}
