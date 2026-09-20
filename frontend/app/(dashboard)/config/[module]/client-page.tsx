"use client"

import { useEffect, useMemo } from "react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { moduleById, MODULES, type ModuleDef } from "@/lib/modules"
import type { GroupSummary, HostGroup, HostSummary } from "@/lib/types"
import { Dot, PageHead, Table, Tag } from "@/components/ld"

import GroupRulesPage from "@/app/(dashboard)/groups/[id]/rules/client-page"
import GroupServicesPage from "@/app/(dashboard)/groups/[id]/services/client-page"
import GroupHostsEntriesPage from "@/app/(dashboard)/groups/[id]/hosts-entries/client-page"
import GroupPackagesPage from "@/app/(dashboard)/groups/[id]/packages/client-page"
import GroupUsersPage from "@/app/(dashboard)/groups/[id]/users/client-page"
import GroupCronJobsPage from "@/app/(dashboard)/groups/[id]/cron-jobs/client-page"
import GroupResolverPage from "@/app/(dashboard)/groups/[id]/resolver/client-page"
import GroupCACertsPage from "@/app/(dashboard)/groups/[id]/ca-certs/client-page"

/**
 * Config is module × scope. The module is the route, the scope is a
 * parameter, and both entrances — from the object (a group or host) or
 * from the module — land on the same URL, so there is exactly one editor
 * per module.
 *
 *   /config/firewall                     fleet lens: every scope that declares it
 *   /config/firewall?scope=group:3       the group's desired state (the editor)
 *   /config/firewall?scope=host:12       the host's effective state → host page
 */
export default function ConfigPage() {
  const params = useParams<{ module: string }>()
  const search = useSearchParams()
  const router = useRouter()
  const mod = moduleById(params.module) ?? MODULES[0]
  const scope = search.get("scope") ?? "fleet"

  // A host's effective state lives with the host: the merged view needs the
  // host's own overrides and its groups side by side, and the host page
  // already has both.
  useEffect(() => {
    if (scope.startsWith("host:")) router.replace(`/hosts/${scope.slice(5)}?tab=${mod.hostTab}`)
  }, [scope, mod.hostTab, router])

  if (scope.startsWith("group:")) return <ConfigGroup mod={mod} groupId={Number(scope.slice(6))} />
  if (scope.startsWith("host:")) return null
  return <ConfigFleet mod={mod} />
}

/* fleet lens — the question the old app could not answer at all: "show me
   every scope that declares this module, strongest first" */
function ConfigFleet({ mod }: { mod: ModuleDef }) {
  const router = useRouter()
  const { data: groups, isLoading } = useQuery<GroupSummary[]>({
    queryKey: ["groups-summary"],
    queryFn: () => apiFetch<GroupSummary[]>("/api/groups/summary"),
  })
  const { data: hosts } = useQuery<HostSummary[]>({
    queryKey: ["hosts-summary"],
    queryFn: () => apiFetch<HostSummary[]>("/api/hosts/summary"),
  })

  type Row = { k: string; kind: "group" | "host override"; name: string; priority: number; items: number; hosts: number; drifted: number; href: string }
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    for (const g of groups ?? []) {
      const n = g.module_counts[mod.countKey]
      if (!n) continue
      const members = (hosts ?? []).filter((h) => h.group_ids.includes(g.id))
      out.push({
        k: `group:${g.id}`,
        kind: "group",
        name: g.name,
        priority: g.priority,
        items: n,
        hosts: g.host_count,
        drifted: members.filter((h) => h.sync_status === "out_of_sync").length,
        href: `/config/${mod.id}?scope=group:${g.id}`,
      })
    }
    for (const h of hosts ?? []) {
      const n = h.override_counts[mod.countKey]
      if (!n) continue
      out.push({ k: `host:${h.id}`, kind: "host override", name: h.hostname, priority: 1000, items: n, hosts: 1, drifted: h.sync_status === "out_of_sync" ? 1 : 0, href: `/hosts/${h.id}?tab=${mod.hostTab}` })
    }
    return out.sort((a, b) => b.priority - a.priority || a.name.localeCompare(b.name))
  }, [groups, hosts, mod])

  const declaringGroups = rows.filter((r) => r.kind === "group").length
  const overrides = rows.filter((r) => r.kind === "host override").length

  return (
    <>
      <PageHead
        crumbs={[{ label: "config" }]}
        title={
          <>
            {mod.label} <Tag>fleet — all scopes</Tag>
          </>
        }
        sub={`${mod.blurb} · every scope that declares ${mod.unit}, strongest first. Pick a scope to edit it.`}
        actions={
          <Link href="/groups/new" className="btn btn-sm hover:no-underline">
            New group
          </Link>
        }
      />
      <Table
        cols={[
          {
            k: "name",
            label: "scope",
            w: "minmax(150px,1.2fr)",
            sortable: false,
            cell: (r) => (
              <span className="flex items-center gap-[7px]">
                <Tag tone={r.kind === "host override" ? "accent" : undefined}>{r.kind}</Tag>
                <span className="mono text-text">{r.name}</span>
              </span>
            ),
          },
          { k: "priority", label: "priority", w: "82px", right: true, sortable: false, cell: (r) => <span className="mono num" style={{ color: r.priority === 1000 ? "var(--accent)" : "var(--text-2)" }}>{r.priority === 1000 ? "host" : r.priority}</span> },
          { k: "items", label: mod.unit, w: "90px", right: true, sortable: false, cell: (r) => <span className="mono num text-text">{r.items}</span> },
          { k: "hosts", label: "applies to", w: "100px", right: true, sortable: false, cell: (r) => <span className="mono num">{r.hosts} host{r.hosts === 1 ? "" : "s"}</span> },
          {
            k: "drift",
            label: "drifted hosts",
            w: "112px",
            right: true,
            sortable: false,
            cell: (r) => (r.drifted ? <span className="mono num font-semibold text-warn">{r.drifted}</span> : <span className="text-text-faint">—</span>),
          },
          { k: "go", label: "", w: "74px", right: true, sortable: false, cell: () => <span className="tt text-ld-accent">edit →</span> },
        ]}
        rows={rows}
        keyOf={(r) => r.k}
        onRowClick={(r) => router.push(r.href)}
        loading={isLoading}
        empty={`No scope declares any ${mod.unit} yet. Open a group and add some — hosts in that group inherit them on their next plan.`}
      />
      <div className="flex shrink-0 flex-wrap items-center gap-[9px] border-t border-line bg-surface-2 px-3.5 py-[9px] text-[11.5px] text-text-2">
        <span>Evaluation runs strongest first: a host&apos;s own overrides, then groups from highest priority down. The control-plane SSH rule cannot be beaten by either.</span>
        <span className="tt ml-auto">
          {declaringGroups} group{declaringGroups === 1 ? "" : "s"} · {overrides} host override{overrides === 1 ? "" : "s"}
        </span>
      </div>
    </>
  )
}

