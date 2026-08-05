"use client"

import { useQuery } from "@tanstack/react-query"

import { apiFetch } from "@/lib/api"
import type { AIUsageSummary } from "@/lib/types"

function Meter({
  label,
  spend,
  limit,
  warnPct,
}: {
  label: string
  spend: number
  limit: number
  warnPct: number
}) {
  // A zero limit means unlimited, so there is no bar to draw — showing an
  // empty progress bar would imply a cap that does not exist.
  if (!limit) {
    return (
      <div>
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-slate-400">{label}</span>
          <span className="font-mono text-sm text-white">${spend.toFixed(2)}</span>
        </div>
        <p className="mt-1 text-xs text-slate-400">No limit set</p>
      </div>
    )
  }

  const fraction = Math.min(spend / limit, 1)
  const pct = fraction * 100
  const colour =
    fraction >= 1 ? "bg-red-600" : pct >= warnPct ? "bg-amber-600" : "bg-green-600"

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-slate-400">{label}</span>
        <span className="font-mono text-sm text-white">
          ${spend.toFixed(2)}{" "}
          <span className="text-xs text-slate-400">of ${limit.toFixed(2)}</span>
        </span>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full ${colour}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export function UsagePanel() {
  const { data, isLoading } = useQuery<AIUsageSummary>({
    queryKey: ["ai-usage"],
    queryFn: () => apiFetch<AIUsageSummary>("/api/ai/usage?days=30"),
    refetchInterval: 30_000,
  })

  if (isLoading) {
    return <div className="py-8 text-center text-slate-400">Loading usage…</div>
  }
  if (!data) return null

  const maxCost = Math.max(...data.days.map((d) => d.cost_usd), 0.0001)
  const totalTokens = data.days.reduce(
    (sum, d) => sum + d.prompt_tokens + d.completion_tokens,
    0
  )

  return (
    <div className="space-y-4 rounded-lg border border-slate-700 bg-slate-900 p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-white">Usage and budget</h2>
        <span className="text-xs text-slate-400">
          {totalTokens.toLocaleString()} tokens over 30 days
        </span>
      </div>

      {data.exceeded && (
        <div className="rounded-md border border-red-700 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          {data.reason} New sessions are refused until spend falls below the limit.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Meter
          label="Today"
          spend={data.day_spend}
          limit={data.day_limit}
          warnPct={data.warn_pct}
        />
        <Meter
          label="This month"
          spend={data.month_spend}
          limit={data.month_limit}
          warnPct={data.warn_pct}
        />
      </div>

      {data.days.length > 0 ? (
        <div>
          <p className="mb-2 text-xs text-slate-400">Daily spend</p>
          <div className="flex h-24 items-end gap-1">
            {data.days.map((day) => (
              <div
                key={`${day.usage_date}-${day.provider_id}`}
                className="group relative flex-1 rounded-t bg-blue-600/70 hover:bg-blue-500"
                style={{ height: `${Math.max((day.cost_usd / maxCost) * 100, 2)}%` }}
                title={`${day.usage_date}: $${day.cost_usd.toFixed(4)} (${day.turn_count} turns${
                  day.provider_name ? `, ${day.provider_name}` : ""
                })`}
              />
            ))}
          </div>
        </div>
      ) : (
        <p className="text-xs text-slate-400">
          No AI usage recorded yet. Self-hosted models record token counts but
          cost $0, so the chart stays flat unless a paid provider is used.
        </p>
      )}
    </div>
  )
}
