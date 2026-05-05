"use client"

import { useEffect, useState } from "react"
import { Input } from "@/components/ui/input"
import { apiFetch } from "@/lib/api"
import { cronToHuman } from "@/lib/cron"
import type { ValidateCronResponse } from "@/lib/types"

interface CronInputProps {
  value: string
  onChange: (next: string) => void
}

const QUICK_PICKS: { label: string; cron: string }[] = [
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Nightly 03:00 UTC", cron: "0 3 * * *" },
  { label: "Weekly Sun 03:00", cron: "0 3 * * 0" },
  { label: "Monthly 1st 03:00", cron: "0 3 1 * *" },
]

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
        const resp = await apiFetch<ValidateCronResponse>(
          "/api/scheduled-actions/validate-cron",
          { method: "POST", json: { cron: value } },
        )
        if (!cancelled) setValidation(resp)
      } catch {
        if (!cancelled) {
          setValidation({ valid: false, message: "Validation failed", next_run_at: [] })
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

  return (
    <div className="space-y-2">
      <Input
        type="text"
        placeholder="0 3 * * *"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="font-mono"
        aria-label="Cron expression"
      />
      <div className="flex flex-wrap gap-1">
        {QUICK_PICKS.map((q) => (
          <button
            key={q.cron}
            type="button"
            onClick={() => onChange(q.cron)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            {q.label}
          </button>
        ))}
      </div>
      {value && (
        <p className="text-xs text-slate-400">
          {cronToHuman(value)}
        </p>
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
