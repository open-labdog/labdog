"use client"

import { SYNCABLE_MODULES } from "@/lib/modules"
import type { Host, HostGroup } from "@/lib/types"
import { Panel, Status } from "@/components/ld"
import type { PlanScope } from "../_hooks/use-plan-scope"

/** Step 1: pick a scope and the modules, then compute the plan. */
export function DefineStep({ hosts, groups, scope, onCompute }: { hosts: Host[] | undefined; groups: HostGroup[] | undefined; scope: PlanScope; onCompute: () => void }) {
  const { groupId, setGroupId, hostIds, setHostIds, modules, setModules, defined, scopeHosts, modulesLabel } = scope
  return (
    <div className="scroll flex-1 p-3.5">
      <div className="grid gap-3" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", alignItems: "start" }}>
        <Panel title="scope" meta="who this plan touches">
          <div className="flex flex-col gap-2.5 p-3">
            <label className="flex flex-col gap-1">
              <span className="tt">group</span>
              <select
                className="inp mono"
                value={groupId ?? ""}
                onChange={(e) => {
                  setGroupId(e.target.value ? Number(e.target.value) : null)
                  if (e.target.value) setHostIds([])
                }}
              >
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
            <div className="tt">or hand-pick hosts</div>
            <div className="scroll flex max-h-[260px] flex-col gap-px rounded-r border border-line bg-surface-2 p-1">
              {(hosts ?? []).map((h) => (
                <label key={h.id} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-[3px] hover:bg-surface-3">
                  <input
                    type="checkbox"
                    checked={hostIds.includes(h.id)}
                    onChange={(e) => {
                      setGroupId(null)
                      setHostIds((ids) => (e.target.checked ? [...ids, h.id] : ids.filter((x) => x !== h.id)))
                    }}
                    style={{ accentColor: "var(--accent)" }}
                  />
                  <span className="mono flex-1 text-[11.5px]">{h.hostname}</span>
                  <Status s={h.sync_status} dim />
                </label>
              ))}
              {(hosts ?? []).length === 0 && <span className="p-2 text-[11.5px] text-text-3">No hosts yet.</span>}
            </div>
          </div>
        </Panel>
        <div className="flex flex-col gap-3">
          <Panel title="modules" meta={`${modules.length} of ${SYNCABLE_MODULES.length}`}>
            <div className="flex flex-col gap-1 p-3">
              {SYNCABLE_MODULES.map((m) => (
                <label key={m.id} className="flex cursor-pointer items-center gap-2 py-[3px]">
                  <input
                    type="checkbox"
                    checked={modules.includes(m.syncModule!)}
                    onChange={(e) => setModules((ms) => (e.target.checked ? [...ms, m.syncModule!] : ms.filter((x) => x !== m.syncModule)))}
                    style={{ accentColor: "var(--accent)" }}
                  />
                  <span className="flex-1 text-xs text-text">{m.label}</span>
                  <span className="text-[10.5px] text-text-faint">{m.blurb}</span>
                </label>
              ))}
              <div className="mt-1 flex gap-1.5">
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setModules(SYNCABLE_MODULES.map((m) => m.syncModule!))}>
                  all
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setModules([])}>
                  none
                </button>
              </div>
              <span className="text-[11px] text-text-3">CA certificates are applied by their own action, not the coalesced sync.</span>
            </div>
          </Panel>
          <Panel title="compute">
            <div className="flex flex-col gap-2.5 p-3">
              <div className="flex items-baseline gap-2">
                <span className="mono num text-2xl font-semibold">{scopeHosts.length}</span>
                <span className="text-[11.5px] text-text-2">host{scopeHosts.length === 1 ? "" : "s"} in scope · {modulesLabel}</span>
              </div>
              <button type="button" className="btn btn-primary justify-center" disabled={!defined || scopeHosts.length === 0 || modules.length === 0} onClick={onCompute}>
                Compute plan — dry run on {scopeHosts.length} host{scopeHosts.length === 1 ? "" : "s"}
              </button>
              <span className="text-[10.5px] leading-[1.45] text-text-3">The dry run reads each host over SSH and compares it to desired state. It changes nothing.</span>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
