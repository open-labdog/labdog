"use client"

import { useEffect, useRef } from "react"
import { Banner, Dot, Panel } from "@/components/ld"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import type { RepoScanResponse } from "@/lib/types"

/** Step 2: the clone-and-scan, started on mount; cancelling removes the
 *  repository that step 1 created so a failed attempt leaves nothing behind. */
export function ScanStep({
  repoId,
  repoName,
  onScanned,
  onCancelled,
}: {
  repoId: number
  repoName: string
  onScanned: (result: RepoScanResponse) => void
  onCancelled: () => void
}) {
  const scanMutation = useApiMutation<RepoScanResponse, void>({
    mutationFn: () => apiFetch<RepoScanResponse>(`/api/git-repos/${repoId}/scan`, { method: "POST" }),
    onSuccess: (data) => onScanned(data),
  })

  const deleteMutation = useApiMutation<unknown, void>({
    mutationFn: () => apiFetch(`/api/git-repos/${repoId}`, { method: "DELETE" }),
    invalidateKeys: [["git-repos"]],
    onSuccess: () => onCancelled(),
  })

  const startedRef = useRef(false)
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    scanMutation.mutate()
    // Run once on mount; mutation reference is stable enough for a one-shot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const isDeleting = deleteMutation.isPending
  const cancel = (
    <button type="button" className="btn btn-sm" onClick={() => deleteMutation.mutate()} disabled={isDeleting}>
      {isDeleting ? "Cancelling…" : "Cancel & remove repo"}
    </button>
  )

  return (
    <Panel title="scan" meta={`step 2 · ${repoName}`}>
      {!scanMutation.error ? (
        <div data-testid="scan-step-loading" className="flex flex-col items-center gap-3 px-[13px] py-8 text-center">
          <span className="inline-flex items-center gap-2 text-xs text-text" style={{ color: "var(--sync)" }}>
            <Dot tone="sync" pulse />
            scanning <span className="mono text-text">{repoName}</span>…
          </span>
          <span className="max-w-[360px] text-[11.5px] text-text-3">Cloning the repository and looking for action packs and GitOps files.</span>
          {cancel}
        </div>
      ) : (
        <div data-testid="scan-step-error" className="flex flex-col gap-3 p-[13px]">
          <Banner tone="danger">
            <span className="font-medium">Scan failed.</span> {scanMutation.error?.message ?? "Unknown error"}
          </Banner>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => {
                scanMutation.reset()
                scanMutation.mutate()
              }}
              disabled={scanMutation.isPending}
            >
              Retry
            </button>
            {cancel}
          </div>
        </div>
      )}
    </Panel>
  )
}
