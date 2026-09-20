"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { MODULES } from "@/lib/modules"
import { showError, showSuccess } from "@/lib/toast"
import type { GroupSummary, Host } from "@/lib/types"
import { Dot, Modal, Tag } from "@/components/ld"

const CATEGORIES = ["baseline", "environment", "role", "exposure", "platform", "policy"]

/** Field label with an optional lower-case hint after it. */
function L({ children, hint }: { children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <span className="tt flex items-baseline gap-1.5">
      {children}
      {hint && <span className="normal-case tracking-normal text-text-faint">{hint}</span>}
    </span>
  )
}

/**
 * Group editor — change what a group *is*. A priority move is the
 * dangerous edit: it changes who wins the merge on every host the group
 * shares with another, so the merge ladder and every winner/loser flip
 * the move causes are shown before Save. Saving records desired state;
 * nothing reaches a host until a plan runs.
 */
export function GroupEditor({
  group,
  groups,
  hosts,
  onClose,
}: {
  /** null creates a new group. */
  group: GroupSummary | null
  groups: GroupSummary[]
  hosts: Host[]
  onClose: () => void
}) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const creating = !group
  const [f, setF] = useState(() => ({
    name: group?.name ?? "",
    priority: String(group?.priority ?? 55),
    category: group?.category ?? "role",
    desc: group?.description ?? "",
  }))
  const [confirmDel, setConfirmDel] = useState(false)
  const [addHosts, setAddHosts] = useState<number[]>([])
  const [picking, setPicking] = useState(false)
  const [q, setQ] = useState("")
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }))
  const prio = Math.max(1, Math.min(1000, parseInt(f.priority, 10) || 0))

  const members = group ? hosts.filter((h) => h.group_ids.includes(group.id)) : []
  const others = groups.filter((g) => !group || g.id !== group.id)
  const tie = others.find((g) => g.priority === prio)
  const moved = !!group && prio !== group.priority
  const declared = useMemo(() => (group ? MODULES.filter((m) => group.module_counts[m.countKey] > 0) : []), [group])

  /* who this group starts or stops beating, counted only where it matters:
     hosts shared with the other group, and a module both declare */
  const flips = useMemo(() => {
    if (!group) return []
    return others
      .map((o) => {
        const was = group.priority > o.priority
        const now = prio > o.priority
        if (was === now) return null
        const shared = hosts.filter((h) => h.group_ids.includes(group.id) && h.group_ids.includes(o.id)).length
        const mods = declared.filter((m) => o.module_counts[m.countKey] > 0)
        if (!shared || !mods.length) return null
        return { o, shared, mods, now }
      })
      .filter((x): x is { o: GroupSummary; shared: number; mods: typeof declared; now: boolean } => x !== null)
  }, [group, others, prio, hosts, declared])

  const ladder = useMemo(() => {
    const rows: { id: string; name: string; p: number; hosts: number; self?: boolean; ghost?: boolean }[] = others.map((g) => ({ id: String(g.id), name: g.name, p: g.priority, hosts: g.host_count }))
    rows.push({ id: "__self", name: f.name || "new group", p: prio, hosts: members.length, self: true })
    if (moved && group) rows.push({ id: "__was", name: f.name, p: group.priority, hosts: members.length, ghost: true })
    return rows.sort((a, b) => b.p - a.p || (a.ghost ? 1 : -1))
  }, [others, f.name, prio, members.length, moved, group])

  const nameErr = !f.name.trim() ? "required" : others.some((o) => o.name === f.name.trim()) ? "already taken" : null
  const valid = !nameErr && prio > 0 && !busy

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["groups"] }),
      queryClient.invalidateQueries({ queryKey: ["groups-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["hosts"] }),
      queryClient.invalidateQueries({ queryKey: ["hosts-summary"] }),
    ])
  }

  const save = async () => {
    setBusy(true)
    const body = { name: f.name.trim(), priority: prio, category: f.category, description: f.desc.trim() || null }
    try {
      if (creating) {
        await apiFetch("/api/groups", { method: "POST", json: body })
        showSuccess(`Group ${body.name} created — no hosts yet. Add members from the group or from Hosts.`)
      } else {
        await apiFetch(`/api/groups/${group.id}`, { method: "PUT", json: body })
        if (addHosts.length) await apiFetch(`/api/groups/${group.id}/hosts`, { method: "POST", json: { host_ids: addHosts } })
        showSuccess(
          moved
            ? `${body.name} saved at priority ${prio} — ${members.length + addHosts.length} hosts re-merge on their next plan.`
            : `${body.name} saved — desired state only, ${members.length + addHosts.length} hosts affected on the next plan.`,
        )
      }
      await invalidate()
      onClose()
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to save group")
    } finally {
      setBusy(false)
    }
  }

  const del = async () => {
    if (!group) return
    if (!confirmDel) {
      setConfirmDel(true)
      return
    }
    setBusy(true)
    try {
      await apiFetch(`/api/groups/${group.id}`, { method: "DELETE" })
      showSuccess(`Group ${group.name} deleted — ${members.length} host${members.length === 1 ? "" : "s"} dropped it`)
      await invalidate()
      onClose()
    } catch (e) {
      showError(e instanceof Error ? e.message : "Failed to delete group")
    } finally {
      setBusy(false)
    }
  }

  const staged = addHosts.map((id) => hosts.find((h) => h.id === id)).filter((h): h is Host => !!h)
  const avail = group ? hosts.filter((h) => !h.group_ids.includes(group.id) && !addHosts.includes(h.id)).sort((a, b) => a.hostname.localeCompare(b.hostname)) : []
  const ql = q.trim().toLowerCase()
  const filtered = ql ? avail.filter((h) => h.hostname.toLowerCase().includes(ql) || h.ip_address.includes(ql)) : avail

  return (
    <Modal
      w={660}
      onClose={onClose}
      title={creating ? "New group" : `Edit group · ${group.name}`}
      meta={creating ? "desired state" : `${members.length} hosts · priority ${group.priority}`}
      footer={
        <>
          {!creating && (
            <button type="button" className="btn btn-sm btn-ghost mr-auto text-danger" onClick={() => void del()} disabled={busy}>
              {confirmDel ? `Confirm delete — ${members.length} host${members.length === 1 ? "" : "s"} drop this group` : "Delete group"}
            </button>
          )}
          {creating && <span className="tt mr-auto">members are added after creating</span>}
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={!valid} onClick={() => void save()}>
            {busy ? "Saving…" : creating ? "Create group" : "Save group"}
          </button>
        </>
      }
    >
      <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 150px" }}>
        <label className="flex flex-col gap-[5px]">
          <L hint={nameErr && f.name ? <span className="text-danger">{nameErr}</span> : undefined}>name</L>
          <input className="inp mono" value={f.name} placeholder="group name" onChange={(e) => set("name", e.target.value)} style={nameErr && f.name ? { borderColor: "var(--danger)" } : undefined} />
        </label>
        <label className="flex flex-col gap-[5px]">
          <L>category</L>
          <select className="inp" value={f.category} onChange={(e) => set("category", e.target.value)}>
            {[...new Set([...CATEGORIES, ...groups.map((g) => g.category).filter((c): c is string => !!c)])].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="flex flex-col gap-[5px]">
        <L>what it&apos;s for</L>
        <input className="inp" value={f.desc} placeholder="one line — why does this group exist?" onChange={(e) => set("desc", e.target.value)} />
      </label>

      <div className="flex flex-col gap-[7px] rounded-r p-[11px]" style={{ border: `1px solid ${moved ? "var(--accent-line)" : "var(--border)"}`, background: moved ? "var(--accent-soft)" : "var(--surface-2)" }}>
        <L hint="higher wins the merge">priority</L>
        <div className="flex items-center gap-[11px]">
          <input className="inp mono num" type="number" min={1} max={1000} value={f.priority} onChange={(e) => set("priority", e.target.value)} style={{ width: 74, flexShrink: 0 }} />
          <input type="range" min={1} max={100} value={Math.min(prio, 100)} onChange={(e) => set("priority", e.target.value)} className="flex-1" style={{ accentColor: "var(--accent)" }} aria-label="priority" />
          {moved && group && (
            <Tag tone="accent">
              {group.priority} → {prio}
            </Tag>
          )}
        </div>
        {tie && (
          <div className="text-[11.5px] text-warn">
            Same priority as <span className="mono">{tie.name}</span> — ties break by an order nobody remembers. Pick a gap.
          </div>
        )}
        {!moved && !tie && (
          <div className="text-[11.5px] text-text-3">
            Higher priority is evaluated first. {creating ? "Baselines sit low (10–20), roles mid (30–50), policy high (90+)." : "A host's own overrides are still evaluated before every group."}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-[11px]">
        <div className="flex min-w-0 flex-col gap-1.5">
          <L hint="this group's slot in bold">merge ladder</L>
          <div className="scroll max-h-[178px] rounded-r border border-line bg-surface-2">
            <div className="flex items-center gap-2 border-b border-line-faint bg-surface-3 px-[9px] py-1">
              <span className="mono num w-[26px] text-right text-[11px] text-text-faint">host</span>
              <span className="mono trunc flex-1 text-[11.5px] text-text-3">host overrides — evaluated first</span>
            </div>
            {ladder.map((r) => (
              <div key={r.id} className="flex items-center gap-2 border-b border-line-faint px-[9px] py-1" style={{ background: r.self ? "var(--accent-soft)" : "transparent", opacity: r.ghost ? 0.45 : 1 }}>
                <span className="mono num w-[26px] text-right text-[11px]" style={{ color: r.self ? "var(--accent-ink)" : "var(--text-faint)" }}>
                  {r.p}
                </span>
                <span className="mono trunc flex-1 text-[11.5px]" style={{ color: r.self ? "var(--text)" : "var(--text-2)", fontWeight: r.self ? 600 : 400, textDecoration: r.ghost ? "line-through" : "none" }}>
                  {r.name}
                </span>
                <span className="mono num text-[10.5px] text-text-faint">{r.hosts}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex min-w-0 flex-col gap-1.5">
          <L hint={`${declared.length} configured`}>declares</L>
          <div className="flex flex-wrap items-center gap-1">
            {declared.map((m) => (
              <span key={m.id} title={m.blurb} className="inline-flex items-center whitespace-nowrap rounded-xl border border-ld-accent-line bg-ld-accent-soft px-[9px] py-[3px] text-[11.5px] text-text">
                <span className="mono">
                  {m.id} · {group?.module_counts[m.countKey]}
                </span>
              </span>
            ))}
            {declared.length === 0 && <span className="text-[11.5px] text-text-3">{creating ? "A new group declares nothing until you add config under Config." : "No modules configured for this group."}</span>}
          </div>
          <div className="text-[11px] text-text-3">Modules this group configures. Their contents are edited under Config, one module at a time.</div>
        </div>
      </div>

      {flips.length > 0 && (
        <div className="flex flex-col gap-1.5 rounded-r border border-warn bg-warn-soft p-[11px]">
          <span className="tt text-text">this move changes who wins</span>
          {flips.map(({ o, shared, mods, now }) => (
            <div key={o.id} className="flex items-baseline gap-[7px] text-[11.5px] text-text">
              <Dot tone={now ? "accent" : "warn"} />
              <span>
                <span className="mono">{f.name}</span> now {now ? "wins over" : "loses to"} <span className="mono">{o.name}</span> on <span className="mono num">{shared}</span> shared host{shared === 1 ? "" : "s"} — for <span className="mono">{mods.map((m) => m.id).join(", ")}</span>
              </span>
            </div>
          ))}
          <span className="text-[11px] text-text-2">Nothing reaches a host until you run a plan. The next plan will show the exact rules that change.</span>
        </div>
      )}

      {!creating && (
        <div className="flex flex-col gap-1.5">
          <L hint={`${members.length + staged.length} hosts${staged.length ? ` · ${staged.length} to add` : ""}`}>members</L>
          <div className="flex flex-wrap items-center gap-1">
            {members.slice(0, 12).map((h) => (
              <Tag
                key={h.id}
                onClick={() => {
                  onClose()
                  router.push(`/hosts/${h.id}`)
                }}
              >
                {h.hostname}
              </Tag>
            ))}
            {members.length > 12 && <Tag>+{members.length - 12}</Tag>}
            {members.length === 0 && staged.length === 0 && <span className="text-[11.5px] text-text-3">No hosts yet.</span>}
            {staged.map((h) => (
              <span key={h.id} className="inline-flex items-center gap-[5px] whitespace-nowrap rounded border border-ld-accent bg-ld-accent-soft py-0.5 pl-2 pr-[5px] text-[11.5px] text-text">
                <span className="mono">{h.hostname}</span>
                <button type="button" title={`don't add ${h.hostname}`} className="flex border-0 bg-transparent p-0 text-[13px] leading-none text-text-3" onClick={() => setAddHosts(addHosts.filter((x) => x !== h.id))}>
                  ×
                </button>
              </span>
            ))}
          </div>
          {avail.length > 0 && !picking && (
            <button type="button" className="btn btn-sm mt-0.5 self-start" onClick={() => setPicking(true)}>
              + add hosts
            </button>
          )}
          {avail.length > 0 && picking && (
            <div className="mt-0.5 rounded-r border border-line bg-surface-2">
              <div className="flex items-center gap-1.5 border-b border-line-faint p-[7px]">
                <input className="inp mono" autoFocus value={q} placeholder="filter hosts…" onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
                <button type="button" className="btn btn-sm" disabled={!filtered.length} onClick={() => setAddHosts([...new Set([...addHosts, ...filtered.map((h) => h.id)])])}>
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
              <div className="scroll max-h-[150px] p-1">
                {filtered.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">No match.</div>}
                {filtered.map((h) => (
                  <label key={h.id} className="flex cursor-pointer items-center gap-2 rounded px-[5px] py-[3px]">
                    <input type="checkbox" checked={false} onChange={() => setAddHosts([...addHosts, h.id])} style={{ accentColor: "var(--accent)" }} />
                    <span className="mono flex-1 text-[11.5px]">{h.hostname}</span>
                    <span className="mono text-[10.5px] text-text-faint">{h.ip_address}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          {staged.length > 0 && <div className="text-[11px] text-text-3">New members re-merge on save. Nothing applied until a plan runs.</div>}
        </div>
      )}
    </Modal>
  )
}
