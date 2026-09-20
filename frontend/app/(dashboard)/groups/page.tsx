"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { MODULES } from "@/lib/modules"
import { showError, showSuccess } from "@/lib/toast"
import type { GroupSummary, Host } from "@/lib/types"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { GroupEditor } from "@/components/group-editor"
import { Filter, PageHead, Table, Tag, type Sort } from "@/components/ld"

/**
 * Groups, in priority order — the order that makes the merge legible.
 * Click a row for the group; edit changes what the group is (name,
 * priority, category, members) with the merge consequences shown first.
 */
export default function GroupsPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [edit, setEdit] = useState<GroupSummary | "new" | null>(null)
  const [q, setQ] = useState("")
  const [category, setCategory] = useState("all")
  const [sort, setSort] = useState<Sort>({ k: "priority", dir: -1 })
  const [sel, setSel] = useState<Set<string | number>>(new Set())
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const { data: groups, isLoading, error } = useQuery<GroupSummary[]>({
    queryKey: ["groups-summary"],
    queryFn: () => apiFetch<GroupSummary[]>("/api/groups/summary"),
  })
  const { data: hosts } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })

  const all = useMemo(() => groups ?? [], [groups])
  const categories = useMemo(() => [...new Set(all.map((g) => g.category ?? "uncategorised"))].sort(), [all])
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = all.filter(
      (g) =>
        (category === "all" || (g.category ?? "uncategorised") === category) &&
        (!ql || g.name.toLowerCase().includes(ql) || (g.description ?? "").toLowerCase().includes(ql)),
    )
    const key: Record<string, (g: GroupSummary) => string | number> = {
      name: (g) => g.name,
      priority: (g) => g.priority,
      category: (g) => g.category ?? "",
      hosts: (g) => g.host_count,
      modules: (g) => MODULES.filter((m) => g.module_counts[m.countKey] > 0).length,
    }
    const f = key[sort.k] ?? key.priority
    return r.sort((a, b) => {
      const x = f(a)
      const y = f(b)
      return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
    })
  }, [all, q, category, sort])

  const drifted = (g: GroupSummary) => (hosts ?? []).filter((h) => h.group_ids.includes(g.id) && h.sync_status === "out_of_sync").length

  async function handleBulkDelete() {
    const ids = Array.from(sel).map(Number)
    setBulkDeleting(true)
    let success = 0
    let failed = 0
    for (const id of ids) {
      try {
        await apiFetch(`/api/groups/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
    }
    setBulkDeleting(false)
    setSel(new Set())
    await queryClient.invalidateQueries({ queryKey: ["groups-summary"] })
    await queryClient.invalidateQueries({ queryKey: ["groups"] })
    await queryClient.invalidateQueries({ queryKey: ["hosts"] })
    if (failed === 0) showSuccess(`Deleted ${success} group${success !== 1 ? "s" : ""}`)
    else showError(`Deleted ${success} of ${ids.length}. ${failed} failed.`)
    setBulkConfirmOpen(false)
  }

  return (
    <>
      <PageHead
        crumbs={[{ label: "fleet" }]}
        title={
          <>
            Groups <span className="mono num text-[12.5px] font-normal text-text-faint">{rows.length}</span>
          </>
        }
        sub="Evaluated strongest first: a host's own overrides come before every group, then groups from highest priority down. Click a row to open the group; edit to change the group itself."
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={() => setEdit("new")}>
            New group
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search groups..." aria-label="Search groups" className="inp mono" style={{ width: 200, fontSize: 11.5, padding: "4px 8px" }} />
          <Filter label="category" value={category} onChange={setCategory} options={categories.map((c) => ({ k: c, label: c, n: all.filter((g) => (g.category ?? "uncategorised") === c).length }))} />
          <span className="tt ml-auto">priority is the merge order — higher wins</span>
        </div>
      </PageHead>

      {error ? (
        <div className="p-6 text-center text-xs text-danger">Failed to load groups</div>
      ) : (
        <Table
          cols={[
            { k: "name", label: "group", w: "minmax(140px,1fr)", cell: (g) => <span className="mono font-medium text-text">{g.name}</span> },
            { k: "priority", label: "priority", w: "84px", right: true, cell: (g) => <span className="mono num" style={{ color: g.priority >= 60 ? "var(--warn)" : "var(--text-2)" }}>{g.priority}</span> },
            { k: "category", label: "category", w: "120px", cell: (g) => (g.category ? <Tag>{g.category}</Tag> : <span className="text-text-faint">—</span>) },
            { k: "hosts", label: "hosts", w: "70px", right: true, cell: (g) => <span className="mono num">{g.host_count}</span> },
            {
              k: "drift",
              label: "drifted",
              w: "70px",
              right: true,
              sortable: false,
              cell: (g) => {
                const d = drifted(g)
                return d ? <span className="mono num font-semibold text-warn">{d}</span> : <span className="text-text-faint">—</span>
              },
            },
            {
              k: "modules",
              label: "declares",
              w: "minmax(180px,1.4fr)",
              cell: (g) => {
                const mods = MODULES.filter((m) => g.module_counts[m.countKey] > 0)
                if (mods.length === 0) return <span className="text-[11px] italic text-text-faint">nothing yet</span>
                return (
                  <span className="flex min-w-0 gap-[3px]">
                    {mods.slice(0, 5).map((m, i, arr) => (
                      <Tag key={m.id} shrink={i === arr.length - 1} onClick={(e) => { e.stopPropagation(); router.push(`/config/${m.id}?scope=group:${g.id}`) }} title={`${g.module_counts[m.countKey]} ${m.unit} — open in Config`}>
                        {m.id} {g.module_counts[m.countKey]}
                      </Tag>
                    ))}
                    {mods.length > 5 && <span className="mono num text-[10px] text-text-faint">+{mods.length - 5}</span>}
                  </span>
                )
              },
            },
            {
              k: "gitops",
              label: "",
              w: "80px",
              sortable: false,
              cell: (g) => (g.gitops_enabled ? <Tag tone={g.gitops_status === "error" ? "danger" : g.gitops_status === "synced" ? "ok" : "sync"}>gitops</Tag> : null),
            },
            { k: "desc", label: "what it's for", w: "minmax(150px,1.2fr)", sortable: false, cell: (g) => <span className="text-[11.5px] text-text-3">{g.description ?? ""}</span> },
            {
              k: "edit",
              label: "",
              w: "62px",
              right: true,
              sortable: false,
              cell: (g) => (
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  title={`edit ${g.name} — name, priority, category, members`}
                  onClick={(e) => {
                    e.stopPropagation()
                    setEdit(g)
                  }}
                >
                  edit
                </button>
              ),
            },
          ]}
          rows={rows}
          keyOf={(g) => g.id}
          sort={sort}
          onSort={(k) => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : k === "priority" ? -1 : 1 }))}
          selected={sel}
          onSelect={setSel}
          onRowClick={(g) => router.push(`/groups/${g.id}`)}
          loading={isLoading}
          empty={
            all.length === 0 ? (
              <>
                No groups yet.{" "}
                <button type="button" className="underline" onClick={() => setEdit("new")}>
                  Create the first one
                </button>{" "}
                — a baseline every host joins is the usual start.
              </>
            ) : (
              "No group in that category."
            )
          }
        />
      )}

      {sel.size > 0 && (
        <div className="fade flex shrink-0 flex-wrap items-center gap-[9px] border-t border-ld-accent-line bg-ld-accent-soft px-3.5 py-[9px]">
          <span className="mono num text-xs font-semibold">{sel.size} selected</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSel(new Set())}>
            clear
          </button>
          <div className="ml-auto flex flex-wrap gap-[7px]">
            <button type="button" className="btn btn-sm btn-danger" disabled={bulkDeleting} onClick={() => setBulkConfirmOpen(true)}>
              Delete selected
            </button>
            {sel.size === 1 && (
              <button type="button" className="btn btn-sm btn-primary" onClick={() => router.push(`/plans?scope=group:${Array.from(sel)[0]}`)}>
                Plan sync
              </button>
            )}
          </div>
        </div>
      )}

      {edit && <GroupEditor group={edit === "new" ? null : edit} groups={all} hosts={hosts ?? []} onClose={() => setEdit(null)} />}

      <ConfirmDialog
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Delete ${sel.size} ${sel.size === 1 ? "group" : "groups"}?`}
        description="Hosts drop the group and lose its declared state on their next plan. This cannot be undone."
        confirmLabel="Delete All"
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />
    </>
  )
}
