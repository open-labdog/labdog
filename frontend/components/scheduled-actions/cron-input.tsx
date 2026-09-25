"use client"

import { useEffect, useState } from "react"
import { apiFetch } from "@/lib/api"
import { cronToHuman } from "@/lib/cron"
import { Field } from "@/components/ld"
import type { ValidateCronResponse } from "@/lib/types"

interface CronInputProps {
  value: string
  onChange: (next: string) => void
}

// Custom is the sentinel — selecting it leaves the cron input as the
// authoritative source. Any other value writes the cron string verbatim.
const CUSTOM = "__custom__"

const PRESETS: { label: string; cron: string }[] = [
  { label: "Every 15 minutes", cron: "*/15 * * * *" },
  { label: "Every 30 minutes", cron: "*/30 * * * *" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Nightly (03:00 UTC)", cron: "0 3 * * *" },
  { label: "Weekdays 03:00 UTC", cron: "0 3 * * 1-5" },
  { label: "Weekly Sun 03:00 UTC", cron: "0 3 * * 0" },
  { label: "Monthly 1st 03:00 UTC", cron: "0 3 1 * *" },
]

function presetForCron(cron: string): string {
  const match = PRESETS.find((p) => p.cron === cron)
  return match ? match.cron : CUSTOM
}

export function CronInput({ value, onChange }: CronInputProps) {
  const [validation, setValidation] = useState<ValidateCronResponse | null>(null)
  const [validating, setValidating] = useState(false)

  // Debounced server-side validation. The endpoint is cheap; the
  // debounce keeps us from hammering it on every keystroke.
  useEffect(() => {
    if (!value) {
      setValidation(null)
      return
    }
    let cancelled = false
    setValidating(true)
    const handle = setTimeout(async () => {
      try {
        const resp = await apiFetch<ValidateCronResponse>("/api/scheduled-actions/validate-cron", { method: "POST", json: { cron: value } })
        if (!cancelled) setValidation(resp)
      } catch {
        if (!cancelled) setValidation({ valid: false, message: "Validation failed", next_run_at: [] })
      } finally {
        if (!cancelled) setValidating(false)
      }
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [value])

  const presetValue = presetForCron(value)

  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-1 gap-[11px] sm:grid-cols-2">
        <Field as="div" label="preset">
          <select
            className="inp"
            aria-label="Cron preset"
            value={presetValue}
            onChange={(e) => {
              if (e.target.value === CUSTOM) return
              onChange(e.target.value)
            }}
          >
            <option value={CUSTOM}>Custom…</option>
            {PRESETS.map((p) => (
              <option key={p.cron} value={p.cron}>{p.label}</option>
            ))}
          </select>
        </Field>
        <Field as="div" label="cron expression" error={validation && !validation.valid ? (validation.message ?? "Invalid cron expression") : undefined}>
          <input type="text" className="inp mono" placeholder="0 3 * * *" aria-label="Cron expression" value={value} onChange={(e) => onChange(e.target.value)} />
        </Field>
      </div>

      {value && <p className="text-[11px] text-text-3">{cronToHuman(value)}</p>}
      {validation?.valid && validation.next_run_at.length > 0 && (
        <div className="text-[11px] text-text-faint">
          <span className="text-text-3">next runs:</span>
          <ul className="mono m-0 mt-0.5 flex list-none flex-col gap-0.5 p-0">
            {validation.next_run_at.slice(0, 3).map((iso) => (
              <li key={iso}>{new Date(iso).toLocaleString()}</li>
            ))}
          </ul>
        </div>
      )}
      {validating && !validation && <p className="text-[11px] text-text-faint">checking…</p>}
    </div>
  )
}
