"use client"

import { useMemo, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { syncModuleLabel } from "@/lib/modules"
import type { ActionDefinition, ActionRun, Host, HostGroup, ScheduledAction } from "@/lib/types"
import { shortAgo } from "@/lib/fleet"
import { ActionRunDialog } from "@/components/action-run-dialog"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { Filter, Modal, PageHead, Table, Tabs, Tag } from "@/components/ld"

import ActionPacksPage from "@/app/(dashboard)/action-packs/client-page"
import SchedulesPage from "@/app/(dashboard)/schedules/client-page"

type Tab = "library" | "packs" | "schedules"

/**
 * Actions — Library · Packs · Schedules. Bring-your-own Ansible playbooks
 * exposed as one-click actions; schedules are the same actions with a
 * cron; packs are where the actions come from.
 */
export default function ActionsPage() {
  const router = useRouter()
  const search = useSearchParams()
  const tab = (search.get("tab") as Tab | null) ?? "library"
  const setTab = (t: string) => router.push(t === "library" ? "/actions" : `/actions?tab=${t}`)

  const { data: actions, isLoading } = useQuery<ActionDefinition[]>({ queryKey: ["actions"], queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/") })
  const { data: runs } = useQuery<ActionRun[]>({ queryKey: ["action-runs", "recent", 100], queryFn: () => apiFetch<ActionRun[]>("/api/actions/runs?limit=100") })
  const { data: schedules } = useQuery<ScheduledAction[]>({ queryKey: ["scheduled-actions"], queryFn: () => apiFetch<ScheduledAction[]>("/api/scheduled-actions") })
  const { data: hosts } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })

  const [pack, setPack] = useState("all")
  const [pick, setPick] = useState<ActionDefinition | null>(null)
  const [target, setTarget] = useState<{ scope: "host" | "group"; id: number } | null>(null)
  const [running, setRunning] = useState<ActionDefinition | null>(null)
  const [scheduling, setScheduling] = useState<ActionDefinition | null>(null)

  const visible = useMemo(() => (actions ?? []).filter((a) => !a.key.startsWith("_builtin.") && (pack === "all" || a.pack_name === pack)), [actions, pack])
  const packs = useMemo(() => [...new Set((actions ?? []).filter((a) => !a.key.startsWith("_builtin.")).map((a) => a.pack_name))].sort(), [actions])
  const runStats = useMemo(() => {
    const m = new Map<string, { n: number; last: string | null }>()
    for (const r of runs ?? []) {
      const e = m.get(r.action_key) ?? { n: 0, last: null }
      e.n += 1
      if (!e.last || r.created_at > e.last) e.last = r.created_at
      m.set(r.action_key, e)
    }
    return m
  }, [runs])

  const targetsOf = (a: ActionDefinition) => [a.supports_host && "host", a.supports_group && "group", a.supports_fleet && "fleet"].filter(Boolean).join(" · ")

  return (
    <>
      <PageHead
        crumbs={[{ label: "operations" }]}
        title="Actions"
        sub="Bring-your-own Ansible playbooks, exposed as one-click actions. Schedules are the same actions with a cron; packs are where they come from."
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => setTab("packs")}>
              Add pack
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => setScheduling(visible[0] ?? null)} disabled={visible.length === 0}>
              Schedule an action…
            </button>
          </>
        }
      >
        <Tabs
          tabs={[
            { k: "library", label: "Library" },
            { k: "packs", label: "Packs" },
            { k: "schedules", label: "Schedules" },
          ]}
          value={tab}
          onChange={setTab}
          counts={{ library: visible.length || undefined, packs: packs.length || undefined, schedules: schedules?.length }}
        />
      </PageHead>

      {tab === "library" && (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-[7px] border-b border-line bg-surface px-3.5 py-2">
            <Filter label="pack" value={pack} onChange={setPack} options={packs.map((p) => ({ k: p, label: p, n: (actions ?? []).filter((a) => a.pack_name === p && !a.key.startsWith("_builtin.")).length }))} />
            <span className="tt ml-auto">run → pick a target · schedule → cron</span>
          </div>
          <Table
            cols={[
              {
                k: "name",
                label: "action",
                w: "minmax(160px,1.2fr)",
                sortable: false,
                cell: (a) => (
                  <span className="flex min-w-0 flex-col">
                    <span className="mono font-medium text-text">{a.name}</span>
                    <span className="trunc text-[10.5px] text-text-faint">{a.key}</span>
                  </span>
                ),
              },
              { k: "pack", label: "pack", w: "130px", sortable: false, cell: (a) => <Tag>{a.pack_name}</Tag> },
              {
                k: "desc",
                label: "what it does",
                w: "minmax(200px,1.8fr)",
                sortable: false,
                cell: (a) => (
                  <span className="text-[11.5px] text-text-3" title={a.description}>
                    {a.description}
                  </span>
                ),
              },
              {
                k: "flags",
                label: "",
                w: "minmax(120px,1fr)",
                sortable: false,
                cell: (a) => (
                  <span className="flex gap-1">
                    {a.destructive && (
                      <Tag tone="warn" title="snapshot / verify / rollback envelope applies">
                        destructive
                      </Tag>
                    )}
                    {a.unresolved && (
                      <Tag tone="hold" title={`contested by ${a.overridden_from.join(", ")} — pick a winner in Packs`}>
                        unresolved
                      </Tag>
                    )}
                    {a.post_run_sync.length > 0 && <Tag title={`re-syncs ${a.post_run_sync.map(syncModuleLabel).join(", ")} afterwards`}>+sync</Tag>}
                  </span>
                ),
              },
              { k: "targets", label: "targets", w: "120px", sortable: false, cell: (a) => <span className="mono text-[11px]">{targetsOf(a) || "—"}</span> },
              { k: "runs", label: "runs", w: "60px", right: true, sortable: false, cell: (a) => <span className="mono num">{runStats.get(a.key)?.n ?? 0}</span> },
              {
                k: "last",
                label: "last run",
                w: "86px",
                right: true,
                sortable: false,
                cell: (a) => <span className="mono num text-[11px]">{runStats.get(a.key)?.last ? `${shortAgo(runStats.get(a.key)!.last)} ago` : "—"}</span>,
              },
              {
                k: "go",
                label: "",
                w: "150px",
                right: true,
                sortable: false,
                cell: (a) => (
                  <span className="flex justify-end gap-1.5">
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        setScheduling(a)
                      }}
                    >
                      schedule…
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={a.unresolved}
                      title={a.unresolved ? "pick a winning pack first" : undefined}
                      onClick={(e) => {
                        e.stopPropagation()
                        setTarget(null)
                        setPick(a)
                      }}
                    >
                      run…
                    </button>
                  </span>
                ),
              },
            ]}
            rows={visible}
            keyOf={(a) => a.key}
            loading={isLoading}
            empty="No actions registered. Add a pack, or check that the bundled pack synced."
          />
        </>
      )}

      {tab === "packs" && (
        <div className="scroll flex-1 p-3.5">
          <ActionPacksPage embedded />
        </div>
      )}
      {tab === "schedules" && (
        <div className="scroll flex-1 p-3.5">
          <SchedulesPage embedded />
        </div>
      )}

      {pick && (
        <Modal
          title={`Run ${pick.name}`}
          meta="pick a target"
          onClose={() => setPick(null)}
          w={460}
          footer={
            <>
              <span className="tt mr-auto">parameters come next</span>
              <button type="button" className="btn" onClick={() => setPick(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!target}
                onClick={() => {
                  setRunning(pick)
                  setPick(null)
                }}
              >
                Continue
              </button>
            </>
          }
        >
          {pick.supports_host && (
            <label className="flex flex-col gap-1">
              <span className="tt">host</span>
              <select className="inp mono" value={target?.scope === "host" ? target.id : ""} onChange={(e) => setTarget(e.target.value ? { scope: "host", id: Number(e.target.value) } : null)}>
                <option value="">— pick a host —</option>
                {(hosts ?? []).map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.hostname} · {h.ip_address}
                  </option>
                ))}
              </select>
            </label>
          )}
          {pick.supports_group && (
            <label className="flex flex-col gap-1">
              <span className="tt">{pick.supports_host ? "or group" : "group"}</span>
              <select className="inp mono" value={target?.scope === "group" ? target.id : ""} onChange={(e) => setTarget(e.target.value ? { scope: "group", id: Number(e.target.value) } : null)}>
                <option value="">— pick a group —</option>
                {[...(groups ?? [])]
                  .sort((a, b) => b.priority - a.priority)
                  .map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name} · p{g.priority} · {(hosts ?? []).filter((h) => h.group_ids.includes(g.id)).length} hosts
                    </option>
                  ))}
              </select>
            </label>
          )}
          {pick.supports_fleet && <span className="text-[11px] text-text-3">Fleet-wide runs go through a schedule — use schedule… to target every host.</span>}
        </Modal>
      )}

      {running && target && (
        <ActionRunDialog
          action={running}
          scope={target.scope}
          targetId={target.id}
          open
          onClose={() => {
            setRunning(null)
            setTarget(null)
          }}
        />
      )}
      {scheduling && <ScheduleActionDialog open onOpenChange={(o) => !o && setScheduling(null)} preselected={{ action_key: scheduling.key }} />}
    </>
  )
}
