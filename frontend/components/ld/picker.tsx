"use client"

import { useId, useState } from "react"
import { Command } from "cmdk"

export interface PickerOption {
  value: number
  label: string
  /** Shown after the label, dimmer, and searched like it — an IP address. */
  meta?: string
}

/**
 * A select you can type into. Every word typed must appear somewhere in an
 * option's label or meta, so "web 10.0.5" narrows to web hosts on that
 * subnet — which a native `<select>`, whose type-ahead only jumps to the
 * first option starting with the typed prefix, cannot do.
 *
 * The list opens in the flow below the input rather than as a popover, for
 * the reason `Help` gives: inside a modal a popover sits under the modal's
 * own layer. Built on cmdk, which the command palette already uses, so the
 * arrow keys and Enter behave the same in both.
 */
export function Picker({
  options,
  value,
  onChange,
  placeholder,
  disabled,
  empty = "Nothing matches.",
  testId,
  className = "",
}: {
  options: PickerOption[]
  value: number | null
  onChange: (value: number | null) => void
  placeholder: string
  disabled?: boolean
  empty?: string
  testId?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const listId = useId()
  const selected = options.find((o) => o.value === value) ?? null
  const shown = selected ? (selected.meta ? `${selected.label} · ${selected.meta}` : selected.label) : ""

  const pick = (o: PickerOption) => {
    onChange(o.value)
    setQuery("")
    setOpen(false)
  }

  return (
    <Command
      label={placeholder}
      shouldFilter={open}
      filter={(_value, search, keywords) => {
        const hay = (keywords ?? []).join(" ").toLowerCase()
        const terms = search.toLowerCase().split(/\s+/).filter(Boolean)
        return terms.every((t) => hay.includes(t)) ? 1 : 0
      }}
      className={`flex flex-col ${className}`}
      onKeyDown={(e) => {
        // Closes the list, not the modal around it.
        if (e.key === "Escape" && open) {
          e.preventDefault()
          e.stopPropagation()
          setOpen(false)
          setQuery("")
        }
      }}
    >
      <Command.Input
        value={open ? query : shown}
        onValueChange={(q) => {
          setQuery(q)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setOpen(false)
          setQuery("")
        }}
        onClick={() => setOpen(true)}
        placeholder={open && selected ? shown : placeholder}
        disabled={disabled}
        aria-expanded={open}
        aria-controls={listId}
        data-testid={testId}
        className="inp mono"
      />
      {open && !disabled && (
        <Command.List
          id={listId}
          // Keeps focus in the input, so clicking an option does not blur
          // it and close the list before the click lands.
          onMouseDown={(e) => e.preventDefault()}
          className="scroll mt-1 max-h-[200px] rounded-r border border-line-strong bg-surface p-1"
        >
          <Command.Empty className="px-2 py-[5px] text-xs text-text-3">{empty}</Command.Empty>
          {options.map((o) => (
            <Command.Item
              key={o.value}
              value={String(o.value)}
              keywords={o.meta ? [o.label, o.meta] : [o.label]}
              onSelect={() => pick(o)}
              className="flex w-full cursor-pointer items-center gap-2 rounded-[3px] px-2 py-[5px] text-left text-xs text-text-2 data-[selected=true]:bg-ld-accent-soft data-[selected=true]:text-text"
            >
              <span className="mono trunc">{o.label}</span>
              {o.meta && <span className="mono trunc text-text-faint">{o.meta}</span>}
              {o.value === value && <span className="ml-auto text-[10px] text-text-faint">selected</span>}
            </Command.Item>
          ))}
        </Command.List>
      )}
    </Command>
  )
}
