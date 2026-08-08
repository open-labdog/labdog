"use client"

import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible"

import { cn } from "@/lib/utils"

/**
 * Disclosure primitive over base-ui's Collapsible.
 *
 * Worth wrapping rather than hand-rolling: the two hand-rolled disclosures
 * already in this codebase (`module-diff-view.tsx`, the group rows in
 * `recent-scheduled-runs-panel.tsx`) between them carry no `aria-expanded`
 * and no `aria-controls`, so a screen reader is told nothing about the
 * state it is toggling. The primitive wires both itself and renders a real
 * `<button>`, so keyboard operation comes free too.
 *
 * The trigger exposes `data-panel-open`, which Tailwind 4 can style
 * directly — `data-panel-open:rotate-180` on a chevron needs no state of
 * its own.
 */
function Collapsible({ ...props }: CollapsiblePrimitive.Root.Props) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />
}

function CollapsibleTrigger({ className, ...props }: CollapsiblePrimitive.Trigger.Props) {
  return (
    <CollapsiblePrimitive.Trigger
      data-slot="collapsible-trigger"
      className={cn(
        "flex w-full items-center justify-between gap-2 rounded-md text-left",
        "outline-none focus-visible:ring-2 focus-visible:ring-blue-500",
        className
      )}
      {...props}
    />
  )
}

function CollapsiblePanel({ className, ...props }: CollapsiblePrimitive.Panel.Props) {
  return (
    <CollapsiblePrimitive.Panel
      data-slot="collapsible-panel"
      className={cn("overflow-hidden", className)}
      {...props}
    />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsiblePanel }
