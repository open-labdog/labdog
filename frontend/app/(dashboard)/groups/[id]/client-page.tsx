"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { runStatus } from "@/lib/activity"
import { countStatuses, plural, shortAgo } from "@/lib/fleet"
import { GITOPS_STATUS, def } from "@/lib/status"
import { MODULES, moduleByAnyName, moduleById, type ModuleDef, type ModuleId } from "@/lib/modules"
import { showError, showSuccess } from "@/lib/toast"
import type { ActionDefinition, ActionRun, GitRepository, GroupSummary, HostGroup, HostSummary } from "@/lib/types"
import { Banner, Facts, Field, Modal, PageHead, Panel, RunStatus, Seg, Status, Table, Tabs, Tag } from "@/components/ld"
import { categoryOptions, useGroupMerge } from "@/components/group-editor"
import { RunActionButton } from "@/components/run-action-button"
import { ScheduledActionsSection } from "@/components/scheduled-actions/scheduled-actions-section"

import GroupRulesPage from "./rules/client-page"
import GroupServicesPage from "./services/client-page"
import GroupHostsEntriesPage from "./hosts-entries/client-page"
import GroupPackagesPage from "./packages/client-page"
import GroupUsersPage from "./users/client-page"
import GroupCronJobsPage from "./cron-jobs/client-page"
import GroupResolverPage from "./resolver/client-page"
import GroupCACertsPage from "./ca-certs/client-page"

/**
 * A group's own page, on the Host detail pattern: Overview · Config ·
 * Members · Activity. Everything the old Edit-group dialog did — rename,
 * re-prioritise, re-categorise, add and remove members, delete — lives
 * inline on the relevant tab, with the merge consequences shown before
 * Save. Config is the module list on the left and the module's editor on
 * the right; a module is "declared" once the group has an item for it.
 *
 * The URL is the state: `?tab=config&module=firewall`, `?tab=activity&view=schedules`.
 * The old module tabs (`?tab=rules`, `?tab=dns` …) still resolve.
 */
type Tab = "overview" | "config" | "members" | "activity"
type ActivityView = "runs" | "schedules"

const EDITORS: Record<ModuleId, React.ComponentType<{ embedded?: boolean; groupId?: number }>> = {
  firewall: GroupRulesPage,
  services: GroupServicesPage,
  "hosts-file": GroupHostsEntriesPage,
  packages: GroupPackagesPage,
  users: GroupUsersPage,
  cron: GroupCronJobsPage,
  resolver: GroupResolverPage,
  "ca-certs": GroupCACertsPage,
}


function resolveTab(raw: string | null): { tab: Tab; module?: ModuleId; view?: ActivityView } {
  if (!raw || raw === "overview") return { tab: "overview" }
  if (raw === "config" || raw === "members" || raw === "activity") return { tab: raw }
  if (raw === "schedules") return { tab: "activity", view: "schedules" }
  if (raw === "actions") return { tab: "activity", view: "runs" }
  const m = moduleByAnyName(raw)
  return m ? { tab: "config", module: m.id } : { tab: "overview" }
}

const byHostname = (a: HostSummary, b: HostSummary) => a.hostname.localeCompare(b.hostname)

export default function GroupDetailPage() {
  const params = useParams<{ id: string }>()
  const id = Number(params.id)
  const router = useRouter()
  const search = useSearchParams()
  const queryClient = useQueryClient()

  const { data: groups, isLoading } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: summaries } = useQuery<GroupSummary[]>({ queryKey: ["groups-summary"], queryFn: () => apiFetch<GroupSummary[]>("/api/groups/summary") })
  const { data: hosts } = useQuery<HostSummary[]>({ queryKey: ["hosts-summary"], queryFn: () => apiFetch<HostSummary[]>("/api/hosts/summary") })
  const { data: runs } = useQuery<ActionRun[]>({
    queryKey: ["action-runs", "group", id],
    queryFn: () => apiFetch<ActionRun[]>(`/api/actions/runs?group_id=${id}&limit=20`),
    refetchInterval: (q) => ((q.state.data as ActionRun[] | undefined)?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false),
  })
  const { data: catalog } = useQuery<ActionDefinition[]>({ queryKey: ["actions-catalog"], queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"), staleTime: 60_000 })

  const group = groups?.find((g) => g.id === id)
  const members = useMemo(() => (hosts ?? []).filter((h) => h.group_ids.includes(id)).sort(byHostname), [hosts, id])
  /* the summary carries module counts and host count; until it lands, a
     zero-count stand-in keeps the merge maths honest (no false "taken") */
  const summary = useMemo<GroupSummary | null>(() => {
    const s = summaries?.find((g) => g.id === id)
    if (s) return s
    if (!group) return null
    return {
      id: group.id, name: group.name, description: group.description, category: group.category, priority: group.priority,
      gitops_enabled: group.gitops_enabled, gitops_status: group.gitops_status, created_at: group.created_at, updated_at: group.updated_at,
      host_count: members.length, has_shared_hosts: false,
      module_counts: { firewall: 0, hosts_file: 0, services: 0, users: 0, cron: 0, packages: 0, resolver: 0, ca_certs: 0 },
    }
  }, [summaries, group, id, members.length])
  const declared = useMemo(() => MODULES.filter((m) => (summary?.module_counts[m.countKey] ?? 0) > 0), [summary])

  const resolved = resolveTab(search.get("tab"))
  const tab = resolved.tab
  const mod = moduleById(search.get("module")) ?? (resolved.module ? moduleById(resolved.module) : undefined) ?? declared[0] ?? MODULES[0]
  const view: ActivityView = search.get("view") === "schedules" ? "schedules" : (resolved.view ?? "runs")

  const navigate = (next: { tab?: Tab; module?: ModuleId; view?: ActivityView }) => {
    const t = next.tab ?? tab
    const q = new URLSearchParams()
    if (t !== "overview") q.set("tab", t)
    if (t === "config") q.set("module", next.module ?? mod.id)
    if (t === "activity" && (next.view ?? view) === "schedules") q.set("view", "schedules")
    const qs = q.toString()
    router.replace(`/groups/${id}${qs ? `?${qs}` : ""}`)
  }

  const invalidate = () =>
    Promise.all(
      [["groups"], ["groups-summary"], ["hosts"], ["hosts-summary"]].map((k) => queryClient.invalidateQueries({ queryKey: k })),
    )

  if (!isLoading && groups && !group) {
    return (
      <>
        <PageHead crumbs={[{ label: "fleet", href: "/hosts" }, { label: "groups", href: "/groups" }]} title="Group" sub="That group no longer exists." />
        <div className="p-6 text-xs text-text-3">
          Group not found.{" "}
          <Link href="/groups" className="underline">
            Back to Groups
          </Link>
        </div>
      </>
    )
  }

  const counts = countStatuses(members)
  const membersMeta = [
    plural(members.length, "host"),
    counts.in_sync ? `${counts.in_sync} in sync` : null,
    counts.out_of_sync ? `${counts.out_of_sync} drifted` : null,
    counts.error ? `${counts.error} failed` : null,
  ]
    .filter(Boolean)
    .join(" · ")
  const planHref = `/plans?scope=group:${id}${tab === "config" && mod.syncModule ? `&modules=${mod.syncModule}` : ""}`
  const Editor = EDITORS[mod.id]

  return (
    <>
      <PageHead
        crumbs={[{ label: "fleet", href: "/hosts" }, { label: "groups", href: "/groups" }]}
        title={
          <>
            <span className="mono">{group?.name ?? "…"}</span>
            {group?.category && <Tag>{group.category}</Tag>}
            {group && <Tag title="merge priority — higher wins">priority {group.priority}</Tag>}
            {group?.gitops_enabled && (
              <Tag tone={def(GITOPS_STATUS, group.gitops_status ?? "disconnected").tone} title="desired state is imported from a Git repository">
                gitops
              </Tag>
            )}
          </>
        }
        sub={
          <>
            {group?.description ? `${group.description} · ` : ""}
            <span className="mono num">{members.length}</span> host{members.length === 1 ? " inherits" : "s inherit"} this
            {declared.length > 0 ? <> · declares {declared.map((m) => m.id).join(", ")}</> : <> · declares nothing yet</>}
          </>
        }
        actions={
          <>
            <RunActionButton scope="group" targetId={id} targetLabel={group?.name} />
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={members.length === 0}
              title={members.length === 0 ? "no hosts in this group — nothing to plan" : "dry-run every host in the group, then review before anything applies"}
              onClick={() => router.push(planHref)}
            >
              Plan sync — {plural(members.length, "host")}
            </button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="tt hidden sm:inline">host overrides beat every group, then priority high → low</span>
          <span className="ml-auto flex flex-wrap items-center gap-2.5">
            {tab === "activity" && <Seg sm value={view} onChange={(k) => navigate({ view: k as ActivityView })} options={[{ k: "runs", label: "Runs" }, { k: "schedules", label: "Schedules" }]} />}
            <Tabs
              tabs={[
                { k: "overview", label: "Overview" },
                { k: "config", label: "Config" },
                { k: "members", label: "Members" },
                { k: "activity", label: "Activity" },
              ]}
              value={tab}
              onChange={(k) => navigate({ tab: k as Tab })}
              counts={{ config: declared.length || undefined, members: members.length || undefined, activity: runs?.length || undefined }}
            />
          </span>
        </div>
      </PageHead>

      {tab === "overview" && group && summary && (
        <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
          <div className="grid items-start gap-3 md:grid-cols-[minmax(320px,400px)_1fr]">
            <GroupSettings key={group.id} group={group} summary={summary} groups={summaries ?? []} hosts={hosts ?? []} memberCount={members.length} onSaved={invalidate} />
            <Panel
              title="modules"
              meta="declared state, by module"
              footer={
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate({ tab: "config" })}>
                  all modules →
                </button>
              }
            >
              <Table
                dense
                cols={[
                  { k: "m", label: "module", w: "minmax(120px,1fr)", sortable: false, cell: (m: ModuleDef) => <span className="text-text">{m.label}</span> },
                  { k: "n", label: "items", w: "120px", right: true, sortable: false, cell: (m) => <span className="mono num">{summary.module_counts[m.countKey]} {m.unit}</span> },
                  { k: "go", label: "", w: "74px", right: true, sortable: false, cell: () => <span className="tt text-ld-accent">edit →</span> },
                ]}
                rows={declared}
                keyOf={(m) => m.id}
                onRowClick={(m) => navigate({ tab: "config", module: m.id })}
                empty="This group declares nothing yet — open Config and add the first item."
              />
            </Panel>
          </div>

          <div className="grid items-start gap-3 md:grid-cols-[minmax(320px,400px)_1fr]">
            <Panel title="members" meta={membersMeta}>
              <div className="flex flex-col gap-[9px] p-[11px]">
                <div className="flex flex-wrap gap-[5px]">
                  {members.slice(0, 10).map((h) => (
                    <Tag key={h.id} onClick={() => router.push(`/hosts/${h.id}`)} title={h.ip_address}>
                      {h.hostname}
                    </Tag>
                  ))}
                  {members.length > 10 && <Tag onClick={() => navigate({ tab: "members" })}>+{members.length - 10} more</Tag>}
                  {members.length === 0 && <span className="text-[11.5px] text-text-3">No hosts in this group yet.</span>}
                </div>
                <button type="button" className="btn btn-sm self-start" onClick={() => navigate({ tab: "members" })}>
                  {members.length === 0 ? "Add members →" : "View all members →"}
                </button>
              </div>
            </Panel>
            <DangerZone group={group} memberCount={members.length} onDeleted={invalidate} />
          </div>

          <div className="grid items-start gap-3 md:grid-cols-[minmax(320px,400px)_1fr]">
            <GitOpsPanel group={group} onChanged={invalidate} />
            <Panel
              title="recent activity"
              meta="this group"
              footer={
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate({ tab: "activity" })}>
                  all activity →
                </button>
              }
            >
              {(runs ?? []).slice(0, 5).map((r) => (
                <Link key={r.id} href={`/groups/${id}/actions/runs/${r.id}`} className="row-hover flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px] text-text hover:no-underline">
                  <span className="mono num w-[52px] shrink-0 text-[10.5px] text-text-faint">{shortAgo(r.created_at)} ago</span>
                  <RunStatus s={r.status} reason={r.pending_reason} />
                  <span className="trunc flex-1 text-xs">{catalog?.find((a) => a.key === r.action_key)?.name ?? r.action_key}</span>
                  <span className="tt text-[9px]">{r.scheduled_action_id ? "schedule" : r.triggered_by_user_id ? "user" : "system"}</span>
                </Link>
              ))}
              {runs && runs.length === 0 && <div className="p-[11px] text-[11.5px] text-text-3">No runs against this group yet. Run action… starts one; plans show up under Operations › Runs.</div>}
            </Panel>
          </div>
        </div>
      )}

      {tab === "config" && (
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          {/* module list: a column beside the editor, a strip above it on phones */}
          <div className="scroll flex shrink-0 gap-px border-b border-line bg-surface p-2 md:w-[180px] md:flex-col md:border-b-0 md:border-r">
            <span className="tt hidden px-2 pb-1.5 pt-1 md:block">modules</span>
            {MODULES.map((m) => {
              const n = summary?.module_counts[m.countKey] ?? 0
              const on = m.id === mod.id
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => navigate({ tab: "config", module: m.id })}
                  aria-pressed={on}
                  className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-r border-0 px-2 py-1.5 text-left text-xs"
                  style={{ background: on ? "var(--surface-3)" : "transparent", color: on ? "var(--text)" : n > 0 ? "var(--text-2)" : "var(--text-faint)", fontWeight: on ? 600 : 400 }}
                  title={n > 0 ? `${n} ${m.unit} declared` : `not declared — ${m.blurb}`}
                >
                  <span className="trunc md:flex-1">{m.label}</span>
                  {n > 0 && <span className="mono num text-[10px] text-text-faint">{n}</span>}
                </button>
              )
            })}
            <span className="mt-2 hidden px-2 text-[10.5px] leading-[1.45] text-text-faint md:block">A module is declared once it has an item; adding the first declares it, deleting the last undeclares it.</span>
          </div>
          {/* The editor's own title repeats the module list; its toolbar stays. */}
          <div className="scroll flex-1 p-3.5 [&_h1]:hidden">
            <Editor key={mod.id} embedded groupId={id} />
          </div>
        </div>
      )}

      {tab === "members" && group && <MembersTab group={group} members={members} hosts={hosts} groups={summaries ?? []} onChanged={invalidate} />}

      {tab === "activity" && view === "runs" && (
        <Table
          dense
          cols={[
            { k: "t", label: "when", w: "84px", sortable: false, cell: (r: ActionRun) => <span className="mono num text-[11px]">{shortAgo(r.created_at)} ago</span> },
            {
              k: "status",
              label: "status",
              w: "104px",
              sortable: false,
              cell: (r) => <RunStatus s={r.status} reason={r.pending_reason} />,
            },
            { k: "action", label: "action", w: "minmax(160px,1.2fr)", sortable: false, cell: (r) => <span className="text-text">{catalog?.find((a) => a.key === r.action_key)?.name ?? r.action_key}</span> },
            { k: "target", label: "target", w: "minmax(140px,1fr)", sortable: false, cell: (r) => <span className="mono text-[11px]">{r.target_label}</span> },
            { k: "who", label: "actor", w: "84px", sortable: false, cell: (r) => <span className="mono text-[11px]">{r.scheduled_action_id ? "schedule" : r.triggered_by_user_id ? "user" : "system"}</span> },
            { k: "go", label: "", w: "78px", right: true, sortable: false, cell: () => <span className="tt text-ld-accent">open →</span> },
          ]}
          rows={runs ?? []}
          keyOf={(r) => r.id}
          onRowClick={(r) => router.push(`/groups/${id}/actions/runs/${r.id}`)}
          rowTone={(r) => (runStatus(r.status) === "failed" ? "danger" : undefined)}
          loading={!runs}
          empty="No runs against this group yet. Run action… starts one."
        />
      )}
      {tab === "activity" && view === "schedules" && (
        <div className="scroll flex-1 p-3.5">
          <ScheduledActionsSection scope="group" targetId={id} />
        </div>
      )}
    </>
  )
}

