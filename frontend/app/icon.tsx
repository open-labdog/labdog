import { ImageResponse } from "next/og"

export const dynamic = "force-static"
export const size = { width: 32, height: 32 }
export const contentType = "image/png"

/**
 * The favicon: the same mark as `public/logo.svg` — a timeline with a
 * restore point, and a U that turns back to it — drawn with the heavier
 * lines the mark needs at 16 and 32 pixels. Keep the two in step.
 */
export default function Icon() {
  const navy = "#1f2f3e"
  const cream = "#f5e9d4"
  const amber = "#d98f3f"
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex" }}>
        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 64 64">
          <rect width="64" height="64" rx="14" fill={navy} />
          <g transform="translate(4 2)">
            <path d="M8 13 H44" fill="none" stroke={cream} strokeWidth="6" strokeLinecap="round" />
            <path d="M44 13 V40 A12 12 0 0 1 20 40 V29" fill="none" stroke={amber} strokeWidth="7.5" strokeLinecap="round" />
            <path d="M20 21.5 L14 30 H26 Z" fill={amber} stroke={amber} strokeWidth="2" strokeLinejoin="round" />
            <circle cx="20" cy="13" r="6.5" fill={amber} stroke={navy} strokeWidth="2.5" />
            <circle cx="44" cy="13" r="4.6" fill={cream} stroke={navy} strokeWidth="2.5" />
          </g>
        </svg>
      </div>
    ),
    { ...size },
  )
}
