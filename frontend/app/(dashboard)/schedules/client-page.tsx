"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CalendarClock, ShieldAlert } from "lucide-react"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { ScheduledActionsList } from "@/components/scheduled-actions/scheduled-actions-list"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useDelayedLoading } from "@/lib/utils"
import type { ScheduledAction } from "@/lib/types"

type CategoryFilter = "all" | "_builtin" | "pack"
type TargetFilter = "all" | "host" | "group" | "fleet"

export default function SchedulesPage() {
  const { user } = useAuth()
  const [createOpen, setCreateOpen] = useState(false)
  const [category, setCategory] = useState<CategoryFilter>("all")
  const [targetFilter, setTargetFilter] = useState<TargetFilter>("all")
  const [enabledOnly, setEnabledOnly] = useState(false)
  const [search, setSearch] = useState("")

  const { data: rows, isLoading, error } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions"],
    queryFn: () =>
      apiFetch<ScheduledAction[]>(
        "/api/scheduled-actions?include_last_run=true",
      ),
    refetchInterval: (query) => {
      const data = query.state.data
      if (
        data?.some(
          (r) =>
            r.last_run?.status === "running" || r.last_run?.status === "queued",
        )
      ) {
        return 3000
      }
      return false
    },
  })
  const showLoading = useDelayedLoading(isLoading)

  const filtered = useMemo(() => {
    if (!rows) return []
    return rows.filter((r) => {
      if (category === "_builtin" && !r.action_key.startsWith("_builtin.")) {
        return false
      }
      if (category === "pack" && r.action_key.startsWith("_builtin.")) {
        return false
      }
      if (targetFilter !== "all" && r.target_kind !== targetFilter) {
        return false
      }
      if (enabledOnly && !r.enabled) return false
      if (search) {
        const q = search.toLowerCase()
        const haystack = [
          r.action_name,
          r.action_key,
          r.target_name,
          r.pack_name,
        ]
          .filter((x): x is string => Boolean(x))
          .join(" ")
          .toLowerCase()
        if (!haystack.includes(q)) return false
      }
      return true
    })
  }, [rows, category, targetFilter, enabledOnly, search])

  // Permission gate — the API requires superuser; show an inline
  // explanation rather than letting non-superusers hit a 403 toast.
  if (user && !user.is_superuser) {
    return (
      <div className="space-y-6">
        <Breadcrumb items={[{ label: "Schedules" }]} />
        <div className="flex flex-col items-center gap-3 rounded-lg border border-slate-700 bg-slate-900 p-10 text-center">
          <ShieldAlert className="h-10 w-10 text-slate-700" />
          <p className="text-slate-300 font-medium">
            Scheduled actions require superuser privileges
          </p>
          <p className="text-sm text-slate-500 max-w-md">
            Ask an administrator to schedule actions on your behalf, or to
            promote your account if you need ongoing access.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Schedules" }]} />

      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Schedules</h1>
          <p className="text-slate-400 text-sm mt-1">
            Cron-driven runs of any registered action across hosts, groups, or
            the entire fleet.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>+ New</Button>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900 p-3 text-sm">
        <SegmentedControl
          label="Category"
          value={category}
          onChange={(v) => setCategory(v as CategoryFilter)}
          options={[
            { value: "all", label: "All" },
            { value: "_builtin", label: "Built-in" },
            { value: "pack", label: "Pack action" },
          ]}
        />
        <SegmentedControl
          label="Target"
          value={targetFilter}
          onChange={(v) => setTargetFilter(v as TargetFilter)}
          options={[
            { value: "all", label: "All" },
            { value: "host", label: "Host" },
            { value: "group", label: "Group" },
            { value: "fleet", label: "Fleet" },
          ]}
        />
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="checkbox"
            checked={enabledOnly}
            onChange={(e) => setEnabledOnly(e.target.checked)}
            className="h-4 w-4 rounded border-slate-600"
          />
          <span className="text-slate-300">Enabled only</span>
        </label>
        <input
          type="text"
          placeholder="Search action / target…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="ml-auto w-64 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-200"
        />
      </div>

      {showLoading && <TableSkeleton rows={3} columns={6} />}
      {error && (
        <div className="text-red-400 py-8 text-center">
          Failed to load scheduled actions
        </div>
      )}

      {!isLoading && !error && (
        <ScheduledActionsList
          rows={filtered}
          emptyState={
            <div className="flex flex-col items-center gap-3 rounded-lg border border-slate-700 bg-slate-900 p-10 text-center">
              <CalendarClock className="h-10 w-10 text-slate-700" />
              <div>
                <p className="text-slate-300 font-medium">No schedules yet</p>
                <p className="text-sm text-slate-500 mt-1 max-w-md">
                  Create one here, or hit <strong>Schedule…</strong> on any
                  action card from a host or group page.
                </p>
              </div>
              <Button onClick={() => setCreateOpen(true)} className="mt-2">
                Create a schedule
              </Button>
            </div>
          }
        />
      )}

      {createOpen && (
        <ScheduleActionDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
        />
      )}
    </div>
  )
}

function SegmentedControl<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs uppercase text-slate-500">{label}</span>
      <div className="flex overflow-hidden rounded border border-slate-700">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={`px-2 py-1 text-xs ${
              value === o.value
                ? "bg-blue-600 text-white"
                : "bg-slate-900 text-slate-400 hover:bg-slate-800"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}