/* ── overview: settings, inline ─────────────────────────────────────── */

function GroupSettings({
  group,
  summary,
  groups,
  hosts,
  memberCount,
  onSaved,
}: {
  group: HostGroup
  summary: GroupSummary
  groups: GroupSummary[]
  hosts: Pick<HostSummary, "group_ids">[]
  memberCount: number
  onSaved: () => Promise<unknown>
}) {
  const [f, setF] = useState(() => ({ name: group.name, priority: String(group.priority), category: group.category ?? "", desc: group.description ?? "" }))
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }))
  const { prio, tie, moved, flips, nameErr } = useGroupMerge({ group: summary, groups, hosts, name: f.name, priority: f.priority })
  const dirty = f.name.trim() !== group.name || prio !== group.priority || f.category !== (group.category ?? "") || f.desc.trim() !== (group.description ?? "")
  const valid = !nameErr && prio > 0

  const save = async () => {
    setBusy(true)
    const body = { name: f.name.trim(), priority: prio, category: f.category || null, description: f.desc.trim() || null }
    try {
      await apiFetch(`/api/groups/${group.id}`, { method: "PUT", json: body })
      await onSaved()
      showSuccess(
        moved
          ? `${body.name} saved at priority ${prio} — ${plural(memberCount, "host")} re-merge on their next plan.`
          : `${body.name} saved — desired state only, ${plural(memberCount, "host")} affected on the next plan.`,
      )
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to save group")
    } finally {
      setBusy(false)
    }
  }

  const showNameErr = !!nameErr && f.name.trim() !== group.name
  return (
    <Panel title="settings" meta={dirty ? <span className="text-warn">unsaved</span> : "name, category, priority"}>
      <div className="flex flex-col gap-2.5 p-[11px]">
        <div className="grid gap-[9px]" style={{ gridTemplateColumns: "1fr 130px" }}>
          <label className="flex flex-col gap-1">
            <span className="tt">
              name{showNameErr && <span className="normal-case tracking-normal text-danger"> · {nameErr}</span>}
            </span>
            <input className="inp mono" value={f.name} onChange={(e) => set("name", e.target.value)} style={showNameErr ? { borderColor: "var(--danger)" } : undefined} aria-label="group name" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="tt">category</span>
            <select className="inp" value={f.category} onChange={(e) => set("category", e.target.value)} aria-label="category">
              <option value="">—</option>
              {categoryOptions(groups).map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1">
          <span className="tt">what it&apos;s for</span>
          <input className="inp" value={f.desc} placeholder="one line — why does this group exist?" onChange={(e) => set("desc", e.target.value)} aria-label="description" />
        </label>
        <div className="flex flex-col gap-[7px] rounded-r p-[9px]" style={{ border: `1px solid ${moved ? "var(--accent-line)" : "var(--border)"}`, background: moved ? "var(--accent-soft)" : "var(--surface-2)" }}>
          <span className="tt">priority · higher wins the merge</span>
          <div className="flex items-center gap-[9px]">
            <input className="inp mono num" type="number" min={1} max={1000} value={f.priority} onChange={(e) => set("priority", e.target.value)} style={{ width: 66, flexShrink: 0 }} aria-label="priority" />
            <input type="range" min={1} max={100} value={Math.min(prio, 100)} onChange={(e) => set("priority", e.target.value)} className="flex-1" style={{ accentColor: "var(--accent)" }} aria-label="priority slider" />
            {moved && (
              <Tag tone="accent">
                {group.priority} → {prio}
              </Tag>
            )}
          </div>
          {tie && (
            <div className="text-[11px] text-warn">
              Same priority as <span className="mono">{tie.name}</span> — ties break by an order nobody remembers. Pick a gap.
            </div>
          )}
        </div>
        {flips.length > 0 && (
          <div className="flex flex-col gap-[5px] rounded-r border border-warn bg-warn-soft p-[9px]">
            <span className="tt text-text">this move changes who wins</span>
            {flips.map(({ o, shared, mods, now }) => (
              <span key={o.id} className="text-[11px] text-text">
                <span className="mono">{f.name.trim() || group.name}</span> now {now ? "wins over" : "loses to"} <span className="mono">{o.name}</span> on {plural(shared, "shared host")} for{" "}
                <span className="mono">{mods.map((m) => m.id).join(", ")}</span>
              </span>
            ))}
            <span className="text-[11px] text-text-2">Nothing reaches a host until a plan runs; the plan shows the exact items that change.</span>
          </div>
        )}
        <div className="flex items-center gap-[7px]">
          <button type="button" className="btn btn-sm btn-primary" disabled={!dirty || !valid || busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save changes"}
          </button>
          {dirty && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setF({ name: group.name, priority: String(group.priority), category: group.category ?? "", desc: group.description ?? "" })}>
              revert
            </button>
          )}
        </div>
      </div>
    </Panel>
  )
}

