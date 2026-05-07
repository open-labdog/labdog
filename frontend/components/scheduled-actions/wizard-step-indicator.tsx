"use client"

import { CheckIcon } from "lucide-react"

export type ScheduleStep = "picker" | "parameters" | "schedule" | "review"

const STEPS: { id: ScheduleStep; label: string }[] = [
  { id: "picker", label: "Action & target" },
  { id: "parameters", label: "Parameters" },
  { id: "schedule", label: "Schedule" },
  { id: "review", label: "Review" },
]

export function WizardStepIndicator({ current }: { current: ScheduleStep }) {
  const idx = STEPS.findIndex((s) => s.id === current)
  return (
    <ol className="flex items-center gap-3 flex-wrap" aria-label="Schedule wizard progress">
      {STEPS.map((step, i) => {
        const done = i < idx
        const active = i === idx
        const pip = done
          ? "bg-green-600 border-green-600 text-white"
          : active
          ? "bg-blue-600 border-blue-600 text-white"
          : "bg-slate-900 border-slate-700 text-slate-400"
        const label = active ? "text-white" : done ? "text-slate-300" : "text-slate-500"
        return (
          <li
            key={step.id}
            data-step={step.id}
            data-active={active ? "true" : "false"}
            data-done={done ? "true" : "false"}
            className="flex items-center gap-2"
          >
            <span
              className={`inline-flex h-6 w-6 items-center justify-center rounded-full border text-xs tabular-nums ${pip}`}
              aria-current={active ? "step" : undefined}
            >
              {done ? <CheckIcon className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className={`text-sm ${label}`}>{step.label}</span>
            {i < STEPS.length - 1 && (
              <span className="ml-1 h-px w-8 bg-slate-700" aria-hidden="true" />
            )}
          </li>
        )
      })}
    </ol>
  )
}
