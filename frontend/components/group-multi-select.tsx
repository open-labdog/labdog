"use client"

import { useEffect, useRef, useState } from "react"
import { X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { HostGroup } from "@/lib/types"

interface GroupMultiSelectProps {
  groups: HostGroup[]
  selected: number[]
  onChange: (ids: number[]) => void
  disabled?: boolean
  label?: string
}

export function GroupMultiSelect({
  groups,
  selected,
  onChange,
  disabled = false,
  label = "Groups",
}: GroupMultiSelectProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const filtered = groups.filter((g) =>
    g.name.toLowerCase().includes(search.toLowerCase()) ||
    (g.description?.toLowerCase().includes(search.toLowerCase()) ?? false)
  )

  const selectedGroups = groups.filter((g) => selected.includes(g.id))

  function toggle(id: number) {
    if (disabled) return
    if (selected.includes(id)) {
      onChange(selected.filter((s) => s !== id))
    } else {
      onChange([...selected, id])
    }
  }

  function remove(id: number) {
    if (disabled) return
    onChange(selected.filter((s) => s !== id))
  }

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div ref={containerRef} className="relative">
        <button
          type="button"
          onClick={() => !disabled && setOpen(!open)}
          disabled={disabled}
          className="flex min-h-[36px] w-full flex-wrap items-center gap-1.5 rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 disabled:pointer-events-none disabled:opacity-50"
        >
          {selectedGroups.length === 0 && (
            <span className="text-muted-foreground">Select groups...</span>
          )}
          {selectedGroups.map((g) => (
            <Badge
              key={g.id}
              variant="secondary"
              className="gap-1 pr-1"
            >
              {g.name}
              <span
                role="button"
                tabIndex={0}
                className="ml-0.5 rounded-full p-0.5 hover:bg-muted-foreground/20 cursor-pointer"
                onMouseDown={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  remove(g.id)
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    e.stopPropagation()
                    remove(g.id)
                  }
                }}
              >
                <X className="h-3 w-3" />
              </span>
            </Badge>
          ))}
        </button>

        {open && (
          <div className="absolute z-50 mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 shadow-lg">
            <div className="p-2">
              <Input
                placeholder="Search groups..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
                className="h-7 text-sm"
              />
            </div>
            <div className="max-h-48 overflow-y-auto px-1 pb-1">
              {filtered.length === 0 && (
                <div className="px-2 py-3 text-center text-sm text-muted-foreground">
                  No groups found
                </div>
              )}
              {filtered.map((group) => (
                <label
                  key={group.id}
                  className="flex items-center gap-2 cursor-pointer rounded-md px-2 py-1.5 hover:bg-slate-800"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(group.id)}
                    onChange={() => toggle(group.id)}
                    className="rounded border-input"
                  />
                  <span className="text-sm text-foreground">{group.name}</span>
                  {group.description && (
                    <span className="text-xs text-muted-foreground truncate">— {group.description}</span>
                  )}
                </label>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
