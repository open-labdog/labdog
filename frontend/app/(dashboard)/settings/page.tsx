"use client"

import { useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDownIcon, SaveIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
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

/**
 * Curated grouping: gives a category a real label and a deliberate field
 * order. It is *not* an allow-list — anything the API returns that no
 * category claims is rendered below under its key prefix.
 *
 * That fallback matters. This map used to be the only way a setting could
 * appear, so adding one to the backend left it unreachable until somebody
 * remembered to list it here as well. Twelve of twenty settings had drifted
 * out of the UI that way, including `ai.enabled` — which the AI subsystem
 * tells operators to go and enable, in a page that never showed it.
 */
interface Category {
  label: string
  keys: string[]
  /**
   * Present only on categories long enough to earn a disclosure. `pinnedKey`
   * renders above the fold and never collapses; the rest go behind the
   * chevron.
   *
   * Pinning is the point, not a nicety. `ai.enabled` is what the AI
   * subsystem's own error message tells operators to come here and change,
   * and AI ships off, so a card that collapsed by default would hide the
   * switch at exactly the moment someone was sent to find it. Making the
   * row structurally exempt is a stronger guarantee than choosing a good
   * default — the same reasoning as the uncategorised fallback below.
   */
  collapsible?: { pinnedKey: string }
}

const CATEGORIES: Record<string, Category> = {
  ai: {
    label: "AI",
    collapsible: { pinnedKey: "ai.enabled" },
    keys: [
      "ai.enabled",
      "ai.allow_cloud_providers",
      "ai.currency",
      "ai.budget_daily",
      "ai.budget_monthly",
      "ai.budget_warn_pct",
      "ai.max_iterations",
      "ai.max_commands",
      "ai.max_tokens_total",
      "ai.wall_clock_seconds",
    ],
  },
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
  actions: {
    label: "Actions",
    keys: ["actions.preflight_enabled"],
  },
  workflow: {
    label: "Workflows",
    keys: ["workflow.snapshot_max_age_hours"],
  },
  discovery: {
    label: "Discovery",
    keys: ["discovery.scan_timeout", "discovery.max_concurrent"],
  },
  logging: {
    label: "Logging",
    keys: ["logging.level", "logging.audit_retention_days"],
  },
}

const CATEGORISED_KEYS = new Set(
  Object.values(CATEGORIES).flatMap((c) => c.keys)
)

/** Title-case a bare key prefix for an uncurated group heading. */
function prefixLabel(prefix: string): string {
  return prefix.charAt(0).toUpperCase() + prefix.slice(1).replace(/_/g, " ")
}

/**
 * An int constrained to 0..1 is a boolean wearing a number's clothes.
 * Rendering it as a spinner asks the operator to type `1` to turn a master
 * switch on, and leaves them guessing which of 0 and 1 means on.
 */
function isToggle(s: AppSetting): boolean {
  return s.value_type === "int" && s.min === 0 && s.max === 1
}

const COLLAPSE_STORAGE_KEY = "labdog:settings-collapse"

/**
 * Manual expand/collapse choices, keyed by category. A key's *absence*
 * means "no manual choice yet", which is what lets the derived default
 * still apply — so this cannot be a plain boolean map with a default.
 *
 * Mirrors the localStorage idiom in `hooks/use-table-state.ts`: guarded,
 * try/caught for private mode and quota, and validated on read so a
 * hand-edited value cannot put non-booleans into the map.
 */
function loadCollapseOverrides(): Record<string, boolean> {
  try {
    const raw =
      typeof window !== "undefined" ? localStorage.getItem(COLLAPSE_STORAGE_KEY) : null
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== "object" || parsed === null) return {}
    const out: Record<string, boolean> = {}
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === "boolean") out[k] = v
    }
    return out
  } catch {
    return {}
  }
}

function saveCollapseOverrides(overrides: Record<string, boolean>) {
  try {
    if (typeof window === "undefined") return
    localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(overrides))
  } catch {
    /* private mode or quota — a lost display preference is not worth raising */
  }
}

/**
 * Whether a collapsible category starts open, absent a manual choice.
 *
 * Read off the *saved* value, never an in-progress edit: the AI card's
 * pinned row is a toggle, and reacting to the edit would collapse the body
 * the moment someone selected "Off", before they had saved it.
 *
 * Unknown reads as open. An absent setting is never a reason to hide the
 * rest of a category — the same instinct as the uncategorised fallback.
 */
function derivedOpen(categoryKey: string, settingsMap: Map<string, AppSetting>): boolean {
  if (categoryKey !== "ai") return true
  return settingsMap.get("ai.enabled")?.value !== "0"
}

const CARD = "rounded-lg border border-slate-700 bg-slate-900 p-5"

/**
 * One category. Plain card unless the category declares `collapsible`.
 *
 * When it does, the header and the pinned row render above the fold and
 * are never hidden; only `children` goes behind the disclosure. That split
 * is the whole point — see the note on `Category.collapsible`.
 *
 * The `<h2>` wraps the trigger rather than being replaced by it, so heading
 * navigation still lands on "AI" exactly as before; it is merely
 * activatable now. `CollapsibleTrigger` supplies aria-expanded and
 * aria-controls itself.
 */
