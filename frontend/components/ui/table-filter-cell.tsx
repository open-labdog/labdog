"use client"

import { useEffect, useRef, useState } from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import type { FilterSpec } from "@/hooks/use-table-state"

type Props = {
  spec: FilterSpec
  value: unknown
  onChange: (value: unknown) => void
  autoOptions?: { label: string; value: string }[]
}

export function TableFilterCell({ spec, value, onChange, autoOptions }: Props) {
  if (spec.type === "text") {
    return <TextFilter value={(value as string) ?? ""} onChange={onChange} placeholder={spec.placeholder} />
  }
  if (spec.type === "enum") {
    const options = spec.options ?? autoOptions ?? []
    return <EnumFilter options={options} value={(value as string[]) ?? []} onChange={onChange} />
  }
  if (spec.type === "boolean") {
    return (
      <BooleanFilter
        value={(value as "" | "yes" | "no") ?? ""}
        onChange={onChange}
        trueLabel={spec.trueLabel ?? "Yes"}
        falseLabel={spec.falseLabel ?? "No"}
      />
    )
  }
  if (spec.type === "dateRange") {
    return <DateRangeFilter value={(value as { from: string; to: string }) ?? { from: "", to: "" }} onChange={onChange} />
  }
  if (spec.type === "custom") {
    return <>{spec.render(value, onChange)}</>
  }
  return null
}

function TextFilter({ value, onChange, placeholder = "Search…" }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [local, setLocal] = useState(value)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => { setLocal(value) }, [value])

  function update(next: string) {
    setLocal(next)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => onChange(next), 150)
  }

  return (
    <div className="relative">
      <Input
        type="text"
        placeholder={placeholder}
        value={local}
        onChange={(e) => update(e.target.value)}
        className="h-7 text-xs pr-6"
      />
      {local && (
        <button
          type="button"
          onClick={() => update("")}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 text-xs leading-none"
          aria-label="Clear filter"
        >
          ×
        </button>
      )}
    </div>
  )
}

function EnumFilter({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: string }[]
  value: string[]
  onChange: (v: string[]) => void
}) {
  function toggle(v: string) {
    onChange(value.includes(v) ? value.filter(x => x !== v) : [...value, v])
  }

  if (options.length === 0) {
    return <div className="px-2 py-1.5 text-xs text-slate-500">No options</div>
  }

  return (
    <ul className="py-1">
      {options.map(o => (
        <li key={o.value}>
          <button
            type="button"
            onClick={() => toggle(o.value)}
            className="flex w-full items-center gap-2 px-2 py-1 text-left text-xs hover:bg-slate-800"
          >
            <input
              type="checkbox"
              checked={value.includes(o.value)}
              readOnly
              className="pointer-events-none"
            />
            <span className="truncate text-slate-200">{o.label}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function BooleanFilter({
  value,
  onChange,
  trueLabel,
  falseLabel,
}: {
  value: "" | "yes" | "no"
  onChange: (v: "" | "yes" | "no") => void
  trueLabel: string
  falseLabel: string
}) {
  const opts: { value: "yes" | "no"; label: string }[] = [
    { value: "yes", label: trueLabel },
    { value: "no", label: falseLabel },
  ]
  return (
    <div className="inline-flex rounded-lg border border-input bg-transparent h-7 overflow-hidden">
      {opts.map(o => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(value === o.value ? "" : o.value)}
          className={cn(
            "px-2 text-xs transition-colors",
            value === o.value ? "bg-primary text-primary-foreground" : "text-slate-400 hover:bg-muted"
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function DateRangeFilter({
  value,
  onChange,
}: {
  value: { from: string; to: string }
  onChange: (v: { from: string; to: string }) => void
}) {
  return (
    <div className="flex gap-1">
      <input
        type="date"
        value={value.from}
        onChange={(e) => onChange({ ...value, from: e.target.value })}
        className="h-7 w-full rounded-lg border border-input bg-transparent px-1.5 text-xs text-foreground"
      />
      <input
        type="date"
        value={value.to}
        onChange={(e) => onChange({ ...value, to: e.target.value })}
        className="h-7 w-full rounded-lg border border-input bg-transparent px-1.5 text-xs text-foreground"
      />
    </div>
  )
}
