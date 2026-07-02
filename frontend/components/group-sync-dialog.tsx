"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { useSyncTray } from "@/lib/sync-tray"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { CardSkeleton } from "@/components/ui/skeleton"
import { ModuleDiffView } from "@/components/module-diff-view"
import type { Host, ModuleDiff } from "@/lib/types"

interface HostPreview {
  host: Host
  diffs: ModuleDiff[] | null
  error: string | null
}

function HostPreviewCard({ preview, multiModule }: { preview: HostPreview; multiModule: boolean }) {
  const { host, diffs, error } = preview
  const changed = diffs?.filter((d) => d.has_changes) ?? []
  const [open, setOpen] = useState(changed.length > 0 || !!error)

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-hidden">
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-800 transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-white">{host.hostname}</span>
          {error ? (
            <span className="text-xs text-red-400">preview failed</span>
          ) : changed.length === 0 ? (
            <span className="text-xs text-slate-500">no changes</span>
          ) : (
            <span className="text-xs text-amber-400">
              {changed.length} module{changed.length === 1 ? "" : "s"} changing
            </span>
          )}
        </div>
        <ChevronDown
          className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="border-t border-slate-700 p-3 space-y-2">
          {error ? (
            <div className="text-red-400 text-xs">{error}</div>
          ) : !diffs || diffs.length === 0 ? (
            <div className="text-slate-500 text-xs">Nothing configured for this host.</div>
          ) : (
            diffs.map((d) => (
              <ModuleDiffView
                key={d.module}
                diff={d}
                showHeader={multiModule}
                defaultExpanded={d.has_changes}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}

export function GroupSyncButton({
  groupId,
  moduleFilter,
  label,
  triggerLabel = "Sync",
  triggerVariant = "default",
  triggerSize = "sm",
}: {
  groupId: number
  /** Canonical module names, or null for every module ("Sync All"). */
  moduleFilter: string[] | null
  /** Dialog title + tray/toast label, e.g. "Sync Firewall Rules". */
  label: string
  triggerLabel?: string
  triggerVariant?: "default" | "outline" | "ghost"
  triggerSize?: "sm" | "default"
}) {
  const { registerSync } = useSyncTray()
  const [open, setOpen] = useState(false)
  const [previews, setPreviews] = useState<HostPreview[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)

  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })
  const groupHosts = (hosts ?? []).filter((h) => h.group_ids?.includes(groupId))

  const runPreview = async (hostsToPreview: Host[]) => {
    setLoading(true)
    setPreviews(null)
    setApplyError(null)
    try {
      const results = await Promise.all(
        hostsToPreview.map(async (host): Promise<HostPreview> => {
          try {
            const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${host.id}/preview`, {
              method: "POST",
              body: JSON.stringify({ module_filter: moduleFilter }),
            })
            return { host, diffs, error: null }
          } catch (e) {
            return { host, diffs: null, error: e instanceof Error ? e.message : "Preview failed" }
          }
        }),
      )
      setPreviews(results)
    } finally {
      setLoading(false)
    }
  }

  const openDialog = () => {
    setOpen(true)
    runPreview(groupHosts)
  }

  const hasChanges = previews?.some((p) => p.diffs?.some((d) => d.has_changes)) ?? false

  const apply = async () => {
    setApplying(true)
    setApplyError(null)
    try {
      const resp = await apiFetch<{ triggered_job_ids: number[]; skipped_host_ids: number[] }>(
        `/api/sync/groups/${groupId}/bulk`,
        { method: "POST", body: JSON.stringify({ module_filter: moduleFilter }) },
      )
      registerSync({ label, jobIds: resp.triggered_job_ids })
      setOpen(false)
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Apply failed")
    } finally {
      setApplying(false)
    }
  }

  return (
    <>
      <Button variant={triggerVariant} size={triggerSize} onClick={openDialog}>
        {triggerLabel}
      </Button>
      <Dialog open={open} onOpenChange={(o) => !applying && setOpen(o)}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{label} — Preview</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 max-h-[60vh] overflow-y-auto">
            {loading && <CardSkeleton />}
            {previews && !loading && (
              <>
                {groupHosts.length === 0 ? (
                  <div className="text-slate-400 text-sm">No hosts in this group.</div>
                ) : !hasChanges ? (
                  <div className="text-green-400 text-sm">All hosts are already in sync.</div>
                ) : null}
                {previews.map((p) => (
                  <HostPreviewCard key={p.host.id} preview={p} multiModule={moduleFilter === null} />
                ))}
              </>
            )}
            {applyError && (
              <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
                {applyError}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={applying}>
              Cancel
            </Button>
            <Button onClick={apply} disabled={applying || loading || !hasChanges}>
              {applying ? "Applying…" : "Apply Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