/* group desired-state editor — the existing per-module editor, under the
   Config zone's header, with the blast radius it did not have before */
function ConfigGroup({ mod, groupId }: { mod: ModuleDef; groupId: number }) {
  const router = useRouter()
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: hosts } = useQuery<HostSummary[]>({ queryKey: ["hosts-summary"], queryFn: () => apiFetch<HostSummary[]>("/api/hosts/summary") })
  const g = groups?.find((x) => x.id === groupId)
  const members = (hosts ?? []).filter((h) => h.group_ids.includes(groupId))
  const overridden = members.filter((h) => h.override_counts[mod.countKey] > 0)
  const inheritedFrom = (groups ?? []).filter((x) => x.id !== groupId && x.priority !== (g?.priority ?? -1) && members.some((h) => h.group_ids.includes(x.id)))

  const Editor = {
    firewall: GroupRulesPage,
    services: GroupServicesPage,
    "hosts-file": GroupHostsEntriesPage,
    packages: GroupPackagesPage,
    users: GroupUsersPage,
    cron: GroupCronJobsPage,
    resolver: GroupResolverPage,
    "ca-certs": GroupCACertsPage,
  }[mod.id]

  if (groups && !g) {
    return (
      <>
        <PageHead crumbs={[{ label: "config" }, { label: mod.label, href: `/config/${mod.id}` }]} title={mod.label} sub="That group no longer exists." />
        <div className="p-6 text-xs text-text-3">
          <Link href={`/config/${mod.id}`} className="btn btn-sm hover:no-underline">
            Back to the fleet lens
          </Link>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHead
        crumbs={[{ label: "config" }, { label: mod.label, href: `/config/${mod.id}` }]}
        title={
          <>
            <span>{mod.label}</span>
            <Tag>group: {g?.name ?? "…"}</Tag>
            {g && (
              <Tag tone="accent" title="merge priority — higher wins" onClick={() => router.push(`/groups/${g.id}`)}>
                priority {g.priority}
              </Tag>
            )}
          </>
        }
        sub={
          <>
            Desired state. {g?.description ? `${g.description} · ` : ""}
            <span className="mono num">{members.length}</span> host{members.length === 1 ? "" : "s"} inherit this
            {inheritedFrom.length > 0 && <> · shares hosts with {inheritedFrom.map((x) => `${x.name} (p${x.priority})`).join(", ")}</>}
            {overridden.length > 0 && (
              <>
                {" "}
                · <span className="text-ld-accent">{overridden.length} carr{overridden.length === 1 ? "ies" : "y"} their own override</span>
              </>
            )}
          </>
        }
        actions={
          <>
            <Link href={`/groups/${groupId}`} className="btn btn-sm hover:no-underline" title="name, priority, category, members">
              Open group
            </Link>
            {mod.syncModule && (
              <button type="button" className="btn btn-sm btn-primary" onClick={() => router.push(`/plans?scope=group:${groupId}&modules=${mod.syncModule}`)} disabled={members.length === 0}>
                Plan sync — {members.length} host{members.length === 1 ? "" : "s"}
              </button>
            )}
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="tt">blast radius</span>
          <div className="flex flex-wrap gap-1">
            {members.slice(0, 12).map((h) => (
              <Tag key={h.id} onClick={() => router.push(`/hosts/${h.id}?tab=${mod.hostTab}`)} tone={h.override_counts[mod.countKey] > 0 ? "accent" : undefined} title={h.override_counts[mod.countKey] > 0 ? `${h.override_counts[mod.countKey]} host override${h.override_counts[mod.countKey] === 1 ? "" : "s"} for ${mod.label.toLowerCase()}` : h.ip_address}>
                {h.hostname}
              </Tag>
            ))}
            {members.length > 12 && <Tag>+{members.length - 12}</Tag>}
            {members.length === 0 && <span className="text-[11.5px] text-text-3">No hosts yet — saving here changes nothing until a host joins the group.</span>}
          </div>
          <span className="tt ml-auto flex items-center gap-1.5">
            <Dot tone="ok" /> nothing reaches a host until a plan is applied
          </span>
        </div>
      </PageHead>
      {/* The editor's own title repeats the page head; its toolbar stays. */}
      <div className="scroll flex-1 p-3.5 [&_h1]:hidden">
        <Editor embedded groupId={groupId} />
      </div>
    </>
  )
}
