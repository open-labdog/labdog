"use client"

import * as React from "react"
import { Select as SelectPrimitive } from "@base-ui/react/select"
import { CheckIcon } from "lucide-react"

import { cn } from "@/lib/utils"

function Select<V = string>({ ...props }: SelectPrimitive.Root.Props<V>) {
  return <SelectPrimitive.Root data-slot="select" {...props} />
}

function SelectTrigger({
  className,
  children,
  ...props
}: SelectPrimitive.Trigger.Props) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      className={cn(
        "flex h-8 w-full items-center justify-between rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 [&>span]:line-clamp-1",
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon>
        <svg className="size-4 opacity-50" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

function SelectValue({ ...props }: SelectPrimitive.Value.Props) {
  return <SelectPrimitive.Value data-slot="select-value" {...props} />
}

function SelectContent({
  className,
  children,
  ...props
}: SelectPrimitive.Positioner.Props & { className?: string }) {
  return (
    <SelectPrimitive.Portal>
      {/*
        The z-index belongs on the Positioner, not the Popup inside it.

        A Select portals to <body>, where it is a sibling of a Dialog's
        backdrop and popup (both z-50). The Positioner is `position:
        fixed`, and a fixed-position element always establishes a stacking
        context — so any z-index on the Popup is resolved *inside* that
        context and cannot lift it past anything outside. The Positioner
        itself then competes at `z-index: auto` and loses to the dialog,
        leaving the dropdown behind a `bg-black/10` blurred backdrop: it
        looks like a dim smudge next to the field rather than a menu.

        Raising the Positioner is what actually moves the subtree.
        Tooltips remain above at z-[100].
      */}
      {/*
        `alignItemWithTrigger` defaults to true, which overlaps the popup
        on the trigger so the *selected* item lines up with the trigger's
        text — native-macOS behaviour. In a form that means picking the
        second option makes the list cover the field above, which reads as
        a misplaced menu rather than an intentional one. Off gives the
        ordinary web behaviour: the list opens below the trigger.

        `sideOffset` matches the gap the other popovers use.
      */}
      <SelectPrimitive.Positioner
        className="z-[60]"
        alignItemWithTrigger={false}
        sideOffset={4}
        {...props}
      >
        <SelectPrimitive.Popup
          data-slot="select-content"
          className={cn(
            // Match the trigger's width rather than the content's, so the
            // menu lines up with the field instead of shrinking to fit the
            // longest label. --anchor-width is set by the positioner.
            "max-h-60 w-[var(--anchor-width)] min-w-[8rem] overflow-y-auto rounded-lg bg-background p-1 text-sm ring-1 ring-foreground/10 outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
            className
          )}
        >
          {children}
        </SelectPrimitive.Popup>
      </SelectPrimitive.Positioner>
    </SelectPrimitive.Portal>
  )
}

function SelectItem({
  className,
  children,
  ...props
}: SelectPrimitive.Item.Props) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "relative flex w-full cursor-default select-none items-center rounded-md py-1.5 pl-8 pr-2 text-sm outline-none data-highlighted:bg-muted data-highlighted:text-foreground data-disabled:pointer-events-none data-disabled:opacity-50",
        className
      )}
      {...props}
    >
      <span className="absolute left-2 flex size-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <CheckIcon className="size-4" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
}

export {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
}