/* ── overview: delete ────────────────────────────────────────────────── */

function DangerZone({ group, memberCount, onDeleted }: { group: HostGroup; memberCount: number; onDeleted: () => Promise<unknown> }) {
  const router = useRouter()
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const del = async () => {
    if (!confirm) {
      setConfirm(true)
      return
    }
    setBusy(true)
    try {
      await apiFetch(`/api/groups/${group.id}`, { method: "DELETE" })
      showSuccess(`Group ${group.name} deleted — ${plural(memberCount, "host")} dropped it`)
      await onDeleted()
      router.push("/groups")
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to delete group")
      setBusy(false)
      setConfirm(false)
    }
  }
  return (
    <Panel title="danger zone">
      <div className="flex flex-wrap items-center gap-[9px] p-[11px]">
        <span className="flex-1 text-[11.5px] text-text-2">Deleting removes this group from every host that carries it. Nothing on a host changes until a plan runs.</span>
        <span className="flex items-center gap-1.5">
          {confirm && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirm(false)} disabled={busy}>
              keep it
            </button>
          )}
          <button type="button" className={`btn btn-sm ${confirm ? "btn-danger" : "btn-ghost text-danger"}`} onClick={() => void del()} disabled={busy}>
            {busy ? "Deleting…" : confirm ? `Confirm — ${plural(memberCount, "host")} drop this group` : "Delete group"}
          </button>
        </span>
      </div>
    </Panel>
  )
}

