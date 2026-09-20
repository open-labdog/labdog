"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Command } from "cmdk"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { apiFetch } from "@/lib/api"
import { queueHostStateCollection } from "@/lib/collect-state"
import { statusDef } from "@/lib/fleet"
import { MODULES } from "@/lib/modules"
import { showError, showSuccess } from "@/lib/toast"
import type { Host, HostGroup } from "@/lib/types"
import { Kbd } from "@/components/ld"
import { ZONE_DEFS } from "./zones"

interface Entry {
  group: "destinations" | "config" | "hosts" | "groups" | "verbs"
  label: string
  meta?: string
  /** What cmdk matches against; the label plus anything else worth typing. */
  keywords: string[]
  run: () => void
  /** Shown before anything is typed. Everything else needs a query. */
  defaultVisible?: boolean
}

const SETTINGS_SECTIONS = [
  ["integrations", "Integrations"],
  ["ai", "AI"],
  ["access", "Access"],
  ["fleet", "Fleet defaults"],
  ["system", "System"],
] as const

/**
 * The palette is infrastructure, not a feature. It indexes every
 * destination, every host, every group, every module × scope pair, and
 * a handful of verbs — that is what lets the rail stay at five entries:
 * the rail covers the cold path, the palette covers the fast path.
 */
export function Palette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter()
  const [q, setQ] = useState("")

  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    enabled: open,
  })
  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
    enabled: open,
  })

  const entries = useMemo<Entry[]>(() => {
    const go = (href: string) => () => router.push(href)
    const out: Entry[] = []

    for (const z of ZONE_DEFS) {
      for (const it of z.items) {
        out.push({ group: "destinations", label: `${z.label} · ${it.label}`, keywords: [z.label, it.label], run: go(it.href), defaultVisible: true })
      }
    }
    for (const [k, label] of SETTINGS_SECTIONS) {
      out.push({ group: "destinations", label: `Settings · ${label}`, keywords: ["settings", label], run: go(`/settings?section=${k}`), defaultVisible: true })
    }

    for (const m of MODULES) {
      out.push({ group: "config", label: `${m.label} — fleet`, meta: "every scope", keywords: [m.label, m.id, "fleet", "config"], run: go(`/config/${m.id}`) })
      for (const g of groups ?? []) {
        out.push({
          group: "config",
          label: `${m.label} — group: ${g.name}`,
          meta: "desired",
          keywords: [m.label, m.id, g.name, "group", "config"],
          run: go(`/config/${m.id}?scope=group:${g.id}`),
        })
      }
    }

    for (const h of hosts ?? []) {
      const st = statusDef(h.sync_status)
      out.push({ group: "hosts", label: h.hostname, meta: `${h.ip_address} · ${st.label}`, keywords: [h.hostname, h.ip_address, st.label], run: go(`/hosts/${h.id}`) })
    }

    for (const g of groups ?? []) {
      const n = hosts?.filter((h) => h.group_ids.includes(g.id)).length
      out.push({ group: "groups", label: g.name, meta: `priority ${g.priority}${n != null ? ` · ${n} hosts` : ""}`, keywords: [g.name, "group", g.category ?? ""], run: go(`/groups/${g.id}`) })
    }

    const driftCheckFleet = async () => {
      const all = hosts ?? []
      if (all.length === 0) {
        showError("No hosts to check")
        return
      }
      const results = await Promise.allSettled(all.map((h) => queueHostStateCollection(h.id)))
      const failed = results.filter((r) => r.status === "rejected").length
      if (failed) showError(`State collection queued for ${all.length - failed} of ${all.length} hosts`)
      else showSuccess(`State collection queued for ${all.length} hosts`)
    }

    out.push(
      { group: "verbs", label: "Plan a sync…", meta: "creates a plan, nothing applied", keywords: ["plan", "sync", "apply"], run: go("/plans"), defaultVisible: true },
      { group: "verbs", label: "Check the fleet for drift", meta: `${hosts?.length ?? "all"} hosts · read-only`, keywords: ["drift", "check", "collect", "state"], run: () => void driftCheckFleet(), defaultVisible: true },
      { group: "verbs", label: "Approve pending hosts", meta: "discovery queue", keywords: ["approve", "pending", "discovery"], run: go("/discovery"), defaultVisible: true },
      { group: "verbs", label: "Start a scan", meta: "discovery is read-only", keywords: ["scan", "discover", "network"], run: go("/discovery?tab=scan"), defaultVisible: true },
      { group: "verbs", label: "Add a host", keywords: ["add", "host", "new"], run: go("/hosts/new"), defaultVisible: true },
      { group: "verbs", label: "New group", keywords: ["new", "group", "create"], run: go("/groups/new"), defaultVisible: true },
    )
    for (const h of hosts ?? []) {
      out.push({ group: "verbs", label: `Open terminal — ${h.hostname}`, meta: "SSH via control plane", keywords: ["terminal", "ssh", h.hostname], run: go(`/hosts/${h.id}/terminal`) })
    }
    return out
  }, [hosts, groups, router])

  const visible = q.trim() ? entries : entries.filter((e) => e.defaultVisible)
  const groupsInOrder: Entry["group"][] = ["destinations", "config", "hosts", "groups", "verbs"]

  const close = () => {
    setQ("")
    onClose()
  }

  return (
    <DialogPrimitive.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) close()
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fade fixed inset-0 z-[100]" style={{ background: "var(--scrim)" }} />
        <DialogPrimitive.Popup
          aria-label="Command palette"
          className="fade fixed left-1/2 top-[9vh] z-[101] flex max-h-[74vh] w-[calc(100%-28px)] max-w-[560px] -translate-x-1/2 flex-col overflow-hidden rounded-r-lg border border-line-strong bg-surface shadow-ld outline-none"
        >
          <Command
            label="Command palette"
            filter={(value, search, keywords) => {
              const hay = `${value} ${(keywords ?? []).join(" ")}`.toLowerCase()
              const terms = search.toLowerCase().split(/\s+/).filter(Boolean)
              return terms.every((t) => hay.includes(t)) ? 1 : 0
            }}
            className="flex min-h-0 flex-col"
          >
            <div className="flex items-center gap-[9px] border-b border-line px-[13px] py-[11px]">
              <span className="text-xs text-text-faint">›</span>
              <Command.Input
                autoFocus
                value={q}
                onValueChange={setQ}
                placeholder="Go to a host, module, scope — or run a command"
                className="mono flex-1 border-0 bg-transparent text-[13px] text-text outline-none placeholder:text-text-faint"
              />
              <Kbd>esc</Kbd>
            </div>
            <Command.List className="scroll min-h-0 flex-1 py-1.5">
              <Command.Empty className="p-[22px] text-center text-xs text-text-3">Nothing matches &ldquo;{q}&rdquo;.</Command.Empty>
              {groupsInOrder.map((g) => {
                const rows = visible.filter((e) => e.group === g)
                if (rows.length === 0) return null
                return (
                  <Command.Group key={g} heading={<span className="tt">{g}</span>} className="[&_[cmdk-group-heading]]:px-[13px] [&_[cmdk-group-heading]]:py-[5px]">
                    {rows.map((r) => (
                      <Command.Item
                        key={`${r.group}:${r.label}`}
                        value={`${r.group}:${r.label}`}
                        keywords={r.keywords}
                        onSelect={() => {
                          r.run()
                          close()
                        }}
                        className="flex w-full cursor-pointer items-center gap-2.5 px-[13px] py-1.5 text-left text-text-2 data-[selected=true]:bg-ld-accent-soft data-[selected=true]:text-text data-[selected=true]:shadow-[inset_2px_0_0_var(--accent)]"
                      >
                        <span className="mono trunc flex-1 text-xs">{r.label}</span>
                        {r.meta && <span className="tt text-[9px]">{r.meta}</span>}
                      </Command.Item>
                    ))}
                  </Command.Group>
                )
              })}
            </Command.List>
            <div className="flex gap-3.5 border-t border-line bg-surface-2 px-[13px] py-[7px]">
              <span className="tt">↑↓ move</span>
              <span className="tt">↵ open</span>
              <span className="tt">esc close</span>
            </div>
          </Command>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
