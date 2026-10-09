"use client"

import { Banner, Modal } from "@/components/ld"
import { ModuleDiffView, moduleLabel } from "@/components/module-diff-view"
import type { ModuleDiff } from "@/lib/types"

export interface SyncPreviewState {
  scope: "module" | "all"
  tabKey: string | null
  module: string | null
  loading: boolean
  error: string | null
  diffs: ModuleDiff[] | null
}

export function SyncPreviewDialog({
  preview,
  applying,
  applyError,
  onClose,
  onApply,
}: {
  preview: SyncPreviewState
  applying: boolean
  applyError: string | null
  onClose: () => void
  onApply: () => void
}) {
  const noChanges = !!preview.diffs && !preview.loading && !preview.error && !preview.diffs.some((d) => d.has_changes)

  return (
    <Modal
      title={preview.scope === "all" ? "Sync all — preview" : `Sync ${moduleLabel(preview.module ?? "")} — preview`}
      w={760}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ml-auto" disabled={applying} onClick={onClose}>{noChanges ? "Close" : "Cancel"}</button>
          {!noChanges && (
            <button type="button" className="btn btn-primary" disabled={applying || preview.loading || !!preview.error || !preview.diffs} onClick={onApply}>
              {applying ? "Applying…" : "Apply changes"}
            </button>
          )}
        </>
      }
    >
      {preview.loading && <span className="text-[11.5px] text-text-3">Computing the diff…</span>}
      {preview.error && <Banner tone="danger">{preview.error}</Banner>}
      {preview.diffs && !preview.loading && (
        <div className="flex flex-col gap-2">
          {noChanges && <Banner tone="ok">Everything is already in sync.</Banner>}
          {[...preview.diffs]
            .sort((a, b) => Number(b.has_changes) - Number(a.has_changes))
            .map((d) => (
              <ModuleDiffView key={d.module} diff={d} showHeader={preview.scope === "all"} defaultExpanded={d.has_changes} />
            ))}
        </div>
      )}
      {applyError && <Banner tone="danger">{applyError}</Banner>}
    </Modal>
  )
}
