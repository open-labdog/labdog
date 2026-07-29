"use client"

import { cn } from "@/lib/utils"

export interface SegmentedToggleOption<T extends string> {
  value: T
  label: string
}

/**
 * Compact two-(or-more)-option segmented control — a "simple toggle" for
 * choosing between a small number of clearly-named modes. Prefer this over
 * a bare on/off `Switch` whenever the two states need labels to be
 * unambiguous (e.g. "Per run" / "Grouped"), and over `Select` when there
 * are few enough options to show all of them at once at header scale.
 */
export function SegmentedToggle<T extends string>({
  value,
  onValueChange,
  options,
  className,
}: {
  value: T
  onValueChange: (value: T) => void
  options: SegmentedToggleOption<T>[]
  className?: string
}) {
  return (
    <div
      role="group"
      className={cn(
        "inline-flex h-7 items-center gap-0.5 rounded-md border border-slate-700 bg-slate-800 p-0.5",
        className
      )}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onValueChange(option.value)}
            className={cn(
              "h-6 rounded-[4px] px-2.5 text-xs font-medium transition-colors",
              active ? "bg-slate-600 text-white" : "text-slate-400 hover:text-slate-200"
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
