"use client"

/**
 * Trimmed, base-ui-compatible chart wrapper.
 *
 * This is a hand-port of the parts of shadcn/ui's `chart.tsx` this app
 * actually needs. The official shadcn version assumes Radix + shadcn's
 * default `--chart-*` CSS variables; this repo uses base-ui and the
 * dashboard chart spec hardcodes exact slate/green/amber hex tokens, so
 * this version intentionally has no Radix dependency and no reliance on
 * theme CSS variables beyond the ones it sets itself.
 */

import * as React from "react"
import { ResponsiveContainer, Tooltip } from "recharts"
import { cn } from "@/lib/utils"

/** Series metadata for a chart — label + hardcoded hex color. */
export type ChartConfig = Record<
  string,
  {
    label: string
    color: string
  }
>

interface ChartContainerProps extends React.ComponentProps<"div"> {
  config: ChartConfig
  children: React.ReactNode
}

/**
 * Styled wrapper around recharts' `ResponsiveContainer`. Exposes each
 * series' color as a `--color-<key>` CSS variable on the wrapper div
 * (not currently consumed by the hardcoded-hex chart components in this
 * app, but kept for parity with the shadcn `ChartContainer` API surface
 * and for any future chart that wants to reference `var(--color-x)`
 * from CSS instead of inline SVG props).
 */
function ChartContainer({ config, className, children, style, ...props }: ChartContainerProps) {
  const colorVars = Object.fromEntries(
    Object.entries(config).map(([key, value]) => [`--color-${key}`, value.color])
  )

  return (
    <div
      data-slot="chart-container"
      className={cn("h-full w-full", className)}
      style={{ ...colorVars, ...style } as React.CSSProperties}
      {...props}
    >
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  )
}

/** Re-export of recharts' `Tooltip` — no behavior change, just a stable import path. */
const ChartTooltip = Tooltip

interface ChartTooltipContentProps<TPoint = Record<string, unknown>> {
  /** Injected by recharts when used as `<Tooltip content={<ChartTooltipContent .../>} />`. */
  active?: boolean
  payload?: ReadonlyArray<{ payload?: TPoint }>
  label?: string | number
  /** Renders the tooltip's title line from the raw axis label + full data point. */
  renderLabel?: (label: string | number | undefined, point: TPoint) => React.ReactNode
  /** Renders the tooltip's body from the full data point (recharts' `payload[0].payload`). */
  renderBody: (point: TPoint) => React.ReactNode
  className?: string
}

/**
 * Custom tooltip content shell matching the dashboard chart spec's dark
 * floating-surface treatment. Callers supply `renderLabel` / `renderBody`
 * per-chart since the sync-rate and drift-trend tooltips have distinct,
 * fully custom line layouts (see `components/dashboard/*-chart.tsx`).
 */
function ChartTooltipContent<TPoint = Record<string, unknown>>({
  active,
  payload,
  label,
  renderLabel,
  renderBody,
  className,
}: ChartTooltipContentProps<TPoint>) {
  if (!active || !payload || payload.length === 0) return null
  const point = payload[0]?.payload
  if (!point) return null

  return (
    <div
      className={cn(
        "rounded-md border border-slate-700 bg-slate-900 px-3 py-2 shadow-lg text-xs space-y-0.5",
        className
      )}
    >
      {renderLabel && <p className="text-slate-300 font-medium">{renderLabel(label, point)}</p>}
      {renderBody(point)}
    </div>
  )
}

export { ChartContainer, ChartTooltip, ChartTooltipContent }
