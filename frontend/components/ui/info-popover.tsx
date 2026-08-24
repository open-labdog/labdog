"use client"

import * as React from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { InfoIcon } from "lucide-react"

import { cn } from "@/lib/utils"

interface InfoPopoverProps {
  /** Short heading — usually the field's own label. */
  title: string
  children: React.ReactNode
  className?: string
}

/**
 * A small info affordance beside a field label.
 *
 * Click-triggered rather than hover: the explanations here run to a couple of
 * sentences, which is more than a hover tooltip should hold, and a hover-only
 * control is unreachable on a touchscreen. Clicking also means the text stays
 * put while it is being read.
 */
function InfoPopover({ title, children, className }: InfoPopoverProps) {
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger
        render={
          <button
            type="button"
            aria-label={`About ${title}`}
            className={cn(
              "inline-flex size-4 shrink-0 items-center justify-center rounded-full text-slate-500 transition-colors hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              className
            )}
          />
        }
      >
        <InfoIcon className="size-3.5" />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        {/*
          z-index on the Positioner, not the Popup: the Positioner is
          `position: fixed` and so establishes a stacking context, which traps
          any z-index set on the popup inside it. These open inside a dialog
          (z-50), so they must sit above it.
        */}
        <PopoverPrimitive.Positioner sideOffset={6} className="z-[70]">
          <PopoverPrimitive.Popup className="max-w-xs rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-300 ring-1 ring-slate-700 shadow-lg outline-none">
            <p className="mb-1 font-medium text-slate-100">{title}</p>
            {children}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}

export { InfoPopover }