function CategoryCard({
  label,
  count,
  hasPendingEdit,
  pinned,
  open,
  onOpenChange,
  children,
}: {
  label: string
  count: number
  hasPendingEdit: boolean
  pinned?: ReactNode
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  const heading = (
    <span className="flex items-center gap-2">
      {label}
      <span className="text-sm font-normal text-slate-400">({count})</span>
      {hasPendingEdit && (
        <span
          className="h-1.5 w-1.5 rounded-full bg-amber-500"
          title="Unsaved changes"
        />
      )}
    </span>
  )

  if (!pinned) {
    return (
      <div className={CARD}>
        <h2 className="mb-4 text-lg font-semibold text-white">{heading}</h2>
        <div className="space-y-5">{children}</div>
      </div>
    )
  }

  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className={CARD}>
      <h2 className="text-lg font-semibold text-white">
        <CollapsibleTrigger className="hover:text-slate-200">
          {heading}
          <span className="flex items-center gap-1.5 text-xs font-normal text-slate-400">
            {open ? "Hide" : `Show ${count - 1} more`}
            <ChevronDownIcon className="h-4 w-4 shrink-0 transition-transform data-panel-open:rotate-180" />
          </span>
        </CollapsibleTrigger>
      </h2>

      {/* Never inside the panel: this is the row an error message sends
          operators here to change, and the card is collapsed by default
          precisely when that setting is off. */}
      <div className="mt-4">{pinned}</div>

      <CollapsiblePanel>
        <div className="mt-5 space-y-5 border-t border-slate-800 pt-5">{children}</div>
      </CollapsiblePanel>
    </Collapsible>
  )
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

  // Precedence: an explicit click wins from then on; until one happens the
  // derived default applies. Seeded lazily so the value is present on the
  // very first render — an effect would apply it a tick after paint, which
  // is exactly how a card flashes open before snapping shut.
  const [collapseOverrides, setCollapseOverrides] = useState<Record<string, boolean>>(
    loadCollapseOverrides
  )

  const isOpen = (key: string): boolean =>
    key in collapseOverrides ? collapseOverrides[key] : derivedOpen(key, settingsMap)

  const setOpen = (key: string, open: boolean) => {
    setCollapseOverrides(prev => {
      const next = { ...prev, [key]: open }
      saveCollapseOverrides(next)
      return next
    })
  }

  // Everything the API returned that no category claims, grouped by key
  // prefix so it still arrives under a heading rather than in a heap.
  const uncategorised = (settings ?? []).filter(s => !CATEGORISED_KEYS.has(s.key))
  const extraGroups = uncategorised.reduce<Record<string, string[]>>((acc, s) => {
    const prefix = s.key.split(".")[0]
    ;(acc[prefix] ??= []).push(s.key)
    return acc
  }, {})

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

    if (setting.choices || isToggle(setting)) {
      const options = setting.choices
        ? setting.choices.map(c => ({ value: c, label: c }))
        : [
            { value: "0", label: "Off" },
            { value: "1", label: "On" },
          ]
      return (
        <div className="flex items-center gap-2">
          <select
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white w-48"
            value={currentValue}
            onChange={e => setEditedValues(prev => ({ ...prev, [setting.key]: e.target.value }))}
          >
            {options.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
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

  const renderRow = (key: string) => {
    const setting = settingsMap.get(key)
    if (!setting) return null
    return (
      <div key={key} className="flex items-start justify-between gap-8">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white">{setting.description}</p>
          {/* FRONTEND.md reserves text-slate-500 for decorative elements;
              both of these are operative text an operator reads and quotes. */}
          <p className="text-xs text-slate-400 mt-0.5 font-mono">{setting.key}</p>
          {setting.min != null && setting.max != null && !isToggle(setting) && (
            <p className="text-xs text-slate-400 mt-0.5">
              Range: {setting.min} &ndash; {setting.max} (default: {setting.default})
            </p>
          )}
          {errors[key] && (
            <p className="text-xs text-red-400 mt-1">{errors[key]}</p>
          )}
        </div>
        <div className="flex-shrink-0">{renderInput(setting)}</div>
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

      {settings &&
        Object.entries(CATEGORIES)
          // A category whose keys the backend does not define would
          // otherwise render as an empty titled card.
          .filter(([, cat]) => cat.keys.some(k => settingsMap.has(k)))
          .map(([catKey, cat]) => {
            const present = cat.keys.filter(k => settingsMap.has(k))
            const pinnedKey = cat.collapsible?.pinnedKey
            // Only collapse when the pinned row is actually there. Without
            // this, a backend that stopped defining ai.enabled would hide
            // the rest of the category behind a chevron with nothing above
            // it to explain why.
            const collapsing = pinnedKey != null && settingsMap.has(pinnedKey)
            return (
              <CategoryCard
                key={catKey}
                label={cat.label}
                count={present.length}
                hasPendingEdit={cat.keys.some(k => k in editedValues)}
                pinned={collapsing ? renderRow(pinnedKey) : undefined}
                open={isOpen(catKey)}
                onOpenChange={open => setOpen(catKey, open)}
              >
                {(collapsing ? cat.keys.filter(k => k !== pinnedKey) : cat.keys).map(
                  renderRow
                )}
              </CategoryCard>
            )
          })}

      {settings &&
        Object.entries(extraGroups).map(([prefix, keys]) => (
          <CategoryCard
            key={prefix}
            label={prefixLabel(prefix)}
            count={keys.length}
            hasPendingEdit={keys.some(k => k in editedValues)}
            open={isOpen(prefix)}
            onOpenChange={open => setOpen(prefix, open)}
          >
            {keys.map(renderRow)}
          </CategoryCard>
        ))}
    </div>
  )
}
