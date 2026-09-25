"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { ageLabel, daysSince, STALE_DAYS, STATUS, STATUS_ORDER } from "@/lib/fleet"
import { MODULES } from "@/lib/modules"
import { showError, showSuccess } from "@/lib/toast"
import { useViewportWidth } from "@/hooks/use-viewport"
import type { HostGroup, HostSummary, ProxmoxNode, VMMapping } from "@/lib/types"
import { BulkBar, Confirm, Filter, PageHead, Status, Table, Tag, type Col, type Sort } from "@/components/ld"

type Filters = {
  status: string
  group: string
  backend: string
  drift: string
  overrides: string
  source: string
}

const CLEAR: Filters = { status: "all", group: "all", backend: "all", drift: "all", overrides: "all", source: "all" }

export default function HostsPage() {
  const router = useRouter()
  const search = useSearchParams()
  const queryClient = useQueryClient()
  const vw = useViewportWidth()

  const [q, setQ] = useState("")
  const [f, setF] = useState<Filters>({
    ...CLEAR,
    status: search.get("status") ?? "all",
    group: search.get("group") ?? "all",
  })
  const [sort, setSort] = useState<Sort>({ k: "hostname", dir: 1 })
  const [sel, setSel] = useState<Set<string | number>>(new Set())
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkDriftConfirmOpen, setBulkDriftConfirmOpen] = useState(false)
  const [bulkDriftTarget, setBulkDriftTarget] = useState(true)
  const [bulkDriftUpdating, setBulkDriftUpdating] = useState(false)

  const { data: hosts, isLoading, error } = useQuery<HostSummary[]>({
    queryKey: ["hosts-summary"],
    queryFn: () => apiFetch<HostSummary[]>("/api/hosts/summary"),
  })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: proxmoxNodes } = useQuery<ProxmoxNode[]>({
    queryKey: ["proxmox-nodes"],
    queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"),
    retry: false,
  })
  const hasProxmox = (proxmoxNodes?.length ?? 0) > 0
  const { data: vmMappings } = useQuery<VMMapping[]>({
    queryKey: ["vm-mappings"],
    queryFn: () => apiFetch<VMMapping[]>("/api/proxmox/vm-mappings"),
    enabled: hasProxmox,
  })

  const groupMap = useMemo(() => new Map((groups ?? []).map((g) => [g.id, g])), [groups])
  const vmByHost = useMemo(() => new Map((vmMappings ?? []).map((vm) => [vm.host_id, vm])), [vmMappings])
  const all = useMemo(() => hosts ?? [], [hosts])
  const overrideTotal = (h: HostSummary) => Object.values(h.override_counts).reduce((a, b) => a + b, 0)

  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = all.filter(
      (h) =>
        (f.status === "all" || h.sync_status === f.status) &&
        (f.group === "all" || (f.group === "ungrouped" ? h.group_ids.length === 0 : h.group_ids.includes(Number(f.group)))) &&
        (f.backend === "all" || h.firewall_backend === f.backend) &&
        (f.drift === "all" || (f.drift === "on" ? h.drift_check_enabled : !h.drift_check_enabled)) &&
        (f.overrides === "all" || (f.overrides === "yes" ? overrideTotal(h) > 0 : overrideTotal(h) === 0)) &&
        (f.source === "all" || (f.source === "proxmox" ? vmByHost.has(h.id) : !vmByHost.has(h.id))) &&
        (!ql ||
          h.hostname.toLowerCase().includes(ql) ||
          h.ip_address.includes(ql) ||
          (h.os_pretty_name ?? "").toLowerCase().includes(ql) ||
          h.group_ids.some((gid) => (groupMap.get(gid)?.name ?? "").toLowerCase().includes(ql))),
    )
    const key: Record<string, (h: HostSummary) => string | number> = {
      hostname: (h) => h.hostname,
      ip: (h) => h.ip_address.split(".").map((p) => p.padStart(3, "0")).join("."),
      status: (h) => STATUS_ORDER.indexOf(h.sync_status),
      groups: (h) => h.group_ids.length,
      overrides: (h) => overrideTotal(h),
      backend: (h) => h.firewall_backend,
      os: (h) => h.os_pretty_name ?? "",
      lastSync: (h) => daysSince(h.last_sync_at) ?? 9_999,
      drift: (h) => (h.drift_check_enabled ? 1 : 0),
    }
    const g = key[sort.k] ?? key.hostname
    return r.sort((a, b) => {
      const x = g(a)
      const y = g(b)
      return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
    })
  }, [all, q, f, sort, groupMap, vmByHost])

  const allCols: Col<HostSummary>[] = [
    { k: "hostname", label: "host", w: "minmax(130px,1.3fr)", cell: (h) => <span className="mono font-medium text-text">{h.hostname}</span> },
    { k: "ip", label: "address", w: "118px", cell: (h) => <span className="mono text-[11.5px]">{h.ip_address}</span> },
    { k: "status", label: "status", w: "110px", cell: (h) => <Status s={h.sync_status} /> },
    {
      k: "groups",
      label: "groups",
      w: "minmax(150px,1.9fr)",
      cell: (h) => {
        const gs = h.group_ids.map((id) => groupMap.get(id)).filter((g): g is HostGroup => !!g).sort((a, b) => b.priority - a.priority)
        if (gs.length === 0) return <span className="text-[11px] italic text-text-faint">ungrouped</span>
        const shown = vw > 1180 ? 3 : 2
        const rest = gs.length - shown
        // Strongest group first, whole; only the last visible tag gives way
        // when the column is tight, so "web · lab · base-li…" rather than
        // three stubs.
        return (
          <span className="flex min-w-0 items-center gap-[3px]">
            {gs.slice(0, shown).map((g, i, arr) => (
              <Tag key={g.id} shrink={i === arr.length - 1} title={`priority ${g.priority}`}>
                {g.name}
              </Tag>
            ))}
            {rest > 0 && (
              <span className="mono num shrink-0 text-[10px] text-text-2" title={gs.slice(shown).map((g) => g.name).join(", ")}>
                +{rest}
              </span>
            )}
          </span>
        )
      },
    },
    {
      k: "overrides",
      label: "overrides",
      w: "minmax(90px,.9fr)",
      cell: (h) => {
        const mods = MODULES.filter((m) => h.override_counts[m.countKey] > 0)
        if (mods.length === 0) return <span className="text-text-faint">—</span>
        return (
          <span className="flex min-w-0 items-center gap-[3px]" title={mods.map((m) => `${m.label}: ${h.override_counts[m.countKey]}`).join("\n")}>
            {mods.slice(0, 3).map((m) => (
              <Tag key={m.id} tone="accent" shrink>
                {m.id} {h.override_counts[m.countKey]}
              </Tag>
            ))}
            {mods.length > 3 && <span className="mono num text-[10px] text-text-2">+{mods.length - 3}</span>}
          </span>
        )
      },
    },
    { k: "backend", label: "firewall", w: "84px", cell: (h) => <span className="mono text-[11px]">{h.firewall_backend === "unknown" ? <span className="text-text-faint">unknown</span> : h.firewall_backend}</span> },
    ...(hasProxmox
      ? [
          {
            k: "proxmox",
            label: "proxmox",
            w: "minmax(110px,1fr)",
            sortable: false,
            cell: (h: HostSummary) => {
              const vm = vmByHost.get(h.id)
              if (!vm) return <span className="text-text-faint">—</span>
              return (
                <span className="mono trunc text-[11px]" title={`${vm.vm_type?.toLowerCase() === "lxc" ? "CT" : "VM"} ${vm.vmid} · ${vm.pve_node_name}`}>
                  {vm.vm_name}
                </span>
              )
            },
          } satisfies Col<HostSummary>,
        ]
      : []),
    { k: "os", label: "os", w: "minmax(96px,.8fr)", cell: (h) => <span className="text-[11.5px]">{h.os_pretty_name ?? <span className="text-text-faint">—</span>}</span> },
    {
      k: "drift",
      label: "drift check",
      w: "104px",
      cell: (h) =>
        h.drift_check_enabled ? (
          <span className="mono text-[11px] text-text-2" title={h.last_drift_check_at ? `last checked ${ageLabel(h.last_drift_check_at)}` : "never checked"}>
            on{h.last_drift_check_at ? ` · ${ageLabel(h.last_drift_check_at)}` : ""}
          </span>
        ) : (
          <span className="mono text-[11px] text-text-faint">off</span>
        ),
    },
    {
      k: "lastSync",
      label: "last sync",
      w: "86px",
      right: true,
      cell: (h) => {
        const d = daysSince(h.last_sync_at)
        return (
          <span className="mono num text-[11.5px]" style={{ color: d === null || d >= STALE_DAYS ? "var(--warn)" : "var(--text-3)" }}>
            {ageLabel(h.last_sync_at)}
          </span>
        )
      },
    },
  ]
  // 1024 and below: drop the lowest-value columns rather than scroll sideways.
  const drop = vw > 1180 ? [] : vw > 860 ? ["os", "backend"] : ["os", "ip", "backend", "drift", "proxmox"]
  const cols = allCols.filter((c) => !drop.includes(c.k))

  const selectedIds = Array.from(sel).map(Number)
  const filtersOn = q !== "" || Object.values(f).some((v) => v !== "all")

  async function handleBulkDelete() {
    setBulkDeleting(true)
    setBulkProgress({ done: 0, total: selectedIds.length })
    let success = 0
    let failed = 0
    for (const id of selectedIds) {
      try {
        await apiFetch(`/api/hosts/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
      setBulkProgress({ done: success + failed, total: selectedIds.length })
    }
    setBulkDeleting(false)
    setBulkProgress(null)
    setSel(new Set())
    await queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
    await queryClient.invalidateQueries({ queryKey: ["hosts"] })
    if (failed === 0) showSuccess(`Deleted ${success} host${success !== 1 ? "s" : ""}`)
    else showError(`Deleted ${success} of ${selectedIds.length}. ${failed} failed.`)
    setBulkConfirmOpen(false)
  }

  async function handleBulkDriftToggle() {
    setBulkDriftUpdating(true)
    let success = 0
    let failed = 0
    for (const id of selectedIds) {
      try {
        await apiFetch(`/api/hosts/${id}`, { method: "PUT", body: JSON.stringify({ drift_check_enabled: bulkDriftTarget }) })
        success++
      } catch {
        failed++
      }
    }
    setBulkDriftUpdating(false)
    setSel(new Set())
    await queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
    await queryClient.invalidateQueries({ queryKey: ["hosts"] })
    const label = bulkDriftTarget ? "enabled" : "disabled"
    if (failed === 0) showSuccess(`Drift check ${label} for ${success} host${success !== 1 ? "s" : ""}`)
    else showError(`Updated ${success} of ${selectedIds.length}. ${failed} failed.`)
    setBulkDriftConfirmOpen(false)
  }

  return (
    <>
      <PageHead
        crumbs={[{ label: "fleet" }]}
        title={
          <>
            Hosts{" "}
            <span className="mono num text-[12.5px] font-normal text-text-faint">
              {rows.length} of {all.length}
            </span>
          </>
        }
        actions={
          <>
            <Link href="/hosts/new" className="btn btn-sm hover:no-underline">
              Add host
            </Link>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => router.push("/plans")}>
              Plan sync
            </button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="filter by name, ip, os, group…"
            aria-label="Filter hosts"
            className="inp mono"
            style={{ width: 208, fontSize: 11.5, padding: "4px 8px" }}
          />
          <Filter
            label="status"
            value={f.status}
            onChange={(v) => setF({ ...f, status: v })}
            options={STATUS_ORDER.map((k) => ({ k, label: STATUS[k].label, n: all.filter((h) => h.sync_status === k).length }))}
          />
          <Filter
            label="group"
            value={f.group}
            onChange={(v) => setF({ ...f, group: v })}
            options={[
              ...[...(groups ?? [])].sort((a, b) => b.priority - a.priority).map((g) => ({ k: String(g.id), label: g.name, n: all.filter((h) => h.group_ids.includes(g.id)).length })),
              { k: "ungrouped", label: "ungrouped", n: all.filter((h) => h.group_ids.length === 0).length },
            ]}
          />
          <Filter label="firewall" value={f.backend} onChange={(v) => setF({ ...f, backend: v })} options={[{ k: "nftables", label: "nftables" }, { k: "iptables", label: "iptables" }, { k: "unknown", label: "unknown" }]} />
          <Filter label="drift check" value={f.drift} onChange={(v) => setF({ ...f, drift: v })} options={[{ k: "on", label: "on", n: all.filter((h) => h.drift_check_enabled).length }, { k: "off", label: "off", n: all.filter((h) => !h.drift_check_enabled).length }]} />
          <Filter label="overrides" value={f.overrides} onChange={(v) => setF({ ...f, overrides: v })} options={[{ k: "yes", label: "has overrides", n: all.filter((h) => overrideTotal(h) > 0).length }, { k: "no", label: "none" }]} />
          {hasProxmox && <Filter label="source" value={f.source} onChange={(v) => setF({ ...f, source: v })} options={[{ k: "proxmox", label: "proxmox guest", n: all.filter((h) => vmByHost.has(h.id)).length }, { k: "other", label: "not mapped" }]} />}
          {filtersOn && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => {
                setQ("")
                setF(CLEAR)
                router.replace("/hosts")
              }}
            >
              clear
            </button>
          )}
          <span className="tt ml-auto">click a row → host detail</span>
        </div>
      </PageHead>

      {error ? (
        <div className="p-6 text-center text-xs text-danger">Failed to load hosts</div>
      ) : (
        <Table
          cols={cols}
          rows={rows}
          keyOf={(h) => h.id}
          sort={sort}
          onSort={(k) => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : 1 }))}
          selected={sel}
          onSelect={setSel}
          onRowClick={(h) => router.push(`/hosts/${h.id}`)}
          loading={isLoading}
          empty={
            all.length === 0 ? (
              <>
                No hosts yet.{" "}
                <Link href="/hosts/new" className="underline">
                  Add your first host
                </Link>{" "}
                or{" "}
                <Link href="/discovery" className="underline">
                  scan the network
                </Link>
                .
              </>
            ) : (
              "No host matches those filters."
            )
          }
        />
      )}

      <BulkBar n={sel.size} onClear={() => setSel(new Set())} status={bulkProgress ? `Deleting ${bulkProgress.done}/${bulkProgress.total}…` : undefined}>
        <button
          type="button"
          className="btn btn-sm"
          disabled={bulkDeleting || bulkDriftUpdating}
          onClick={() => {
            setBulkDriftTarget(true)
            setBulkDriftConfirmOpen(true)
          }}
        >
          Enable drift check
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={bulkDeleting || bulkDriftUpdating}
          onClick={() => {
            setBulkDriftTarget(false)
            setBulkDriftConfirmOpen(true)
          }}
        >
          Disable drift check
        </button>
        <button type="button" className="btn btn-sm btn-danger" disabled={bulkDeleting || bulkDriftUpdating} onClick={() => setBulkConfirmOpen(true)}>
          Delete selected
        </button>
      </BulkBar>

      <Confirm
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Delete ${sel.size} ${sel.size === 1 ? "host" : "hosts"}?`}
        description="This action cannot be undone."
        confirmLabel="Delete All"
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />
      <Confirm
        open={bulkDriftConfirmOpen}
        onOpenChange={setBulkDriftConfirmOpen}
        title={`${bulkDriftTarget ? "Enable" : "Disable"} drift check for ${sel.size} ${sel.size === 1 ? "host" : "hosts"}?`}
        description={bulkDriftTarget ? "Drift checking will be scheduled for the selected hosts." : "Drift checking will be stopped for the selected hosts."}
        confirmLabel={bulkDriftTarget ? "Enable" : "Disable"}
        loading={bulkDriftUpdating}
        onConfirm={handleBulkDriftToggle}
      />
    </>
  )
}
