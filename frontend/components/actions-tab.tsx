"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { shortAgo } from "@/lib/fleet"
import { syncModuleLabel } from "@/lib/modules"
import { Panel, RunStatus, Table, Tag } from "@/components/ld"
import { ActionRunDialog } from "@/components/action-run-dialog"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import type { ActionDefinition, ActionRun, Host } from "@/lib/types"

interface ActionsTabProps {
  scope: "host" | "group"
  targetId: number
  host?: Host
}

function formatDuration(run: ActionRun): string {
  if (!run.started_at || !run.finished_at) return "—"
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export function ActionsTab({ scope, targetId, host }: ActionsTabProps) {
  const router = useRouter()
  const [selectedAction, setSelectedAction] = useState<ActionDefinition | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [scheduleAction, setScheduleAction] = useState<ActionDefinition | null>(null)

  const { data: catalog, isLoading: catalogLoading } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 60_000,
  })

  const scopeParam = scope === "host" ? `host_id=${targetId}` : `group_id=${targetId}`
  const { data: runs, isLoading: runsLoading } = useQuery<ActionRun[]>({
    queryKey: ["action-runs", scope, targetId],
    queryFn: () => apiFetch<ActionRun[]>(`/api/actions/runs?${scopeParam}&limit=20`),
    refetchInterval: (query) => {
      const data = query.state.data as ActionRun[] | undefined
      return data?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false
    },
  })

  const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
  // Which host ids this component instance has already asked to refresh.
  // Collection is asynchronous: the POST queues a Celery job and
  // os_facts_collected_at does not move until that job lands, so the
  // staleness test below stays true and the effect re-fired on every
  // mount of the tab — one extra SSH round trip per navigation until the
  // worker caught up. The ref makes the dispatch once-per-host instead.
  const factsRefreshRequested = useRef<Set<number>>(new Set())
  useEffect(() => {
    if (scope !== "host" || !host) return
    if (factsRefreshRequested.current.has(targetId)) return
    const stale = !host.os_facts_collected_at || Date.now() - new Date(host.os_facts_collected_at).getTime() > SEVEN_DAYS_MS
    if (stale) {
      factsRefreshRequested.current.add(targetId)
      apiFetch(`/api/hosts/${targetId}/facts/refresh`, { method: "POST" }).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, host?.id, host?.os_facts_collected_at])

  // Built-in pseudo-actions (sync / drift_check / collect_state) are
  // dispatched from their own UI surfaces and never appear in the
  // catalog here; the schedule dialog still surfaces them.
  const filteredCatalog = (catalog ?? []).filter((a) => !a.key.startsWith("_builtin.") && (scope === "host" ? a.supports_host : a.supports_group))
  const runsBasePath = scope === "host" ? `/hosts/${targetId}` : `/groups/${targetId}`

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-5">
        <Panel title="available actions" className="lg:col-span-3" pad={0}>
          <Table<ActionDefinition>
            cols={[
              {
                k: "name", label: "action", w: "minmax(140px,1.2fr)", sortable: false,
                cell: (a) => (
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="mono trunc font-medium text-text">{a.name}</span>
                    <Tag>{a.pack_name}</Tag>
                    {a.unresolved && <Tag tone="hold" title={`contested by ${a.overridden_from.join(", ")} — pick a winner on /actions?tab=packs`}>unresolved</Tag>}
                    {a.post_run_sync.length > 0 && <Tag title={`re-syncs ${a.post_run_sync.map(syncModuleLabel).join(", ")} afterwards`}>+sync</Tag>}
                  </span>
                ),
              },
              { k: "desc", label: "what it does", w: "minmax(120px,1.4fr)", sortable: false, cell: (a) => <span className="trunc text-[11px] text-text-3" title={a.description}>{a.description}</span> },
              {
                k: "actions", label: "", w: "150px", right: true, sortable: false,
                cell: (a) => (
                  <span className="flex items-center justify-end gap-1.5">
                    <button type="button" className="btn btn-sm btn-ghost" disabled={a.unresolved} onClick={() => setScheduleAction(a)}>schedule…</button>
                    <button type="button" className="btn btn-sm btn-ghost" disabled={a.unresolved} title={a.unresolved ? "pick a winning pack first" : undefined} onClick={() => { setSelectedAction(a); setDialogOpen(true) }}>run →</button>
                  </span>
                ),
              },
            ]}
            rows={filteredCatalog}
            keyOf={(a) => a.key}
            loading={catalogLoading}
            empty="No actions available."
          />
        </Panel>

        <Panel title="recent runs" className="lg:col-span-2" pad={0}>
          <Table<ActionRun>
            cols={[
              { k: "action", label: "action", w: "minmax(100px,1.2fr)", sortable: false, cell: (r) => <span className="trunc text-text-2">{(catalog ?? []).find((a) => a.key === r.action_key)?.name ?? r.action_key}</span> },
              { k: "status", label: "status", w: "90px", sortable: false, cell: (r) => <RunStatus s={r.status} reason={r.pending_reason} /> },
              { k: "duration", label: "took", w: "70px", sortable: false, cell: (r) => <span className="mono text-[11px] text-text-faint">{formatDuration(r)}</span> },
              { k: "when", label: "when", w: "70px", right: true, sortable: false, cell: (r) => <span className="mono num text-[11px] text-text-faint">{shortAgo(r.created_at)} ago</span> },
            ]}
            rows={runs ?? []}
            keyOf={(r) => r.id}
            onRowClick={(r) => router.push(`${runsBasePath}/actions/runs/${r.id}`)}
            loading={runsLoading}
            empty="No runs yet."
          />
        </Panel>
      </div>

      <ActionRunDialog
        action={selectedAction}
        scope={scope}
        targetId={targetId}
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setSelectedAction(null) }}
        hostOsCodename={scope === "host" ? host?.os_codename : undefined}
      />

      {scheduleAction && (
        <ScheduleActionDialog
          open
          onOpenChange={(o) => !o && setScheduleAction(null)}
          preselected={{ action_key: scheduleAction.key, target: { kind: scope, id: targetId } }}
        />
      )}
    </div>
  )
}
