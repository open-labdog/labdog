"use client"

import { CheckIcon } from "lucide-react"

export type WizardStep = "auth" | "scanning" | "review"

const STEPS: { id: WizardStep; label: string }[] = [
  { id: "auth", label: "Connect" },
  { id: "scanning", label: "Scan" },
  { id: "review", label: "Review & activate" },
]

export function WizardStepIndicator({ current }: { current: WizardStep }) {
  const currentIdx = STEPS.findIndex((s) => s.id === current)
  return (
    <ol className="flex items-center gap-3" aria-label="Wizard progress">
      {STEPS.map((step, idx) => {
        const isDone = idx < currentIdx
        const isActive = idx === currentIdx
        const pipClass = isDone
          ? "bg-green-600 border-green-600 text-white"
          : isActive
          ? "bg-blue-600 border-blue-600 text-white"
          : "bg-slate-900 border-slate-700 text-slate-400"
        const labelClass = isActive ? "text-white" : isDone ? "text-slate-300" : "text-slate-500"
        return (
          <li
            key={step.id}
            data-step={step.id}
            data-active={isActive ? "true" : "false"}
            data-done={isDone ? "true" : "false"}
            className="flex items-center gap-2"
          >
            <span
              className={`inline-flex h-6 w-6 items-center justify-center rounded-full border text-xs tabular-nums ${pipClass}`}
              aria-current={isActive ? "step" : undefined}
            >
              {isDone ? <CheckIcon className="h-3.5 w-3.5" /> : idx + 1}
            </span>
            <span className={`text-sm ${labelClass}`}>{step.label}</span>
            {idx < STEPS.length - 1 && <span className="ml-1 h-px w-8 bg-slate-700" aria-hidden="true" />}
          </li>
        )
      })}
    </ol>
  )
}
