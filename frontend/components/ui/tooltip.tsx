"use client"

import * as React from "react"
import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip"

interface TooltipProps {
  content: string
  children: React.ReactNode
  side?: "top" | "bottom" | "left" | "right"
}

function Tooltip({ content, children, side = "top" }: TooltipProps) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger delay={200} render={<span />}>
        {children}
      </TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        {/* z-index on the Positioner, not the Popup — see the note in
            select.tsx. The Positioner is `position: fixed` and so
            establishes a stacking context, which traps any z-index set on
            the popup inside it. Not currently visibly broken (no tooltip
            sits over a dialog today) but it is the same defect. */}
        <TooltipPrimitive.Positioner side={side} className="z-[100]">
          <TooltipPrimitive.Popup className="bg-slate-800 text-slate-200 border border-slate-700 rounded-md px-3 py-1.5 text-xs shadow-lg">
            {content}
          </TooltipPrimitive.Popup>
        </TooltipPrimitive.Positioner>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

export { Tooltip }
