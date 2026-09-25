"use client"

import type { ReactNode } from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"

/**
 * The design's modal — title + meta + esc in the header, a scrolling body,
 * an optional footer strip — built on the base-ui dialog so it keeps the
 * focus trap, the Escape handling and the aria wiring the shadcn Dialog
 * has. Sits at 7vh from the top rather than dead-centre so a tall modal
 * grows downwards instead of jumping.
 */
export function Modal({
  title,
  meta,
  onClose,
  children,
  footer,
  w = 620,
  open = true,
}: {
  title: ReactNode
  meta?: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  w?: number
  open?: boolean
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fade fixed inset-0 z-[90]" style={{ background: "var(--scrim)" }} />
        <DialogPrimitive.Popup
          className="fade fixed left-1/2 top-[7vh] z-[91] flex max-h-[86vh] w-[calc(100%-32px)] -translate-x-1/2 flex-col overflow-hidden rounded-r-lg border border-line-strong bg-surface shadow-ld outline-none"
          style={{ maxWidth: w }}
        >
          <header className="flex items-center gap-2.5 border-b border-line bg-surface-2 px-[13px] py-[11px]">
            <DialogPrimitive.Title className="m-0 text-[13.5px] font-semibold">{title}</DialogPrimitive.Title>
            {meta && <span className="mono text-[10.5px] text-text-faint">{meta}</span>}
            <DialogPrimitive.Close className="btn btn-sm btn-ghost ml-auto">esc</DialogPrimitive.Close>
          </header>
          <div className="scroll flex min-h-0 flex-col gap-[11px] p-[13px]">{children}</div>
          {footer && (
            <footer className="flex items-center gap-2 border-t border-line bg-surface-2 px-[13px] py-2.5">{footer}</footer>
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
