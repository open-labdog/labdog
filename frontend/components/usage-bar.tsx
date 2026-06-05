/** Green/amber/red thresholds for resource usage. Single source of truth
 *  so the tiles and bars stay consistent and the cut-offs are tunable. */
export function usageTone(pct: number): { text: string; bar: string } {
  if (pct >= 90) return { text: "text-red-400", bar: "bg-red-500" }
  if (pct >= 75) return { text: "text-amber-400", bar: "bg-amber-500" }
  return { text: "text-green-400", bar: "bg-green-500" }
}

/** A slim static usage fill — the only "chart" we draw (no time series). */
export function UsageBar({ value }: { value: number }) {
  const clamped = Math.min(100, Math.max(0, value))
  const { bar } = usageTone(value)
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={`h-full rounded-full ${bar}`} style={{ width: `${clamped}%` }} />
    </div>
  )
}
