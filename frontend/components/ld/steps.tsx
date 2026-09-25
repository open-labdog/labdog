"use client"

import { Fragment } from "react"
import { Dot } from "./atoms"

/**
 * Where a multi-step flow is: numbered `.tt` labels with a dot per step
 * — done in ok, current in accent (pulsing), ahead faint. Carries
 * `data-step` / `data-active` / `data-done` so a test can read the
 * state without parsing colours.
 */
export function Steps<K extends string>({ steps, current, className }: { steps: { k: K; label: string }[]; current: K; className?: string }) {
  const at = steps.findIndex((s) => s.k === current)
  return (
    <ol className={`m-0 flex list-none flex-wrap items-center gap-3 p-0 ${className ?? ""}`} aria-label="progress">
      {steps.map((s, i) => {
        const done = i < at
        const active = i === at
        return (
          <Fragment key={s.k}>
            <li
              data-step={s.k}
              data-active={active ? "true" : "false"}
              data-done={done ? "true" : "false"}
              aria-current={active ? "step" : undefined}
              className="flex items-center gap-1.5"
            >
              <Dot tone={done ? "ok" : active ? "accent" : "idle"} pulse={active} />
              <span className="tt" style={{ color: active ? "var(--text)" : done ? "var(--text-2)" : "var(--text-faint)" }}>
                {i + 1} {s.label}
              </span>
            </li>
            {i < steps.length - 1 && <span aria-hidden className="h-px w-6 bg-line" />}
          </Fragment>
        )
      })}
    </ol>
  )
}
