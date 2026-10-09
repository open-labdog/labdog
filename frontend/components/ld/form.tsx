"use client"

import type { CSSProperties, ReactNode } from "react"

/* ── forms ──────────────────────────────────────────────────────── */

/**
 * A labelled control the way the design writes one: a `.tt` caption over
 * the control, an optional hint in the caption's own line, and the error
 * where the hint would be. The wrapper is a `<label>` so clicking the
 * caption focuses the control and `getByLabel` finds it; use `as="div"`
 * for composite controls (a picker with its own buttons) where a label
 * would forward clicks it should not.
 */
export function Field({
  label,
  hint,
  error,
  children,
  as = "label",
  htmlFor,
  className,
  style,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  children: ReactNode
  as?: "label" | "div"
  htmlFor?: string
  className?: string
  style?: CSSProperties
}) {
  const Wrap = as
  return (
    <Wrap
      className={`field flex min-w-0 flex-col gap-1 ${className ?? ""}`}
      data-invalid={error ? "true" : undefined}
      style={style}
      {...(as === "label" ? { htmlFor } : null)}
    >
      <span className="tt flex flex-wrap items-baseline gap-1.5">
        {label}
        {hint && !error && <span className="normal-case tracking-normal text-text-faint">{hint}</span>}
        {error && (
          <span className="normal-case tracking-normal text-danger" role="alert">
            · {error}
          </span>
        )}
      </span>
      {children}
    </Wrap>
  )
}

/**
 * A disclosure for the paragraph that would have been a tooltip or a
 * popover. Inside a modal a popover would sit under the modal's own
 * layer; a `<details>` is in the flow, keyboard-reachable and prints.
 */
export function Help({ summary = "why", children }: { summary?: ReactNode; children: ReactNode }) {
  return (
    <details className="text-[11px] leading-[1.5] text-text-3">
      <summary className="cursor-pointer select-none text-text-faint hover:text-text-2">{summary}</summary>
      <div className="mt-1">{children}</div>
    </details>
  )
}
