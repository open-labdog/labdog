"use client"

import { Dot, Modal, type Tone } from "@/components/ld"
import type { HostPlan } from "../model"

/** The last stop before Apply: what the run will do, and to which hosts. */
export function ApplyConfirm({
  modulesLabel,
  scopeLabel,
  scopeHostCount,
  changingCount,
  targets,
  excluded,
  selChangeCount,
  selSsh,
  pveInScope,
  onClose,
  onApply,
}: {
  modulesLabel: string
  scopeLabel: string
  scopeHostCount: number
  changingCount: number
  targets: HostPlan[]
  excluded: number
  selChangeCount: number
  selSsh: boolean
  pveInScope: number
  onClose: () => void
  onApply: () => void
}) {
  return (
    <Modal
      title={`Apply plan — ${modulesLabel}`}
      meta={`${scopeLabel} · ${targets.length} of ${scopeHostCount} hosts`}
      onClose={onClose}
      w={520}
      footer={
        <>
          <span className="tt mr-auto">this is the last stop</span>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={onApply}>
            Apply now
          </button>
        </>
      }
    >
      <div className="text-[12.5px] leading-[1.6]">unbork will queue one coalesced Ansible playbook per selected host. Hosts with an operation already running wait their turn in the per-host queue.</div>
      <div className="flex flex-col gap-[7px]">
        {(
          [
            [`${targets.length} of ${changingCount} changing hosts are selected${excluded ? ` — ${excluded} excluded, left untouched` : ""}`, excluded ? "warn" : "accent"],
            [`${selChangeCount} change${selChangeCount === 1 ? "" : "s"} across ${modulesLabel}`, "hold"],
            [selSsh ? "SSH (22/tcp) is touched — the control-plane rule is re-injected on apply" : "nothing on 22/tcp changes in this run", selSsh ? "warn" : "ok"],
            [pveInScope ? `${pveInScope} of ${scopeHostCount} hosts in scope are Proxmox guests — syncs do not snapshot them` : "no host in this scope is a Proxmox guest — rollback unavailable", "idle"],
          ] as [string, Tone][]
        ).map(([l, t]) => (
          <div key={l} className="flex items-start gap-2 text-xs">
            <div className="mt-1">
              <Dot tone={t} />
            </div>
            <span>{l}</span>
          </div>
        ))}
        <div className="mono scroll max-h-24 rounded-r border border-line px-[9px] py-1.5 text-[11.5px] text-text-2">
          {targets.map((p) => (
            <div key={p.host.id}>{p.host.hostname}</div>
          ))}
        </div>
      </div>
    </Modal>
  )
}
