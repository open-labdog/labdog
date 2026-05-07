"use client"

import { useEffect, useRef } from "react"
import { Loader2Icon, AlertCircleIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import type { RepoScanResponse } from "@/lib/types"

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
    mutationFn: () =>
      apiFetch<RepoScanResponse>(`/api/git-repos/${repoId}/scan`, { method: "POST" }),
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

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
      {!scanMutation.error ? (
        <div
          data-testid="scan-step-loading"
          className="flex flex-col items-center gap-4 py-8"
        >
          <Loader2Icon className="h-8 w-8 animate-spin text-blue-500" />
          <div className="text-center">
            <p className="text-sm text-slate-200">
              Scanning <span className="font-medium text-white">{repoName}</span>…
            </p>
            <p className="text-xs text-slate-500 mt-1">
              Cloning the repository and looking for action packs and GitOps files.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => deleteMutation.mutate()}
            disabled={isDeleting}
          >
            {isDeleting ? "Cancelling..." : "Cancel & remove repo"}
          </Button>
        </div>
      ) : (
        <div data-testid="scan-step-error" className="flex flex-col items-start gap-4">
          <div className="flex items-start gap-2 text-red-400">
            <AlertCircleIcon className="h-5 w-5 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium">Scan failed</p>
              <p className="text-sm text-slate-400 mt-1 break-words">
                {scanMutation.error?.message ?? "Unknown error"}
              </p>
            </div>
          </div>
          <div className="flex gap-3">
            <Button
              type="button"
              onClick={() => {
                scanMutation.reset()
                scanMutation.mutate()
              }}
              disabled={scanMutation.isPending}
            >
              Retry
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => deleteMutation.mutate()}
              disabled={isDeleting}
            >
              {isDeleting ? "Cancelling..." : "Cancel & remove repo"}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
