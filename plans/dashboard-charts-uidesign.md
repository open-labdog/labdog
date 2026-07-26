# Dashboard Charts + Activity Feed — UI Design Spec

Branch-scoped design doc (delete before PR, per `CONTRIBUTING.md` / plans/ workflow). Companion to `plans/dashboard-charts.md` (backend/architecture plan) — this file is the visual/dataviz spec for the frontend engineer. It assumes the API contract already scoped there:

- `GET /api/dashboard/sync-success-rate?days=7&granularity=day&module=` → array of `{ bucket: string (ISO date or ISO hour), synced: number, total: number, rate: number (0-100) }`
- `GET /api/dashboard/drift-trend?days=7&granularity=day&module=` → array of `{ bucket: string, drifted_checks: number, total_drift: number }` (`total_drift` = sum of `add_count + remove_count + policy_change_count` across `drift_samples` in the bucket)
- `GET /api/audit-log?limit=10` (existing endpoint, no changes)

If field names land differently, keep the visual spec — only the data-mapping layer changes.

---

## 0. Where this slots in

In `app/(dashboard)/dashboard/page.tsx`, insert a new row **between** the two `StatCard` grids and the `{/* Host triage table */}` block. It is a sibling `<div>` inside the existing `<div className="space-y-6">` — no extra top/bottom margin needed, the page's `space-y-6` rhythm handles it.

```
Breadcrumb
Header (title + Collect State button)
StatCard grid (5-col: Total / In Sync / Drifted / Errors / Unknown-Pending)
StatCard grid (3-col: Last Check / Never Checked / Never Synced)
──────────────── NEW: charts + feed row ────────────────
Host triage table (TableSkeleton / DataTable)
```

Visual relationship to neighbors:
- StatCards above use `bg-slate-800/40` (lighter, "tile" surface, no shadow, dense numeric readouts). The chart/feed cards use `bg-slate-900` (same surface as the DataTable wrapper below) with `border-slate-700` — this makes the new row read as "content panels" (heavier, analytical) sitting between two lighter/denser data surfaces, which is the correct visual hierarchy: glanceable numbers → trend context → row-level triage detail.
- Same border treatment (`border-slate-700`, `rounded-lg`) as the DataTable wrapper directly below, so the two sections feel like one visual family separated only by the `space-y-6` gap.

---

## 1. Sync success-rate over time chart

**Chart type: Area chart (not line).** Rationale: this is a single bounded series (0–100%), and an area fill communicates "how full is the success bar" more immediately than a bare line, which reads better for multi-series comparison. A gradient-fill area is the standard recharts/shadcn pattern for a single-series bounded percentage metric and keeps visual weight low against the dark card.

**Card**
- Wrapper: `rounded-lg border border-slate-700 bg-slate-900 p-4`
- Header row: `flex items-center justify-between mb-3`
  - Title: `text-sm font-semibold text-white` — "Sync Success Rate"
  - Subtitle (optional, same line via a `<span>` or on its own line below): `text-xs text-slate-400` — "Last 7 days"
- Chart canvas: `<div className="h-[240px] w-full">` wrapping `<ResponsiveContainer width="100%" height="100%">`

**Axes**
- X axis: `dataKey="bucket"`, `tickLine={false}`, `axisLine={{ stroke: "#334155" }}` (slate-700), `tick={{ fill: "#94a3b8", fontSize: 11 }}` (slate-400). Format tick labels client-side: `granularity="day"` → `"Jul 22"` (short month + day); `granularity="hour"` → `"14:00"`. Show at most ~7 ticks (recharts `interval="preserveStartEnd"` or manual `interval` when `days` > 7 to avoid label crowding).
- Y axis: `domain={[0, 100]}`, ticks restricted to `[0, 50, 100]` only (not 0/25/50/75/100 — fewer gridlines reduces chart-junk for a dashboard-glance widget), `tickFormatter={(v) => `${v}%`}`, same tick style as X axis, `axisLine={false}`, `tickLine={false}`.
- Gridlines: `<CartesianGrid vertical={false} stroke="#1e293b" strokeDasharray="3 3" />` (slate-800) — horizontal-only gridlines; vertical gridlines add no information on a single-series time chart and clutter the dark background.

