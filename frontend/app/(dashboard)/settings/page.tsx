"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { SaveIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Breadcrumb } from "@/components/ui/breadcrumb"

interface AppSetting {
  key: string
  value: string
  value_type: string
  description: string
  default: string
  min?: number | null
  max?: number | null
  choices?: string[] | null
  updated_at: string | null
}

// Group settings by category
const CATEGORIES: Record<string, { label: string; keys: string[] }> = {
  drift: {
    label: "Drift Detection",
    keys: ["drift.check_interval_minutes"],
  },
  ssh: {
    label: "SSH",
    keys: ["ssh.connect_timeout", "ssh.idle_timeout_seconds"],
  },
  ansible: {
    label: "Ansible",
    keys: ["ansible.playbook_timeout"],
  },
  discovery: {
    label: "Discovery",
    keys: ["discovery.scan_timeout", "discovery.max_concurrent"],
  },
  logging: {
    label: "Logging",
    keys: ["logging.level", "logging.audit_retention_days"],
  },
  celery: {
    label: "Worker",
    keys: ["celery.concurrency"],
  },
}

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const [editedValues, setEditedValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  const { data: settings, isLoading } = useQuery<AppSetting[]>({
    queryKey: ["settings"],
    queryFn: () => apiFetch<AppSetting[]>("/api/settings"),
  })

  const settingsMap = new Map(settings?.map(s => [s.key, s]) ?? [])

  const handleSave = async (key: string) => {
    const value = editedValues[key]
    if (value === undefined) return

    setSaving(prev => ({ ...prev, [key]: true }))
    setErrors(prev => ({ ...prev, [key]: "" }))

    try {
      await apiFetch(`/api/settings/${key}`, {
        method: "PATCH",
        body: JSON.stringify({ value }),
      })
      await queryClient.invalidateQueries({ queryKey: ["settings"] })
      setEditedValues(prev => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    } catch (e: unknown) {
      const msg = e && typeof e === "object" && "detail" in (e as Record<string, unknown>)
        ? String((e as Record<string, unknown>).detail)
        : "Failed to save"
      setErrors(prev => ({ ...prev, [key]: msg }))
    }
    setSaving(prev => ({ ...prev, [key]: false }))
  }

  const renderInput = (setting: AppSetting) => {
    const currentValue = editedValues[setting.key] ?? setting.value
    const isEdited = setting.key in editedValues && editedValues[setting.key] !== setting.value

    if (setting.choices) {
      return (
        <div className="flex items-center gap-2">
          <select
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white w-48"
            value={currentValue}
            onChange={e => setEditedValues(prev => ({ ...prev, [setting.key]: e.target.value }))}
          >
            {setting.choices.map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          {isEdited && (
            <Button size="sm" disabled={saving[setting.key]} onClick={() => handleSave(setting.key)}>
              <SaveIcon className="w-3.5 h-3.5 mr-1" />
              {saving[setting.key] ? "Saving..." : "Save"}
            </Button>
          )}
        </div>
      )
    }

    return (
      <div className="flex items-center gap-2">
        <Input
          type={setting.value_type === "float" ? "number" : setting.value_type === "int" ? "number" : "text"}
          step={setting.value_type === "float" ? "0.1" : undefined}
          min={setting.min ?? undefined}
          max={setting.max ?? undefined}
          className="w-48 bg-slate-800 border-slate-700"
          value={currentValue}
          onChange={e => setEditedValues(prev => ({ ...prev, [setting.key]: e.target.value }))}
          onKeyDown={e => { if (e.key === "Enter" && isEdited) handleSave(setting.key) }}
        />
        {isEdited && (
          <Button size="sm" disabled={saving[setting.key]} onClick={() => handleSave(setting.key)}>
            <SaveIcon className="w-3.5 h-3.5 mr-1" />
            {saving[setting.key] ? "Saving..." : "Save"}
          </Button>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Settings" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-slate-400 text-sm mt-1">
          Application settings stored in the database. Changes take effect immediately.
        </p>
      </div>

      {isLoading && <p className="text-slate-500">Loading settings...</p>}

      {settings && Object.entries(CATEGORIES).map(([catKey, cat]) => (
        <div key={catKey} className="rounded-lg border border-slate-700 bg-slate-900 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">{cat.label}</h2>
          <div className="space-y-5">
            {cat.keys.map(key => {
              const setting = settingsMap.get(key)
              if (!setting) return null
              return (
                <div key={key} className="flex items-start justify-between gap-8">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white">{setting.description}</p>
                    <p className="text-xs text-slate-500 mt-0.5 font-mono">{setting.key}</p>
                    {setting.min != null && setting.max != null && (
                      <p className="text-xs text-slate-600 mt-0.5">
                        Range: {setting.min} &ndash; {setting.max} (default: {setting.default})
                      </p>
                    )}
                    {errors[key] && (
                      <p className="text-xs text-red-400 mt-1">{errors[key]}</p>
                    )}
                  </div>
                  <div className="flex-shrink-0">
                    {renderInput(setting)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
