"use client"

import { Breadcrumb } from "@/components/ui/breadcrumb"
import ProxmoxSettingsPage from "@/app/(dashboard)/settings/proxmox/client-page"

export default function HypervisorsPage() {
  return (
    <div className="space-y-8">
      <Breadcrumb items={[{ label: "Proxmox" }]} />

      <div>
        <h1 className="text-2xl font-bold text-white">Proxmox</h1>
        <p className="text-slate-400 text-sm mt-1">
          Manage Proxmox connections for VM snapshot and rollback support.
        </p>
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <p className="text-slate-300 text-sm">
          Connecting a hypervisor allows LabDog to take automatic VM snapshots
          before applying system updates. If an update fails verification, the
          snapshot enables instant rollback to the pre-update state. Configure
          snapshot and rollback settings in each group&apos;s{" "}
          <strong className="text-white">Workflow</strong> tab.
        </p>
      </div>

      <ProxmoxSettingsPage embedded />
    </div>
  )
}
