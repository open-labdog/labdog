"use client"

import { useEffect, useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiFetch } from "@/lib/api"
import { cronToHuman } from "@/lib/cron"
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
  const [validation, setValidation] = useState<ValidateCronResponse | null>(
    null,
  )
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
        const resp = await apiFetch<ValidateCronResponse>(
          "/api/scheduled-actions/validate-cron",
          { method: "POST", json: { cron: value } },
        )
        if (!cancelled) setValidation(resp)
      } catch {
        if (!cancelled) {
          setValidation({
            valid: false,
            message: "Validation failed",
            next_run_at: [],
          })
        }
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
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label className="text-xs uppercase text-slate-500">Preset</Label>
          <select
            value={presetValue}
            onChange={(e) => {
              if (e.target.value === CUSTOM) return
              onChange(e.target.value)
            }}
            className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white"
            aria-label="Cron preset"
          >
            <option value={CUSTOM}>Custom…</option>
            {PRESETS.map((p) => (
              <option key={p.cron} value={p.cron}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs uppercase text-slate-500">
            Cron expression
          </Label>
          <Input
            type="text"
            placeholder="0 3 * * *"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="font-mono"
            aria-label="Cron expression"
          />
        </div>
      </div>

      {value && (
        <p className="text-xs text-slate-400">{cronToHuman(value)}</p>
      )}
      {validation && !validation.valid && (
        <p className="text-xs text-red-400">
          {validation.message ?? "Invalid cron expression"}
        </p>
      )}
      {validation?.valid && validation.next_run_at.length > 0 && (
        <div className="text-xs text-slate-500">
          <span className="text-slate-400">Next runs:</span>
          <ul className="mt-1 space-y-0.5 font-mono">
            {validation.next_run_at.slice(0, 3).map((iso) => (
              <li key={iso}>{new Date(iso).toLocaleString()}</li>
            ))}
          </ul>
        </div>
      )}
      {validating && !validation && (
        <p className="text-xs text-slate-600">Checking…</p>
      )}
    </div>
  )
}