**Color encoding (green — success)**
- Area `stroke="#22c55e"` (green-500), `strokeWidth={2}`.
- Fill: linear gradient, id `syncRateFill`, stop 0% `#22c55e` at `stopOpacity=0.35`, stop 100% `#22c55e` at `stopOpacity=0.02`.
- Active dot on hover: `r={4}`, `fill="#4ade80"` (green-400), `stroke="#0f172a"` (slate-900, matches card bg so the dot appears to "pop" off the line), `strokeWidth={2}`.
- No default dots on the line itself (`dot={false}`) — only the active/hover dot renders, keeping the resting state clean.

**Legend:** none. Single series with a labeled card title already identifies what's plotted; a legend would be redundant chrome.

**Tooltip**
Custom tooltip content (dark card style, matching the rest of the app's floating surfaces):
- Container: `rounded-md border border-slate-700 bg-slate-900 px-3 py-2 shadow-lg text-xs`
- Line 1 (date): `text-slate-300 font-medium` — full formatted date, e.g. "Wed, Jul 22" (day granularity) or "Jul 22, 14:00" (hour granularity) — more precise than the axis tick.
- Line 2 (raw counts): `text-slate-400` — `"38 / 42 hosts synced"` (i.e. `${synced} / ${total}`)
- Line 3 (rate, emphasized): `text-green-400 font-semibold text-sm` — `"90% success rate"`

**Sparse-data behavior**
- **Zero buckets returned** (empty array): don't render an empty axis. Show the same muted empty-state treatment as the drift chart (see §2) with copy: "No sync activity recorded yet" / "Success rate appears once hosts start syncing."
- **Exactly 1 bucket**: an Area/Line with a single point renders nothing visible in recharts (no line has two ends to connect). Detect this case (`data.length === 1`) and render the single point as a standalone dot (`<Dot cx={...} cy={...} r={4} fill="#22c55e" />` positioned at the horizontal center, or simplest: fall back to rendering a `BarChart` with one bar for that one bucket) **plus** a caption below the chart: `text-xs text-slate-500 text-center mt-1` — "Only one data point so far — the trend line appears with more history."
- **2+ buckets with gaps** (e.g. a day with zero sync activity, `total: 0`): recharts will naturally dip the line to `rate=0` or leave a gap depending on `connectNulls`. Prefer explicit `rate: null` for buckets with `total === 0` (no syncs attempted that day, not "0% success") and set `connectNulls={false}` so the line visibly breaks rather than falsely reading as "everything failed" — that distinction (no data vs. bad data) matters a lot for an ops dashboard.

**Height:** chart canvas `h-[240px]`; full card (with header) ≈ 300px.

---

## 2. Drift trend over the past week chart

**Chart type: Bar chart (not area).** Rationale: drift trend is fundamentally an event-count/magnitude-per-bucket metric, not a continuous flowing quantity — bars communicate discrete daily totals better than a filled area, and they read correctly even with sparse/zero days (a zero-height bar is unambiguous; a zero-value area chart can look like a rendering bug).

**Card:** same wrapper/header pattern as §1 — `rounded-lg border border-slate-700 bg-slate-900 p-4`, title "Drift Trend", subtitle "Last 7 days".

**Primary series: `drifted_checks` (count).** This is the bar height — "how many drift checks this day found the host out of sync." Recommended over `total_drift` as the bar-height metric because a count is directly comparable across days regardless of how "big" any one drift event was, and it matches the semantic vocabulary used everywhere else in the app (drifted **hosts**, not a magnitude score).

**Secondary series: `total_drift` (magnitude) — tooltip-only, not a second plotted series.** Two amber series plotted together (bars + line, dual Y-axis) is a classic dataviz anti-pattern here: `drifted_checks` (small integer counts, e.g. 0–12) and `total_drift` (unbounded sum of add/remove/policy-change counts, could be in the hundreds) live on wildly different scales, so a shared or secondary axis either flattens one series or requires an awkward dual-axis that's easy to misread. Instead, surface `total_drift` — and its breakdown — richly in the tooltip, where scale doesn't matter. This keeps the chart itself a single, honest, easy-to-scan bar series.

*(Optional v2 enhancement, not required for this pass: a thin `total_drift` line on a secondary right-hand axis, styled `stroke="#fbbf24"` amber-400, `strokeDasharray="4 2"`, axis hidden (`YAxis yAxisId="right" hide`) — only worth adding once real data shows the two series are visually complementary rather than noisy.)*

**Axes**
- X axis: `dataKey="bucket"`, same day-label formatting as §1 (`"Jul 22"`), `tickLine={false}`, `axisLine={{ stroke: "#334155" }}`, tick style `{ fill: "#94a3b8", fontSize: 11 }`.
- Y axis: integer-only ticks (`allowDecimals={false}`), label optional ("Drifted Checks"), same slate-400/slate-700 styling as §1. No fixed domain — let recharts auto-scale to the max `drifted_checks` in range (bars should never look pinned to an artificial ceiling).
- Gridlines: `<CartesianGrid vertical={false} stroke="#1e293b" strokeDasharray="3 3" />` — same horizontal-only treatment as §1 for visual consistency between the two charts.

**Color encoding (amber — drift/warning)**
- Bar `fill="#f59e0b"` (amber-500), `radius={[3, 3, 0, 0]}` (rounded top corners only, consistent with the general rounded-corner language of cards/badges in the app).
- Hover state: `fill="#fbbf24"` (amber-400) via `activeBar` / cell-level hover styling — slightly lighter to confirm interactivity without a full recolor.
- Special case: a bucket with `drifted_checks === 0` still renders as a real (zero-height) bar in the same amber-family axis — do **not** recolor it green. Zero drift is good news, but recoloring individual bars per-value breaks the single-hue "this chart is about drift" identity and makes the chart harder to scan at a glance. Instead, if **every** bucket in range has `drifted_checks === 0`, add a small inline confirmation directly under the chart title (not per-bar): `text-xs text-green-400` — "No drift detected in the last 7 days." This is a distinct, positive-signal callout, separate from the true empty-state below.

**Tooltip**
- Container: same dark tooltip shell as §1 (`rounded-md border border-slate-700 bg-slate-900 px-3 py-2 shadow-lg text-xs`)
- Line 1 (date): `text-slate-300 font-medium` — e.g. "Wed, Jul 22"
- Line 2 (primary metric): `text-amber-400 font-semibold text-sm` — `"{drifted_checks} drifted check{s}"`
- Line 3 (magnitude breakdown): `text-slate-400` — `"Total drift: {total_drift} change{s}"` (this is where `total_drift` earns its keep, without needing a second axis)

**Legend:** none (single plotted series).

### Empty state — critical, this is the default state on day 1

The `drift_samples` table is forward-only with no backfill (per the backend plan), so this chart **will** render with zero rows for some period after ship. This must look intentional and informative, never like a broken axis or a loading stall.

**Exact spec:**
- Replace the entire chart canvas region (same `h-[240px]` footprint as the populated state, so the card doesn't jump in height when data starts arriving) with a centered empty-state block:
  ```
  <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-center">
    <HistoryIcon className="h-8 w-8 text-slate-600" />
    <p className="text-sm text-slate-400">Drift history is being collected</p>
    <p className="max-w-[280px] text-xs text-slate-500">
      Trend appears after the first drift checks run across your fleet.
    </p>
  </div>
  ```
- Icon: Lucide `History` (or `Hourglass` as an alternative — either reads as "accumulating over time" rather than "broken/missing"), sized `h-8 w-8`, colored `text-slate-600` — deliberately muted/decorative, not an error-red or warning-amber icon (this is not a failure state).
- Do **not** render axes, gridlines, or a zero-value bar chart behind/around this message — a fully-empty Recharts canvas with visible axes reads as broken. Suppress the chart entirely and show only this block when the API returns an empty array (or when `days`-range total sample count is 0).
- Trigger condition: render this state when `data.length === 0` from `/api/dashboard/drift-trend`, **not** when all values are 0 (see the green "No drift detected" callout above for that case — those are semantically different: no data collected yet vs. data collected and it's all zero).
- Card header (title "Drift Trend" + subtitle) still renders normally above the empty-state block — only the chart canvas region is replaced.

---

## 3. Compact activity-feed panel

Shows the last **10** audit events (`GET /api/audit-log?limit=10`), read-only, densest of the three new panels.

**Card**
- Wrapper: `rounded-lg border border-slate-700 bg-slate-900 p-4 flex flex-col`
- Header: `flex items-center justify-between mb-3`
  - Title: `text-sm font-semibold text-white` — "Recent Activity"
  - Link: `text-xs text-blue-400 hover:underline` — `<Link href="/audit">View all →</Link>` (matches the FRONTEND.md link convention: `text-blue-400 hover:underline`)
- List container: `h-[240px] overflow-y-auto` (same height as the two chart canvases, so all three cards line up flush in the 3-column row) — this is a deliberately dense scroll region; ~10 compact rows will exceed 240px, and that's fine, the container scrolls internally rather than growing the card taller than its chart siblings.

**Row layout** — one row per audit entry, `flex items-start gap-2.5 py-2 border-b border-slate-800 last:border-b-0` (note: `border-slate-800`, one step darker/subtler than the card's own `border-slate-700`, so internal row dividers read as secondary to the card boundary):

```
[dot]  {actor} {action phrase} {entity}              [outcome badge, if applicable]
       {relative time}
```

Concretely:
```tsx
<div className="flex items-start gap-2.5 py-2 border-b border-slate-800 last:border-b-0">
  <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dotColor)} />
  <div className="min-w-0 flex-1">
    <p className="truncate text-xs text-slate-300">
      <span className="font-medium text-white">{actor}</span>{" "}
      <span className="text-slate-400">{actionPhrase}</span>{" "}
      <span className="font-medium text-slate-200">{entityLabel}</span>
    </p>
    <div className="mt-0.5 flex items-center gap-1.5">
      <span className="text-[11px] text-slate-500">{formatRelativeTime(entry.created_at)}</span>
      {outcomeBadge}
    </div>
  </div>
</div>
```

**Fields, exactly as requested:**
- **Relative timestamp**: `formatRelativeTime(entry.created_at)` (existing helper in `lib/utils.ts`) — e.g. "3m ago", "2h ago" — rendered in the meta row under the main line, `text-[11px] text-slate-500`.
- **Actor / user_email**: `entry.user_email ?? (entry.user_id ? `User #${entry.user_id}` : "System")` — `font-medium text-white`, matches the existing audit table's rendering (`app/(dashboard)/audit/page.tsx`).
- **Action indicator**: a small `h-1.5 w-1.5 rounded-full` colored dot to the left of the row (not a full badge — the row is too dense for one), color-mapped below.
- **Entity**: `${entry.entity_type.replace("_", " ")}${entry.entity_id ? ` #${entry.entity_id}` : ""}` — e.g. "host #4", "ssh key #12" — `font-medium text-slate-200`, reads as the object of the sentence.
- **Outcome**: only rendered when derivable (sync/drift rows) — reuses `SyncStatusBadge` directly (do not invent a new badge component) at its native size, placed at the row's right edge on the meta line.

**Action phrase map** (`text-slate-400`, lowercase, sits between actor and entity to form a natural-language sentence) — derived from the real `action` values seen in the backend (`app/api/audit.py` callers):

| `action` | Phrase |
|---|---|
| `create` | "created" |
| `update` | "updated" |
| `delete` | "deleted" |
| `sync_triggered` | "triggered a sync on" |
| `sync_completed` | "completed a sync on" |
| `sync_failed` | "sync failed on" |
| `add_hosts` | "added hosts to" |
| `remove_hosts` | "removed hosts from" |
| `trust_host_key` | "trusted the host key for" |
| `session_start` | "started an SSH session on" |
| `session_end` | "ended an SSH session on" |
| *(fallback, any other action)* | `action.replace(/_/g, " ")` verbatim |

**Dot color map (`dotColor`)** — reuses the exact semantic palette from `SyncStatusBadge` / FRONTEND.md, so a user who already knows "amber = drift" instantly reads the feed the same way:

| Condition | Color | Class |
|---|---|---|
| `action === "delete"` | Error | `bg-red-500` |
| `action === "create"` or `add_hosts` | Success | `bg-green-500` |
| `action === "update"` | Info | `bg-blue-500` |
| `action === "sync_completed"` | Success | `bg-green-500` |
| `action === "sync_failed"` | Error | `bg-red-500` |
| `action === "sync_triggered"` | Info/pending | `bg-blue-500` |
| drift-related row where derived status is `out_of_sync` | Warning | `bg-amber-500` |
| `session_start` / `session_end` | Neutral | `bg-slate-500` |
| fallback / anything unrecognized | Neutral | `bg-slate-500` |

**Outcome badge**: for rows where `entry.action` is one of `sync_completed` / `sync_failed` / drift-check related and a status is derivable from `entry.after_state` (e.g. `after_state.status` or the module outcome payload described in the backend plan's `sync_completed` audit shape), render `<SyncStatusBadge status={derivedStatus} />` at the end of the meta row. Rows without a derivable outcome (plain `create`/`update`/`delete`/session events) render no badge — don't force one.

**Density**: rows are intentionally tighter than the main audit table (`py-2` vs. the table's row padding) — this is a glanceable digest, not a data-entry surface. Text sizes: main line `text-xs` (12px), meta line `text-[11px]`, one step down from the app's default `text-sm` table rows, appropriate for a dashboard widget.

**Empty state** (no audit entries at all — fresh install):
```
<div className="flex h-[240px] flex-col items-center justify-center gap-2 text-center">
  <InboxIcon className="h-8 w-8 text-slate-600" />
  <p className="text-sm text-slate-400">No activity yet</p>
  <p className="max-w-[240px] text-xs text-slate-500">
    Actions will appear here as your fleet is managed.
  </p>
</div>
```
Same visual language as the drift chart's empty state (muted icon, two-line copy, same `h-[240px]` footprint) — all three panels share one empty-state idiom across the dashboard.

---

## 4. Layout — responsive grid

Three panels, arranged as a single grid row that collapses gracefully:

```tsx
<div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
  <div className="xl:col-span-1">{/* Sync Success Rate chart card */}</div>
  <div className="xl:col-span-1">{/* Drift Trend chart card */}</div>
  <div className="md:col-span-2 xl:col-span-1">{/* Activity Feed card */}</div>
</div>
```

Breakpoint behavior:
- **`< md` (mobile, <768px):** `grid-cols-1` — all three stack full-width, in this order: Sync Success Rate → Drift Trend → Recent Activity.
- **`md` (tablet, 768–1279px):** `grid-cols-2` — the two charts sit side by side in row 1; the feed panel spans both columns (`col-span-2`) in row 2, full width, giving the dense row list more horizontal room to breathe on a medium screen where three-across would be cramped.
- **`xl` (desktop, ≥1280px):** `grid-cols-3` — all three panels in one row, equal width: Sync Success Rate | Drift Trend | Recent Activity. This is the "two-column charts row + feed as third column" layout called for.
- Gap: `gap-4` (one step up from the `gap-3` used in the StatCard grids above — these are heavier content panels and benefit from slightly more breathing room).
- No change to the panels' internal height across breakpoints — each card keeps its `h-[240px]` content region regardless of column width, so the row's overall height stays predictable.

---

## 5. Loading states

Use `Skeleton` primitives from `components/ui/skeleton.tsx` directly (the existing `CardSkeleton` — fixed title + 3 text lines — isn't the right shape for a chart canvas; compose new skeleton layouts per-panel using the base `Skeleton` component instead). Gate with `useDelayedLoading` (200ms) per the existing dashboard pattern, so fast responses don't flicker.

**Chart card skeleton** (used for both Sync Success Rate and Drift Trend while loading):
```tsx
<div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
  <Skeleton className="mb-1 h-4 w-40" />        {/* title */}
  <Skeleton className="mb-4 h-3 w-28" />         {/* "Last 7 days" subtitle */}
  <Skeleton className="h-[240px] w-full rounded-md" /> {/* chart canvas placeholder */}
</div>
```

**Activity feed skeleton:**
```tsx
<div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
  <div className="mb-3 flex items-center justify-between">
    <Skeleton className="h-4 w-28" />            {/* "Recent Activity" */}
    <Skeleton className="h-3 w-14" />             {/* "View all →" */}
  </div>
  <div className="space-y-3">
    {[85, 70, 90, 60, 75, 80].map((w, i) => (
      <div key={i} className="flex items-start gap-2.5">
        <Skeleton className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" />
        <div className="flex-1 space-y-1">
          <Skeleton className="h-3" style={{ width: `${w}%` }} />
          <Skeleton className="h-2 w-16" />
        </div>
      </div>
    ))}
  </div>
</div>
```
Six skeleton rows (not ten) — enough to communicate "a list is loading" without visually competing with the real 10-row scroll content once loaded.

---

## 6. Color token table (ChartConfig-ready)

Per the backend plan's intent to hand-port a trimmed shadcn `components/ui/chart.tsx` (`ChartConfig`/`ChartContainer`/`ChartTooltipContent`), the two chart series should be declared as:

```ts
// sync-success-chart.tsx
export const syncRateChartConfig = {
  rate: { label: "Success Rate", color: "#22c55e" }, // green-500
} satisfies ChartConfig

// drift-trend-chart.tsx
export const driftTrendChartConfig = {
  drifted_checks: { label: "Drifted Checks", color: "#f59e0b" }, // amber-500
  total_drift: { label: "Total Drift", color: "#fbbf24" },       // amber-400 — tooltip/optional-overlay only, see §2
} satisfies ChartConfig
```

Full palette reference (exact hex = standard Tailwind v4 palette values, safe to hardcode in chart SVG props since recharts doesn't resolve Tailwind classes):

| Token | Tailwind class | Hex | Used for |
|---|---|---|---|
| Success / in-sync | `green-500` | `#22c55e` | Sync-rate area stroke + gradient top-stop; drift-trend "no drift" callout text is `green-400` |
| Success (emphasis) | `green-400` | `#4ade80` | Sync-rate active dot fill; tooltip rate value; feed dot (create/sync_completed) |
| Warning / drift | `amber-500` | `#f59e0b` | Drift-trend bar fill; feed dot (derived out_of_sync) |
| Warning (hover/lighter) | `amber-400` | `#fbbf24` | Drift bar hover fill; `total_drift` optional overlay line |
| Error | `red-500` / `red-600` | `#ef4444` / `#dc2626` | Feed dot (delete / sync_failed) — not used in the two charts |
| Info / pending | `blue-500` / `blue-600` | `#3b82f6` / `#2563eb` | Feed dot (update / sync_triggered) |
| Neutral | `slate-500` | `#64748b` | Feed dot (session events, fallback) |
| Grid lines | `slate-800` | `#1e293b` | `CartesianGrid` stroke, both charts |
| Axis line / border | `slate-700` | `#334155` | `axisLine` stroke, card borders, row dividers reference point |
| Row divider (feed, subtler) | `slate-800` | `#1e293b` | Feed row `border-b` |
| Axis tick text | `slate-400` | `#94a3b8` | X/Y axis tick labels, both charts |
| Card / tooltip surface | `slate-900` | `#0f172a` | Card backgrounds, tooltip background, active-dot stroke |
| Muted icon (empty states) | `slate-600` | `#475569` | Empty-state icons (both drift chart and feed) |
| Muted heading (empty states) | `slate-400` | `#94a3b8` | Empty-state primary line |
| Muted body (empty states) | `slate-500` | `#64748b` | Empty-state secondary line |

---

## Summary of files a frontend engineer would touch (for reference only — not part of this design deliverable)

- `frontend/components/dashboard/sync-success-chart.tsx` (new)
- `frontend/components/dashboard/drift-trend-chart.tsx` (new)
- `frontend/components/dashboard/activity-feed-panel.tsx` (new)
- `frontend/components/ui/chart.tsx` (new — trimmed base-ui-compatible port, per `plans/dashboard-charts.md`)
- `frontend/app/(dashboard)/dashboard/page.tsx` (insert the grid row from §4)
- `frontend/lib/types.ts` (add `SyncRatePoint`, `DriftTrendPoint` types mirroring the backend Pydantic schemas)
