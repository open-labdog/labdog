"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { X } from "lucide-react"
import { Input } from "@/components/ui/input"
import { apiFetch } from "@/lib/api"
import type { Host } from "@/lib/types"

interface HostComboboxProps {
  value: number | null | undefined
  onChange: (id: number | null) => void
  placeholder?: string
  disabled?: boolean
}

export function HostCombobox({ value, onChange, placeholder = "Select host…", disabled = false }: HostComboboxProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const containerRef = useRef<HTMLDivElement>(null)

  const { data: hosts = [] } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch("/api/hosts"),
  })

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [])

  const selected = useMemo(() => hosts.find((h) => h.id === value) ?? null, [hosts, value])
  const filtered = hosts.filter(
    (h) =>
      h.hostname.toLowerCase().includes(search.toLowerCase()) ||
      h.ip_address.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        className="flex min-h-[36px] w-full items-center justify-between rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white disabled:opacity-50"
      >
        {selected ? (
          <span className="flex items-center gap-2">
            {selected.hostname}
            <span className="text-slate-400 text-xs">({selected.ip_address})</span>
            <span
              role="button"
              aria-label="Clear"
              onClick={(e) => {
                e.stopPropagation()
                onChange(null)
              }}
              className="ml-1 inline-flex items-center rounded hover:bg-slate-700 p-0.5"
            >
              <X className="h-3 w-3" />
            </span>
          </span>
        ) : (
          <span className="text-slate-400">{placeholder}</span>
        )}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-slate-700 bg-slate-900 shadow-lg">
          <div className="p-2 border-b border-slate-800">
            <Input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search hosts…"
              className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500 h-8"
            />
          </div>
          <ul className="max-h-60 overflow-auto py-1">
            {filtered.length === 0 && (
              <li className="px-3 py-2 text-sm text-slate-400">No hosts found</li>
            )}
            {filtered.map((h) => (
              <li key={h.id}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(h.id)
                    setOpen(false)
                    setSearch("")
                  }}
                  className={`flex w-full items-center justify-between px-3 py-2 text-sm text-left hover:bg-slate-800 ${
                    h.id === value ? "bg-slate-800" : ""
                  }`}
                >
                  <span className="text-white">{h.hostname}</span>
                  <span className="text-slate-400 text-xs">{h.ip_address}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
