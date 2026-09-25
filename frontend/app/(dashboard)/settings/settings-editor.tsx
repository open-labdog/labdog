"use client"

import { useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Help, Panel, Tag } from "@/components/ld"

interface AppSetting {
  key: string
  value: string
  value_type: string
  description: string
  /**
   * Optional long-form detail — caveats, reasoning, "only useful when"
   * conditions. Rendered behind an info button rather than inline, so the
   * card stays scannable. Absent on settings whose description already says
   * everything there is to say.
   */
  help?: string | null
  default: string
  min?: number | null
  max?: number | null
  choices?: string[] | null
  /** Character cap for `text` settings. Absent on every other type. */
  max_length?: number | null
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
 *
 * The fallback is a safety net, not a substitute for listing keys here. When
 * the approvals, snapshot and alert-intake settings shipped without being
 * added, all seven landed in the fallback — so the page grew a second card
 * headed "Ai", below the curated "AI" one, reading as a duplicate panel
 * rather than as the omission it was. Add new keys to the owning category;
 * a prefix appearing twice in the rendered page is the symptom that someone
 * did not.
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
      // Spend limits.
      "ai.budget_daily",
      "ai.budget_monthly",
      "ai.budget_warn_pct",
      // Per-session caps.
      "ai.max_iterations",
      "ai.max_commands",
      "ai.max_tokens_total",
      "ai.wall_clock_seconds",
      // Changes and approvals.
      "ai.snapshot_before_mutating",
      "ai.snapshot_retention_days",
      "ai.approval_expiry_hours",
      // Alert intake.
      "ai.alert_intake_enabled",
      "ai.alertmanager_poll_minutes",
      "ai.auto_investigate_enabled",
      "ai.auto_investigate_min_severity",
      "ai.alert_mission_template",
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
    keys: ["logging.level", "logging.audit_retention_days", "logging.run_retention_days"],
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

/**
 * A prompt is not a value — it is a paragraph, and it does not belong in
 * the 12rem control every other row uses. Multiline settings take the full
 * width of the card and put the editor below the label rather than beside
 * it.
 */
function isMultiline(s: AppSetting): boolean {
  return s.value_type === "text"
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

/**
 * One category — a kit `Panel`. Plain unless the category declares
 * `collapsible`.
 *
 * When it does, the header and the pinned row render above the fold and
 * are never hidden; only `children` goes behind the disclosure. That split
 * is the whole point — see the note on `Category.collapsible`. The
 * disclosure is a native `<details>` whose `open` we control, so it needs
 * no library and reads as one to assistive tech.
 */
function CategoryCard({
  label,
  count,
  hasPendingEdit,
  uncategorised,
  pinned,
  open,
  onOpenChange,
  children,
}: {
  label: string
  count: number
  hasPendingEdit: boolean
  /**
   * Marks the card as the uncategorised fallback rather than a curated
   * category. Worth saying out loud in the UI: the heading is a bare key
   * prefix, so a fallback card for `ai.*` renders as "Ai" directly beneath
   * the curated "AI" — indistinguishable from a duplicate panel unless it
   * admits what it is. Naming it turns a puzzling second card into a
   * legible "somebody forgot to categorise these".
   */
  uncategorised?: boolean
  pinned?: ReactNode
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  const title = (
    <span className="flex items-center gap-2">
      {label}
      {uncategorised && (
        <Tag tone="hold" title="no curated category claims these keys — add them to the owning one">
          uncategorised
        </Tag>
      )}
    </span>
  )
  const meta = `${count} setting${count === 1 ? "" : "s"}${hasPendingEdit ? " · unsaved" : ""}`

  if (!pinned) {
    return (
      <Panel title={title} meta={meta}>
        {children}
      </Panel>
    )
  }

  return (
    <Panel title={title} meta={meta}>
      {/* Never inside the disclosure: this is the row an error message
          sends operators here to change, and the card is collapsed by
          default precisely when that setting is off. */}
      {pinned}
      <details open={open} onToggle={(e) => onOpenChange((e.target as HTMLDetailsElement).open)}>
        <summary className="tt cursor-pointer select-none list-none px-[11px] py-2 text-text-3 hover:text-text-2 [&::-webkit-details-marker]:hidden">
          {open ? "▾ hide" : `▸ show ${count - 1} more`}
        </summary>
        {children}
      </details>
    </Panel>
  )
}

/**
 * The database-backed settings editor, one section at a time. The
 * Settings screen composes it: `categories` picks which curated
 * categories to render (by key), `includeUncategorised` adds everything
 * the API returned that no category claims — that safety net belongs to
 * exactly one section, or an unlisted key would render twice.
 */
export function SettingsEditor({
  categories,
  includeUncategorised = false,
}: {
  categories: string[]
  includeUncategorised?: boolean
}) {
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

  const setValue = (key: string, value: string) => setEditedValues((prev) => ({ ...prev, [key]: value }))
  const discard = (key: string) =>
    setEditedValues((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })

  const renderInput = (setting: AppSetting) => {
    const currentValue = editedValues[setting.key] ?? setting.value
    const isEdited = setting.key in editedValues && editedValues[setting.key] !== setting.value
    const busy = !!saving[setting.key]
    const saveButton = isEdited && (
      <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => handleSave(setting.key)}>
        {busy ? "Saving…" : "Save"}
      </button>
    )

    if (isMultiline(setting)) {
      const overLimit = setting.max_length != null && currentValue.length > setting.max_length
      const isDefault = currentValue === setting.default
      return (
        <div className="flex w-full flex-col gap-2">
          <textarea
            rows={12}
            className="inp mono text-[11.5px]"
            spellCheck={false}
            value={currentValue}
            onChange={(e) => setValue(setting.key, e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-1.5">
            {isEdited && (
              <button type="button" className="btn btn-sm btn-primary" disabled={busy || overLimit} onClick={() => handleSave(setting.key)}>
                {busy ? "Saving…" : "Save"}
              </button>
            )}
            {/*
              Restoring the shipped wording is otherwise unreachable: the
              default is a paragraph nobody can retype from memory, so an
              operator who edits one badly has no way back short of the
              database.
            */}
            {!isDefault && (
              <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setValue(setting.key, setting.default)}>
                Reset to default
              </button>
            )}
            {isEdited && (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => discard(setting.key)}>
                Discard
              </button>
            )}
            {setting.max_length != null && (
              <span className={`mono num ml-auto text-[11px] ${overLimit ? "text-danger" : "text-text-3"}`}>
                {currentValue.length} / {setting.max_length}
              </span>
            )}
          </div>
        </div>
      )
    }

    if (setting.choices || isToggle(setting)) {
      const options = setting.choices
        ? setting.choices.map((c) => ({ value: c, label: c }))
        : [
            { value: "0", label: "Off" },
            { value: "1", label: "On" },
          ]
      return (
        <div className="flex items-center gap-1.5">
          <select className="inp mono" style={{ width: 140 }} value={currentValue} onChange={(e) => setValue(setting.key, e.target.value)}>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {saveButton}
        </div>
      )
    }

    return (
      <div className="flex items-center gap-1.5">
        <input
          type={setting.value_type === "float" || setting.value_type === "int" ? "number" : "text"}
          step={setting.value_type === "float" ? "0.1" : undefined}
          min={setting.min ?? undefined}
          max={setting.max ?? undefined}
          className="inp mono num"
          style={{ width: 140 }}
          value={currentValue}
          onChange={(e) => setValue(setting.key, e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && isEdited) handleSave(setting.key)
          }}
        />
        {saveButton}
      </div>
    )
  }

  const renderRow = (key: string) => {
    const setting = settingsMap.get(key)
    if (!setting) return null
    const stacked = isMultiline(setting)
    return (
      <div key={key} className={`flex gap-3 border-b border-line-faint px-[11px] py-[9px] last:border-b-0 ${stacked ? "flex-col" : "items-center"}`}>
        <div className="min-w-0 flex-1">
          <div className="text-xs text-text">{setting.description}</div>
          {/* Both lines are operative text an operator reads and quotes:
              the key is what goes in a config file or a bug report. */}
          <div className="mono trunc text-[11px] text-text-3">
            {setting.key}
            {setting.min != null && setting.max != null && !isToggle(setting) && (
              <span className="text-text-faint">
                {" "}
                · {setting.min} – {setting.max} · default {setting.default}
              </span>
            )}
          </div>
          {setting.help && (
            // Summarised with "why" rather than the key: the key is on the
            // line above, and the paragraph answers the question the row
            // leaves open.
            <Help>{setting.help}</Help>
          )}
          {errors[key] && (
            <div className="mt-1 text-[11px] text-danger" role="alert">
              {errors[key]}
            </div>
          )}
        </div>
        <div className={stacked ? "" : "shrink-0"}>{renderInput(setting)}</div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {isLoading && <p className="m-0 text-xs text-text-3">Loading settings…</p>}

      {settings &&
        Object.entries(CATEGORIES)
          .filter(([catKey]) => categories.includes(catKey))
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
        includeUncategorised &&
        Object.entries(extraGroups).map(([prefix, keys]) => (
          <CategoryCard
            key={prefix}
            label={prefixLabel(prefix)}
            count={keys.length}
            hasPendingEdit={keys.some(k => k in editedValues)}
            uncategorised
            open={isOpen(prefix)}
            onOpenChange={open => setOpen(prefix, open)}
          >
            {keys.map(renderRow)}
          </CategoryCard>
        ))}
    </div>
  )
}
