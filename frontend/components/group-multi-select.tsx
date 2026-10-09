"use client"

import { useState } from "react"
import { Field } from "@/components/ld"
import type { HostGroup } from "@/lib/types"

interface GroupMultiSelectProps {
  groups: HostGroup[]
  selected: number[]
  onChange: (ids: number[]) => void
  disabled?: boolean
  label?: string
}

/**
 * An always-visible checkbox box, not a dropdown — the design has no
 * popover primitive that would sit above a modal's own layer, and a plain
 * list reads fine at the handful of groups most fleets have.
 */
export function GroupMultiSelect({ groups, selected, onChange, disabled = false, label = "groups" }: GroupMultiSelectProps) {
  const [q, setQ] = useState("")
  const ql = q.trim().toLowerCase()
  const filtered = ql ? groups.filter((g) => g.name.toLowerCase().includes(ql) || (g.description ?? "").toLowerCase().includes(ql)) : groups

  function toggle(id: number) {
    if (disabled) return
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id])
  }

  return (
    <Field as="div" label={label} hint={`${selected.length} selected`}>
      <div className="rounded-r border border-line bg-surface-2">
        {groups.length > 6 && (
          <div className="border-b border-line-faint p-[7px]">
            <input className="inp" placeholder="filter groups…" value={q} onChange={(e) => setQ(e.target.value)} disabled={disabled} />
          </div>
        )}
        <div className="scroll max-h-[168px] p-1">
          {filtered.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">No groups found.</div>}
          {filtered.map((g) => (
            <label key={g.id} className="flex cursor-pointer items-center gap-2 rounded px-[5px] py-[3px]">
              <input type="checkbox" checked={selected.includes(g.id)} disabled={disabled} onChange={() => toggle(g.id)} style={{ accentColor: "var(--accent)" }} />
              <span className="flex-1 text-[11.5px] text-text">{g.name}</span>
              {g.description && <span className="trunc text-[10.5px] text-text-faint" style={{ maxWidth: 160 }}>{g.description}</span>}
            </label>
          ))}
        </div>
      </div>
    </Field>
  )
}
