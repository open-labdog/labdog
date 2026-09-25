import type { ModuleCounts } from "@/lib/types"

/**
 * The eight configuration modules, in the order the group page's Config
 * tab lists them.
 *
 * One registry for the several spellings each module already has: the
 * segment of its group editor route (`/groups/[id]/rules`), the host
 * detail tab (`?tab=rules`), the key in `ModuleCounts` (`firewall`) and
 * the canonical name the sync orchestrator's `module_filter` accepts
 * (`firewall`). Anything that needs to turn one into another goes
 * through here rather than carrying its own map.
 */
export interface ModuleDef {
  /** Canonical id used in URLs on the new shell: `/groups/<id>?tab=config&module=<id>`. */
  id: ModuleId
  label: string
  /** Unit noun for counts — "3 rules", "12 packages". */
  unit: string
  blurb: string
  /** Route segment of the existing group editor under `/groups/[id]/`. */
  groupSegment: string
  /** `?tab=` value on the host detail page. */
  hostTab: string
  /** Key in `ModuleCounts` (group summaries, host override counts). */
  countKey: keyof ModuleCounts
  /**
   * Canonical module name for `module_filter` on the sync endpoints, or
   * null when the module is applied by its own action rather than the
   * coalesced sync (CA certificates).
   */
  syncModule: string | null
  /** `module_type` values a `HostModuleStatus` row may carry for this module. */
  statusTypes: string[]
}

export type ModuleId =
  | "firewall"
  | "services"
  | "hosts-file"
  | "packages"
  | "users"
  | "cron"
  | "resolver"
  | "ca-certs"

export const MODULES: ModuleDef[] = [
  { id: "firewall", label: "Firewall", unit: "rules", blurb: "nftables / iptables", groupSegment: "rules", hostTab: "rules", countKey: "firewall", syncModule: "firewall", statusTypes: ["firewall"] },
  { id: "services", label: "Services", unit: "units", blurb: "systemd state + overrides", groupSegment: "services", hostTab: "services", countKey: "services", syncModule: "services", statusTypes: ["services"] },
  { id: "hosts-file", label: "Hosts file", unit: "entries", blurb: "/etc/hosts, system entries protected", groupSegment: "hosts-entries", hostTab: "hosts-file", countKey: "hosts_file", syncModule: "hosts-file", statusTypes: ["hosts-file", "hosts_file", "hosts"] },
  { id: "packages", label: "Packages", unit: "packages", blurb: "apt / dnf / yum", groupSegment: "packages", hostTab: "packages", countKey: "packages", syncModule: "packages", statusTypes: ["packages"] },
  { id: "users", label: "Users & SSH", unit: "users", blurb: "users, keys, sudo", groupSegment: "users", hostTab: "users", countKey: "users", syncModule: "linux-users", statusTypes: ["linux-users", "linux_users", "users"] },
  { id: "cron", label: "Cron", unit: "jobs", blurb: "declarative schedules", groupSegment: "cron-jobs", hostTab: "cron-jobs", countKey: "cron", syncModule: "cron", statusTypes: ["cron"] },
  { id: "resolver", label: "DNS resolver", unit: "servers", blurb: "resolv.conf / resolved", groupSegment: "resolver", hostTab: "dns", countKey: "resolver", syncModule: "resolver", statusTypes: ["resolver"] },
  { id: "ca-certs", label: "CA certificates", unit: "certs", blurb: "trust store", groupSegment: "ca-certs", hostTab: "ca-certs", countKey: "ca_certs", syncModule: null, statusTypes: ["ca-certs", "ca_certs"] },
]

const BY_ID = new Map(MODULES.map((m) => [m.id, m]))

export function moduleById(id: string | null | undefined): ModuleDef | undefined {
  return id ? BY_ID.get(id as ModuleId) : undefined
}

/** Resolve any of a module's spellings (status type, sync name, host tab, group segment) to its definition. */
export function moduleByAnyName(name: string | null | undefined): ModuleDef | undefined {
  if (!name) return undefined
  return MODULES.find(
    (m) =>
      m.id === name ||
      m.syncModule === name ||
      m.hostTab === name ||
      m.groupSegment === name ||
      m.countKey === name ||
      m.statusTypes.includes(name),
  )
}

/** Modules the coalesced sync can plan and apply. */
export const SYNCABLE_MODULES = MODULES.filter((m) => m.syncModule !== null)

/** Human label for a canonical sync module name, falling back to the raw name. */
export function syncModuleLabel(name: string): string {
  return moduleByAnyName(name)?.label ?? name
}