/* ── overview: gitops ────────────────────────────────────────────────── */

function GitOpsPanel({ group, onChanged }: { group: HostGroup; onChanged: () => Promise<unknown> }) {
  const [enableOpen, setEnableOpen] = useState(false)
  const [repoId, setRepoId] = useState("")
  const [filePath, setFilePath] = useState("")
  const [confirmDisable, setConfirmDisable] = useState(false)
  const [busy, setBusy] = useState(false)
  const { data: repos } = useQuery<GitRepository[]>({ queryKey: ["git-repos"], queryFn: () => apiFetch<GitRepository[]>("/api/git-repos") })
  const repo = repos?.find((r) => r.id === group.git_repository_id)

  const enable = async () => {
    setBusy(true)
    try {
      await apiFetch(`/api/groups/${group.id}/gitops/enable`, { method: "POST", json: { git_repository_id: Number(repoId), file_path: filePath.trim() } })
      await onChanged()
      showSuccess(`GitOps enabled — ${group.name} now imports its desired state from ${filePath.trim()}`)
      setEnableOpen(false)
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to enable GitOps")
    } finally {
      setBusy(false)
    }
  }
  const disable = async () => {
    if (!confirmDisable) {
      setConfirmDisable(true)
      return
    }
    setBusy(true)
    try {
      await apiFetch(`/api/groups/${group.id}/gitops/disable`, { method: "POST" })
      await onChanged()
      showSuccess(`GitOps disabled — ${group.name} keeps its current items and stops importing`)
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to disable GitOps")
    } finally {
      setBusy(false)
      setConfirmDisable(false)
    }
  }

  return (
    <Panel title="gitops" meta={group.gitops_enabled ? "desired state from Git" : "off"}>
      {!group.gitops_enabled ? (
        <div className="flex flex-wrap items-center gap-[9px] p-[11px]">
          <span className="flex-1 text-[11.5px] text-text-2">Import this group&apos;s desired state from a file in a Git repository instead of editing it here.</span>
          <button type="button" className="btn btn-sm" onClick={() => setEnableOpen(true)} disabled={!repos || repos.length === 0} title={repos && repos.length === 0 ? "register a repository under Settings › Integrations first" : undefined}>
            Enable…
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-2 p-[11px]">
          <Facts
            min={120}
            items={[
              { k: "status", v: <Tag tone={def(GITOPS_STATUS, group.gitops_status ?? "disconnected").tone}>{group.gitops_status ?? "disconnected"}</Tag> },
              { k: "repository", v: repo?.name ?? "unknown" },
              { k: "file", v: group.gitops_file_path ?? "—", mono: true },
              { k: "last import", v: group.gitops_last_import_at ? `${shortAgo(group.gitops_last_import_at)} ago` : "never", mono: true },
            ]}
          />
          {group.gitops_status === "error" && group.gitops_error_message && <Banner tone="danger">{group.gitops_error_message}</Banner>}
          <div className="flex items-center gap-1.5">
            <button type="button" className={`btn btn-sm ${confirmDisable ? "btn-danger" : "btn-ghost text-danger"}`} onClick={() => void disable()} disabled={busy}>
              {busy ? "Disabling…" : confirmDisable ? "Confirm — stop importing" : "Disable GitOps"}
            </button>
            {confirmDisable && (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirmDisable(false)}>
                keep it
              </button>
            )}
          </div>
        </div>
      )}
      {enableOpen && (
        <Modal
          title="Enable GitOps"
          meta={`group: ${group.name}`}
          onClose={() => setEnableOpen(false)}
          w={460}
          footer={
            <>
              <span className="tt mr-auto">items imported on the next sync</span>
              <button type="button" className="btn" onClick={() => setEnableOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={!repoId || !filePath.trim() || busy} onClick={() => void enable()}>
                {busy ? "Enabling…" : "Enable"}
              </button>
            </>
          }
        >
          <Field label="repository">
            <select className="inp mono" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
              <option value="">— pick a repository —</option>
              {(repos ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} · {r.url}
                </option>
              ))}
            </select>
          </Field>
          <Field label="file path">
            <input className="inp mono" value={filePath} placeholder="groups/my-group.yaml" onChange={(e) => setFilePath(e.target.value)} />
          </Field>
          <span className="text-[11px] text-text-3">The file replaces what is declared here; edits made in LabDog are overwritten on the next import.</span>
        </Modal>
      )}
    </Panel>
  )
}

