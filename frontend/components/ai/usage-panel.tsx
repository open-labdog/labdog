"use client"

import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Banner, Meter, Panel, Stat, type Tone } from "@/components/ld"
import type { AIUsageSummary } from "@/lib/types"

/**
 * Format an amount in the operator's chosen currency.
 *
 * Intl handles the symbol, its placement, and the separators, which differ
 * by currency and locale — "1 234,56 €" is as correct for a euro user as
 * "$1,234.56" is for a dollar one. Falls back to appending the code if the
 * setting holds something Intl does not recognise, so a typo degrades to a
 * readable number rather than throwing.
 */
export function money(amount: number, currency: string, digits = 2): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(amount)
  } catch {
    return `${amount.toFixed(digits)} ${currency}`
  }
}

function Budget({ label, spend, limit, warnPct, currency }: { label: string; spend: number; limit: number; warnPct: number; currency: string }) {
  // A zero limit means unlimited, so there is no bar to draw — showing an
  // empty progress bar would imply a cap that does not exist.
  if (!limit) return <Stat label={label} value={money(spend, currency)} sub="no limit set" />
  const pct = Math.min(spend / limit, 1) * 100
  const tone: Tone = pct >= 100 ? "danger" : pct >= warnPct ? "warn" : "ok"
  return (
    <div className="rounded-r border border-line bg-surface-2 p-[9px]">
      <Meter label={label} pct={pct} tone={tone} value={`${money(spend, currency)} of ${money(limit, currency)}`} />
    </div>
  )
}

/** Spend against the budgets, and the last 30 days as bars. */
export function UsagePanel() {
  const { data, isLoading } = useQuery<AIUsageSummary>({
    queryKey: ["ai-usage"],
    queryFn: () => apiFetch<AIUsageSummary>("/api/ai/usage?days=30"),
    refetchInterval: 30_000,
  })

  if (isLoading) return <div className="text-xs text-text-3">Loading usage…</div>
  if (!data) return null

  const maxCost = Math.max(...data.days.map((d) => d.cost), 0.0001)
  // Server-provided so every figure on the page agrees; it is a display
  // unit only, never a conversion.
  const currency = data.currency || "USD"
  const totalTokens = data.days.reduce((sum, d) => sum + d.prompt_tokens + d.completion_tokens, 0)

  return (
    <Panel title="usage and budget" meta={`${totalTokens.toLocaleString()} tokens over 30 days`}>
      <div className="flex flex-col gap-3 p-[11px]">
        {data.exceeded && <Banner tone="danger">{data.reason} New sessions are refused until spend falls below the limit.</Banner>}
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          <Budget label="today" spend={data.day_spend} limit={data.day_limit} warnPct={data.warn_pct} currency={currency} />
          <Budget label="this month" spend={data.month_spend} limit={data.month_limit} warnPct={data.warn_pct} currency={currency} />
        </div>
        {data.days.length > 0 ? (
          <div>
            <div className="tt mb-1.5">daily spend</div>
            <div className="flex h-16 items-end gap-px">
              {data.days.map((day) => (
                <div
                  key={`${day.usage_date}-${day.provider_id}`}
                  className="flex-1 rounded-t-[2px]"
                  style={{ height: `${Math.max((day.cost / maxCost) * 100, 3)}%`, background: "var(--accent-line)" }}
                  title={`${day.usage_date}: ${money(day.cost, currency, 4)} (${day.turn_count} turns${day.provider_name ? `, ${day.provider_name}` : ""})`}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="text-[11.5px] text-text-3">No AI usage recorded yet. Self-hosted models record token counts but cost nothing, so the chart stays flat unless a paid provider is used.</div>
        )}
      </div>
    </Panel>
  )
}
