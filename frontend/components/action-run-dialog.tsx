"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { AlertTriangle } from "lucide-react"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ActionParameterForm } from "@/components/action-parameter-form"
import { apiFetch } from "@/lib/api"
import { nextCodename } from "@/lib/os-upgrade-paths"
import { toast } from "sonner"
import type {
  ActionDefinition,
  ActionRun,
  Host,
} from "@/lib/types"

interface ActionRunDialogProps {
  action: ActionDefinition | null
  scope: "host" | "group"
  targetId: number
  open: boolean
  onClose: () => void
  hostOsCodename?: string | null
}

export function ActionRunDialog({ action, scope, targetId, open, onClose, hostOsCodename }: ActionRunDialogProps) {
  const router = useRouter()
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [parallelism, setParallelism] = useState(1)
  const [submitting, setSubmitting] = useState(false)

  // Fetch group hosts to show OS codename context for linux-os-upgrade
  const { data: groupHosts } = useQuery<Host[]>({
    queryKey: ["group-hosts", targetId],
    queryFn: () => apiFetch<Host[]>(`/api/groups/${targetId}/hosts`),
    enabled: scope === "group" && open && action?.key === "linux-os-upgrade",
    staleTime: 30_000,
  })

  const uniqueCodenames = [...new Set(
    (groupHosts ?? []).map((h) => h.os_codename).filter((c): c is string => Boolean(c))
  )]
  const groupSingleCodename = uniqueCodenames.length === 1 ? uniqueCodenames[0] : null
  const groupMixedCodenames = uniqueCodenames.length > 1
  const groupCodenameCounts = uniqueCodenames.map((c) => ({
    codename: c,
    count: (groupHosts ?? []).filter((h) => h.os_codename === c).length,
  }))

  // Reset params when action changes
  // (only reset, don't initialize defaults here to keep state simple)

  if (!action) return null

  async function handleSubmit(dryRun = false) {
    if (!action) return
    setSubmitting(true)
    try {
      // Build params with defaults for unset bool fields
      const currentForPrepop = scope === "host" ? hostOsCodename : groupSingleCodename
      const resolvedParams: Record<string, unknown> = {}
      for (const p of action.parameters) {
        const val = params[p.key]
        if (val !== undefined) {
          resolvedParams[p.key] = val
        } else {
          // Pre-populate for linux-os-upgrade: current_version from host/group
          // codename; next_version derived from the static upgrade-path map.
          let prePopulated: string | undefined
          if (action.key === "linux-os-upgrade" && p.key === "current_version") {
            prePopulated = currentForPrepop ?? undefined
          } else if (action.key === "linux-os-upgrade" && p.key === "next_version") {
            prePopulated = nextCodename(currentForPrepop)
          }
          if (prePopulated !== undefined) {
            resolvedParams[p.key] = prePopulated
          } else if (p.default !== null && p.default !== undefined) {
            resolvedParams[p.key] = p.default
          }
        }
        if (p.required && resolvedParams[p.key] === undefined) {
          toast.error(`${p.label} is required`)
          setSubmitting(false)
          return
        }
      }
      if (dryRun) resolvedParams.__dry_run = true

      const body: Record<string, unknown> = {
        action_key: action.key,
        parameters: resolvedParams,
        dry_run: dryRun,
      }
      if (scope === "host") {
        body.host_id = targetId
      } else {
        body.group_id = targetId
        body.parallelism = parallelism
      }

      const run = await apiFetch<ActionRun>("/api/actions/runs", {
        method: "POST",
        json: body,
      })
      toast.success("Action started")
      onClose()
      // Navigate to run page
      const base = scope === "host" ? `/hosts/${targetId}` : `/groups/${targetId}`
      router.push(`${base}/actions/runs/${run.id}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start action"
      toast.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{action.name}</DialogTitle>
        </DialogHeader>

        {action.unresolved && (
          <div className="rounded-lg border border-amber-800 bg-amber-950/40 p-3 text-sm space-y-1.5">
            <p className="flex items-center gap-2 text-amber-200 font-medium">
              <AlertTriangle className="h-4 w-4" />
              Unresolved — pick a winner first
            </p>
            <p className="text-amber-300/80 text-xs">
              Multiple packs declare action key{" "}
              <code className="font-mono">{action.key}</code>. Choose which
              pack wins on{" "}
              <Link
                href="/action-packs"
                className="underline hover:text-amber-200"
                onClick={onClose}
              >
                /action-packs
              </Link>
              {" "}before running.
            </p>
          </div>
        )}

        {scope === "group" && action.key === "linux-os-upgrade" && (
          <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-3 text-sm">
            {groupMixedCodenames ? (
              <div className="text-amber-400">
                <span className="font-medium">Mixed OS versions detected:</span>{" "}
                {groupCodenameCounts.map(({ codename, count }) => `${codename} (${count})`).join(", ")}
                {". "}Verify <em>Current codename</em> is correct before running.
              </div>
            ) : groupSingleCodename ? (
              <p className="text-slate-400">
                All hosts are on <span className="font-mono text-slate-200">{groupSingleCodename}</span>.
              </p>
            ) : groupHosts !== undefined ? (
              <p className="text-slate-500 italic">OS facts not yet collected for hosts in this group.</p>
            ) : null}
          </div>
        )}

        <ActionParameterForm
          action={action}
          values={params}
          onChange={setParams}
          placeholderFor={(p) => {
            const current = scope === "host" ? hostOsCodename : groupSingleCodename
            if (action.key === "linux-os-upgrade" && p.key === "current_version") {
              return current ?? undefined
            }
            if (action.key === "linux-os-upgrade" && p.key === "next_version") {
              return nextCodename(current)
            }
            return undefined
          }}
        />

        {scope === "group" && (
          <div className="space-y-1.5">
            <Label className="text-sm font-medium text-slate-200">Parallelism</Label>
            <select
              value={parallelism}
              onChange={(e) => setParallelism(Number(e.target.value))}
              className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white"
            >
              <option value={-1}>All at once</option>
              <option value={1}>Rolling — 1 at a time</option>
              <option value={2}>Rolling — 2 at a time</option>
              <option value={5}>Rolling — 5 at a time</option>
            </select>
          </div>
        )}

        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => handleSubmit(true)}
            disabled={submitting || action.unresolved}
          >
            Preview (dry-run)
          </Button>
          <Button
            onClick={() => handleSubmit(false)}
            disabled={submitting || action.unresolved}
          >
            {submitting ? "Starting…" : "Run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
