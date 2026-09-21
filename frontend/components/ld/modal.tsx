"use client"

import type { FormEvent, ReactNode } from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"

/**
 * The design's modal — title + meta + esc in the header, a scrolling body,
 * an optional footer strip — built on the base-ui dialog so it keeps the
 * focus trap, the Escape handling and the aria wiring the shadcn Dialog
 * has. Sits at 7vh from the top rather than dead-centre so a tall modal
 * grows downwards instead of jumping.
 *
 * `onSubmit` turns the whole popup into a form — header, body and footer
 * — so a `type="submit"` button in the footer and Enter in a field both
 * submit; `form.handleSubmit(save)` drops straight in. `description` is
 * the sentence a screen reader announces with the title.
 */
export function Modal({
  title,
  meta,
  description,
  onClose,
  onSubmit,
  children,
  footer,
  w = 620,
  open = true,
}: {
  title: ReactNode
  meta?: ReactNode
  description?: ReactNode
  onClose: () => void
  onSubmit?: (e: FormEvent<HTMLFormElement>) => void
  children: ReactNode
  footer?: ReactNode
  w?: number
  open?: boolean
}) {
  const inner = (
    <>
      <header className="flex items-center gap-2.5 border-b border-line bg-surface-2 px-[13px] py-[11px]">
        <DialogPrimitive.Title className="m-0 text-[13.5px] font-semibold">{title}</DialogPrimitive.Title>
        {meta && <span className="mono text-[10.5px] text-text-faint">{meta}</span>}
        <DialogPrimitive.Close type="button" className="btn btn-sm btn-ghost ml-auto">
          esc
        </DialogPrimitive.Close>
      </header>
      <div className="scroll flex min-h-0 flex-col gap-[11px] p-[13px]">
        {description && <DialogPrimitive.Description className="m-0 text-[12.5px] leading-[1.55] text-text-2">{description}</DialogPrimitive.Description>}
        {children}
      </div>
      {footer && (
        <footer className="flex items-center gap-2 border-t border-line bg-surface-2 px-[13px] py-2.5">{footer}</footer>
      )}
    </>
  )
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fade fixed inset-0 z-[90]" style={{ background: "var(--scrim)" }} />
        <DialogPrimitive.Popup
          className="fade fixed left-1/2 top-[7vh] z-[91] flex max-h-[86vh] w-[calc(100%-32px)] -translate-x-1/2 flex-col overflow-hidden rounded-r-lg border border-line-strong bg-surface shadow-ld outline-none"
          style={{ maxWidth: w }}
        >
          {onSubmit ? (
            <form className="contents" noValidate onSubmit={onSubmit}>
              {inner}
            </form>
          ) : (
            inner
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