/* ── members ─────────────────────────────────────────────────────────── */

function MembersTab({
  group,
  members,
  hosts,
  groups,
  onChanged,
}: {
  group: HostGroup
  members: HostSummary[]
  hosts: HostSummary[] | undefined
  groups: GroupSummary[]
  onChanged: () => Promise<unknown>
}) {
  const router = useRouter()
  const [picking, setPicking] = useState(false)
  const [q, setQ] = useState("")
  const [busy, setBusy] = useState(false)

  const avail = useMemo(() => (hosts ?? []).filter((h) => !h.group_ids.includes(group.id)).sort(byHostname), [hosts, group.id])
  const ql = q.trim().toLowerCase()
  const filtered = ql ? avail.filter((h) => h.hostname.toLowerCase().includes(ql) || h.ip_address.includes(ql)) : avail
  const overrides = (h: HostSummary) => Object.values(h.override_counts).reduce((a, b) => a + b, 0)
  const otherGroups = (h: HostSummary) => groups.filter((g) => g.id !== group.id && h.group_ids.includes(g.id)).sort((a, b) => b.priority - a.priority)

  const add = async (ids: number[]) => {
    if (ids.length === 0) return
    setBusy(true)
    try {
      await apiFetch(`/api/groups/${group.id}/hosts`, { method: "POST", json: { host_ids: ids } })
      await onChanged()
      showSuccess(`${plural(ids.length, "host")} added to ${group.name} — they re-merge on their next plan`)
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to add hosts")
    } finally {
      setBusy(false)
    }
  }
  const remove = async (h: HostSummary) => {
    setBusy(true)
    try {
      await apiFetch(`/api/groups/${group.id}/hosts`, { method: "DELETE", json: { host_ids: [h.id] } })
      await onChanged()
      showSuccess(`${h.hostname} removed from ${group.name} — it drops this group's items on its next plan`)
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to remove host")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="scroll flex flex-1 flex-col gap-2.5 p-3.5">
      <div className="flex flex-wrap items-center gap-[9px]">
        <span className="tt">{plural(members.length, "host")} inherit{members.length === 1 ? "s" : ""} this group</span>
        {!picking && avail.length > 0 && (
          <button type="button" className="btn btn-sm ml-auto" onClick={() => setPicking(true)}>
            + add hosts
          </button>
        )}
        {!picking && hosts && avail.length === 0 && <span className="tt ml-auto text-text-faint">every host is already a member</span>}
      </div>
      {picking && (
        <div className="rounded-r border border-line bg-surface-2">
          <div className="flex items-center gap-1.5 border-b border-line-faint p-[7px]">
            <input className="inp mono" autoFocus value={q} placeholder="filter hosts…" onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} aria-label="filter hosts to add" />
            <button type="button" className="btn btn-sm" disabled={!filtered.length || busy} onClick={() => void add(filtered.map((h) => h.id))}>
              add {filtered.length}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => {
                setPicking(false)
                setQ("")
              }}
            >
              done
            </button>
          </div>
          <div className="scroll max-h-[168px] p-1">
            {filtered.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">{avail.length ? "No match." : "Every host is already a member."}</div>}
            {filtered.map((h) => (
              <label key={h.id} className="flex cursor-pointer items-center gap-2 rounded px-[5px] py-[3px]">
                <input type="checkbox" checked={false} disabled={busy} onChange={() => void add([h.id])} style={{ accentColor: "var(--accent)" }} aria-label={`add ${h.hostname}`} />
                <span className="mono flex-1 text-[11.5px]">{h.hostname}</span>
                <span className="mono text-[10.5px] text-text-faint">{h.ip_address}</span>
              </label>
            ))}
          </div>
        </div>
      )}
      <Table
        cols={[
          { k: "name", label: "host", w: "minmax(130px,1fr)", sortable: false, cell: (h: HostSummary) => <span className="mono font-medium text-text">{h.hostname}</span> },
          { k: "status", label: "status", w: "104px", sortable: false, cell: (h) => <Status s={h.sync_status} /> },
          { k: "ip", label: "ip", w: "124px", sortable: false, cell: (h) => <span className="mono text-[11.5px]">{h.ip_address}</span> },
          {
            k: "groups",
            label: "other groups",
            w: "minmax(150px,1.4fr)",
            sortable: false,
            cell: (h) => {
              const og = otherGroups(h)
              return og.length ? (
                <span className="flex min-w-0 gap-[3px]">
                  {og.map((g, i) => (
                    <Tag key={g.id} shrink={i === og.length - 1} title={`priority ${g.priority}`} onClick={(e) => { e.stopPropagation(); router.push(`/groups/${g.id}`) }}>
                      {g.name}
                    </Tag>
                  ))}
                </span>
              ) : (
                <span className="text-text-faint">—</span>
              )
            },
          },
          {
            k: "ov",
            label: "overrides",
            w: "90px",
            right: true,
            sortable: false,
            cell: (h) => {
              const n = overrides(h)
              return n ? (
                <Tag tone="accent" title="this host's own items — evaluated before every group">
                  {n}
                </Tag>
              ) : (
                <span className="text-text-faint">—</span>
              )
            },
          },
          {
            k: "rm",
            label: "",
            w: "80px",
            right: true,
            sortable: false,
            cell: (h) => (
              <button
                type="button"
                className="btn btn-sm btn-ghost text-danger"
                disabled={busy}
                onClick={(e) => {
                  e.stopPropagation()
                  void remove(h)
                }}
              >
                remove
              </button>
            ),
          },
        ]}
        rows={members}
        keyOf={(h) => h.id}
        onRowClick={(h) => router.push(`/hosts/${h.id}`)}
        loading={!hosts}
        empty="No hosts are members of this group. Add some above — they inherit its declared state on their next plan."
      />
    </div>
  )
}
