"use client"

import { Modal } from "./modal"

export interface ConfirmProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void | Promise<void>
  variant?: "default" | "destructive"
  loading?: boolean
}

/**
 * The one confirmation modal: a title, a sentence, Cancel and the
 * action. Destructive actions get the danger button; while the action
 * runs the button says so and nothing can close the modal from under it.
 * In-place two-step buttons (the design's "Confirm — N hosts drop this
 * group") are for an object's own danger zone; a row's delete comes here.
 */
export function Confirm({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  variant = "default",
  loading = false,
}: ConfirmProps) {
  return (
    <Modal
      open={open}
      onClose={() => !loading && onOpenChange(false)}
      title={title}
      description={description}
      w={440}
      footer={
        <>
          <button type="button" className="btn ml-auto" disabled={loading} onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${variant === "destructive" ? "btn-danger" : "btn-primary"}`}
            disabled={loading}
            onClick={() => void onConfirm()}
          >
            {loading ? `${confirmLabel}…` : confirmLabel}
          </button>
        </>
      }
    >
      {null}
    </Modal>
  )
}
