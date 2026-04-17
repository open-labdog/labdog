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
        <TooltipPrimitive.Positioner side={side}>
          <TooltipPrimitive.Popup className="bg-slate-800 text-slate-200 border border-slate-700 rounded-md px-3 py-1.5 text-xs shadow-lg z-[100]">
            {content}
          </TooltipPrimitive.Popup>
        </TooltipPrimitive.Positioner>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

export { Tooltip }
