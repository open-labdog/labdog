# LabDog Frontend — Design & Pattern Reference

This document defines the frontend conventions for LabDog. All new pages, components, and modifications must follow these patterns.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router), built as a static export served by the backend |
| UI | The LabDog kit in `components/ld` — the only component layer. Dialogs are built on `@base-ui/react`'s dialog; the command palette on `cmdk`. |
| Styling | Tailwind CSS v4 over the LabDog design tokens in `app/globals.css` — dark is primary, light is a real second theme |
| Data Fetching | TanStack Query (`@tanstack/react-query`) |
| API Client | `apiFetch()` from `lib/api.ts` |
| Auth | `useAuth()` context from `lib/auth.ts` |
| Forms | React Hook Form + Zod (`lib/schemas.ts`) |
| Toasts | Sonner, through `lib/toast.ts` |
| Terminal | xterm.js (`components/ssh-terminal.tsx`) |

There is no icon library: the design is text-first — `▾ ▸ →` glyphs,
`Dot`, `Tag` — and the rail's line icons are hand-drawn SVG in
`components/shell/glyph.tsx`.

---

## Shell: icon rail + contextual pane

The navigation is four zones on a 50px icon rail, each with a 208px pane of
destinations, plus Settings at the foot of the rail. The rail never grows
with the feature count — new destinations go into a zone's pane, or into
the command palette. Config is deliberately not a zone: a module's desired
state belongs to the group that declares it and is edited on the group's
page; a host's effective state is read on the host's page.

| Zone | Pane items | Routes |
|------|-----------|--------|
| Overview | Summary · Pending · Fleet state · Activity · Upcoming | `/overview` (`?view=pending|state|activity|upcoming`) |
| Fleet | Hosts · Groups · Discovery | `/hosts`, `/hosts/[id]` (`?tab=`), `/groups`, `/groups/[id]` (`?tab=overview|config|members|activity`, `&module=<id>`, `&view=schedules`), `/discovery` (`?tab=pending|schedules|scan`) |
| Operations | Plans · Drift · Actions · Runs · Audit | `/plans`, `/drift`, `/actions` (`?tab=library|packs|schedules`), `/runs`, `/audit` |
| Assistant | Sessions · Alerts | `/assistant`, `/alerts` |
| Settings | (no pane — five section tabs) | `/settings` (`?section=integrations|ai|access|fleet|system`) |

Everything lives in `components/shell/`:

- `zones.ts` — the zone/route registry: `ZONE_DEFS`, `SETTINGS_ZONE`,
  `zoneForPath`, `zoneDef`, `itemIsActive`. Add a destination here, never a
  new rail entry. Legacy routes are mapped to the zone their content moved
  to.
- `rail.tsx` (the rail, its bottom-tab-bar form and `ThemeToggle`),
  `pane.tsx`, `palette.tsx`, `account-menu.tsx`, `glyph.tsx` (the rail's
  line icons)
- `use-shell-counts.ts` — the numbers the pane and the Overview badge show;
  every query shares its key with the page that owns the data.
- `redirect.tsx` — client-side redirect for moved routes (the production
  build is a static export, so there is no server to 301).

`components/app-shell.tsx` composes them. Breakpoints: below 1180px the pane
becomes an overlay that closes on navigation; below 640px the rail is a
bottom tab bar and the pane is gone. Keyboard: `⌘K`/`Ctrl+K` palette, `[`
toggles the pane, `t` toggles the theme (never while typing in a field).

**Legacy routes** (`/dashboard`, `/hosts/discovery`, `/hosts/pending`,
`/hosts/discover`, `/hosts/scans`, `/schedules`, `/action-packs`,
`/settings/about`, `/settings/proxmox`, `/pending`, the per-module
`/groups/[id]/<module>` pages) redirect to their new home so deep links keep
working. Every other route is a screen: the shell's `<main>` is a flush
column and each screen owns its header and scroll region.

### Screens

A screen is a `PageHead` (crumbs, title, sub, actions, optional filter row
or tabs) followed by a `.scroll` body or a `Table`, and is wrapped by a
server `page.tsx` with a `Suspense` boundary when it reads
`useSearchParams` (required by the static export). It uses the kit in
`components/ld` and the `.btn` classes.

Dynamic segments in a static export must be **numeric** (`/hosts/123`,
`/groups/7`) or prerendered with `generateStaticParams` — the backend's SPA
fallback only substitutes digits into the placeholder page. Anything else
that varies (a module, a tab) goes in the query string.

### The group page

`/groups/[id]` follows the host page's pattern — Overview · Config ·
Members · Activity — and is where a group is edited; there is no edit
dialog (the `GroupEditor` modal creates groups only). The URL is the
state: `?tab=config&module=firewall`, `?tab=activity&view=schedules`; the
old module tabs (`?tab=rules`, `?tab=dns` …) still resolve.

- **Overview** — settings inline (name, category, description, priority
  with the tie warning and every winner/loser flip a move causes, from
  `useGroupMerge` in `components/group-editor.tsx`), declared modules,
  members, the danger zone, GitOps, recent runs.
- **Config** — the eight modules on the left, the module's editor on the
  right. The editors are `components/config/*-editor.tsx`, embedded-only:
  each takes a `groupId` and renders a `Toolbar` + `Table` with no head of
  its own. A module is "declared" once the group has an item for it.
- **Members** — add (inline picker, one POST per tick) and remove hosts.
- **Activity** — the group's action runs, or its schedules
  (`ScheduledActionsSection`).

"Run action…" on the group and host heads is `components/run-action-button.tsx`:
the same `ActionRunDialog` an action's own run button opens, with an action
picker because the entry point is the target. "Plan sync — N hosts" opens
`/plans?scope=group:<id>` (plus `&modules=` from the Config tab).

---

## Typography

| Role | Font | CSS Variable | Weights |
|------|------|-------------|---------|
| Body / UI | **Atkinson Hyperlegible Next** (vendored) | `--font-sans` | 200–800 variable |
| Code / Mono | **Atkinson Hyperlegible Mono** (vendored) | `--font-mono` | 200–800 variable |

Loaded in `app/layout.tsx` via `next/font/local` from `app/fonts/` (see the
README there). Applied to `<body>` via `font-sans` class. Body size is 13px.
Both faces are drawn to keep easily confused shapes apart — l/I/1, O/0,
rn/m — which is most of what reading a hostname or an address needs.

### Usage

- Page titles come from `PageHead` (19px semibold) and are sans — even when
  the title is a hostname or a group name. Section labels are `.tt`
  (uppercase sans, 9.5px, `--text-3`).
- Every value an operator might copy — hostnames, IPs, ports, CIDRs, cron
  lines — is `.mono`; counts add `.num` for tabular figures (both fonts carry
  `tnum`).
- `Tag` is sans by default. Pass `mono` when the chip holds an identifier —
  a hostname, a module or group name, a CIDR, a branch, a priority, a
  `+3 −1` diff count. Status words and kinds stay sans.
- Body text `text-xs`/`text-[12.5px]` in `text-text` (primary), `text-text-2`
  (secondary), `text-text-3` (muted), `text-text-faint` (decorative only).

---

## Color Palette

Two themes, both defined as tokens in `app/globals.css` under
`html[data-theme="dark"]` (primary) and `html[data-theme="light"]` (a real
second theme, not an inversion). `next-themes` writes both `class` and
`data-theme` on `<html>`, persisted under `labdog:theme`.

| Token | Tailwind | Use |
|-------|----------|-----|
| `--bg` | `bg-bg` | page |
| `--surface`, `--surface-2`, `--surface-3` | `bg-surface`, `bg-surface-2`, `bg-surface-3` | panels · panel headers/hover · segmented controls |
| `--rail` | `bg-rail` | the rail |
| `--border`, `--border-strong`, `--border-faint` | `border-line`, `border-line-strong`, `border-line-faint` | dividers |
| `--text`, `--text-2`, `--text-3`, `--text-faint` | `text-text`, `text-text-2`, `text-text-3`, `text-text-faint` | four grades of ink |
| `--accent`, `--accent-soft`, `--accent-line`, `--accent-fill`, `--accent-ink` | `ld-accent*` | the one blue: active states, primary buttons, host overrides |
| `--ok` `--warn` `--danger` `--sync` `--idle` `--hold` `--add` `--del` | `bg-ok` … | status tones, all at one chroma so nothing shouts louder than its severity |
| `--<tone>-soft` / `--<tone>-ink` | `bg-ok-soft text-ok-ink` | a tint and the ink solved against it (≥5:1). **Never paint a raw tone on its own tint.** |

The Tailwind colour names for the accent are `ld-accent*` rather than
`accent*` for a historical reason — they once had to stay clear of a
component library's own `accent` — kept because renaming every class buys
nothing (see the `@theme` comment in `globals.css`).

In TSX the tone helpers in `components/ld/tone.ts` (`toneVar`, `toneSoft`,
`toneInk`, `tint`) resolve a tone name to these variables; `Tag tone="warn"`
and `Dot tone="ok"` do it for you. Status vocabularies — which word and tone
each backend value gets — live in two places: `lib/fleet.ts` (`STATUS`, the
host sync statuses used by `Status`, `StatusBar` and the palette) and
`lib/status.ts` (everything else: run and job states, GitOps, item and
package states, systemd states, firewall actions and backends, audit
actions, alert status and severity, assistant sessions), read with
`def(VOCAB, value)`.

---

## Layout Architecture

```
app/layout.tsx              — Fonts, Providers (theme, query client, auth, toaster), AppShell
├── components/app-shell.tsx    — Rail + pane + content column; mobile header + bottom rail; palette; shortcuts
├── components/shell/*          — zones registry, Rail, Pane, Palette, AccountMenu, glyphs, redirect helper
├── components/ld/*             — the LabDog UI kit (see below)
└── app/(dashboard)/...         — every screen (inside the shell)
    app/(auth)/...              — login/register (no shell, centred card)
```

### AppShell Pattern

`components/app-shell.tsx` checks the current pathname. Auth routes (`/login`, `/register`) render children bare. Everything else renders the rail, the current zone's pane and a content column. Account things — email, change password, About, log out — live behind the avatar at the foot of the rail (`components/shell/account-menu.tsx`), where they cannot be mistaken for navigation.

### The LabDog kit (`components/ld`)

Dense, token-driven primitives ported from the design prototype, all
exported from `@/components/ld`:

| Piece | What it is |
|-------|------------|
| `PageHead` | Crumbs, title, sub, actions, and a children slot for tabs or a filter row |
| `Panel`, `Split` | A titled box (title, meta, actions) · a responsive grid of panels |
| `Tabs`, `Seg` | Underlined tabs with counts · a segmented control |
| `Toolbar` | A strip above a table: a label on the left, actions on the right |
| `Filter` | A dropdown chip that picks one option (each with its count) and reads as a sentence when set — `status · drifted` |
| `Table`, `Col`, `Sort` | A CSS-grid table with table roles — sort, a selection column, row click, row tone, sticky head, `loading`, `empty` |
| `BulkBar` | The bar that appears while rows are selected |
| `Tag`, `Dot`, `Status`, `RunStatus`, `Provenance`, `Kbd`, `Empty` | Atoms — a chip, a status dot, a host sync status, a run status, the "comes from" chip, a key cap, an empty state |
| `Banner` | A toned sentence with an optional action; `flush` for full width, `pulse` for waiting-on-you |
| `Facts`, `Stat`, `CodeBlock`, `Copy` | A key/value grid · a big number · a scrolling code/log block (pinnable to the bottom) · a copy button |
| `Window` | A log or terminal the viewer can size: text size, minimize (hides, never unmounts), maximize to the browser window, a drag strip for the height on its free edge (`anchor` top or bottom; never past the parent's box); all remembered per browser |
| `Pager` | A table footer's `1–10 of 17 · ‹ › · 10 per page`, with `pageSlice` / `lastPage` for the rows |
| `Spark`, `Meter`, `StatusBar` | A sparkline · a percentage bar that turns amber above 75% and red above 90% · the fleet status bar |
| `Modal` | The dialog — title, meta, **esc**, a scrolling body, a footer strip. `onSubmit` turns the whole popup into a form. |
| `Confirm` | The one yes/no dialog — see [Confirmation Dialogs](#confirmation-dialogs) |
| `Field`, `Help` | A labelled control with `hint` / `error` · a "why" disclosure for a paragraph of explanation |
| `Steps` | The step strip for a multi-step modal (the connect wizard, the schedule wizard) |

Buttons are the `.btn` / `.btn-sm` / `.btn-primary` / `.btn-ghost` /
`.btn-danger` classes from `globals.css`; every text control — input,
select and textarea — is `.inp` inside a `Field`.

Shared vocabulary: `lib/modules.ts` is the one registry of the eight modules
and their several spellings (route segment, host tab, `ModuleCounts` key,
sync `module_filter` name); `lib/fleet.ts` holds the host status vocabulary
and the age helpers (`shortAgo`, `ageLabel`, `plural`); `lib/status.ts` the
other status vocabularies; `lib/activity.ts` merges sync jobs and action
runs into one stream; `lib/pending.ts` builds the Pending queue's lanes.

---

## Component Patterns

### A screen

```tsx
"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { def, ITEM_STATE } from "@/lib/status"
import { Banner, Empty, PageHead, Table, Tag } from "@/components/ld"

export default function ThingsPage() {
  const { data, isLoading, error } = useQuery<Thing[]>({ queryKey: ["things"], queryFn: () => apiFetch<Thing[]>("/api/things") })
  const [creating, setCreating] = useState(false)

  return (
    <>
      <PageHead
        crumbs={[{ label: "settings", href: "/settings" }, { label: "integrations", href: "/settings?section=integrations" }]}
        title="Things"
        sub="One sentence on what this screen is for."
        actions={<button type="button" className="btn btn-sm btn-primary" onClick={() => setCreating(true)}>Add thing…</button>}
      />
      {error && <Banner tone="danger" flush>Could not load things: {error.message}</Banner>}
      <Table<Thing>
        cols={[
          { k: "name", label: "name", w: "minmax(140px,1fr)", cell: (t) => <span className="mono font-medium text-text">{t.name}</span> },
          { k: "state", label: "state", w: "90px", cell: (t) => <Tag tone={def(ITEM_STATE, t.state).tone}>{t.state}</Tag> },
        ]}
        rows={data ?? []}
        keyOf={(t) => t.id}
        loading={isLoading}
        empty={<Empty title="No things yet" note="What adding one does, in a sentence." />}
      />
      {creating && <ThingDialog onClose={() => setCreating(false)} />}
    </>
  )
}
```

Column labels are lowercase; row actions are lowercase ghost buttons at the
end of the row (`edit · delete`), not a kebab menu.

### Dialogs

A dialog is a `Modal` (or `Confirm` for yes/no). The form idiom: `Modal
onSubmit`, `Field` + `.inp` controls in the body, and a footer of an
optional caption, **Cancel**, then the primary action.

```tsx
<Modal
  title="Add thing"
  onClose={onClose}
  onSubmit={form.handleSubmit(save)}
  footer={
    <>
      <span className="tt mr-auto">nothing applies until a plan runs</span>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
        {saveMutation.isPending ? "Saving…" : "Add thing"}
      </button>
    </>
  }
>
  <Field label="name" htmlFor="thing-name" error={form.formState.errors.name?.message}>
    <input id="thing-name" className="inp mono" {...form.register("name")} />
  </Field>
  <Field label="state" htmlFor="thing-state" hint="absent removes it from every host">
    <select id="thing-state" className="inp" {...form.register("state")}>
      <option value="present">present</option>
      <option value="absent">absent</option>
    </select>
  </Field>
  {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
</Modal>
```

Field labels are lowercase. Render the modal conditionally
(`{open && <Modal …/>}`) so its form state resets each time it opens.

---

## Data Fetching

### API Client (`lib/api.ts`)

```tsx
import { apiFetch } from "@/lib/api"

// GET
const data = await apiFetch<Type[]>("/api/endpoint")

// POST
await apiFetch("/api/endpoint", {
  method: "POST",
  body: JSON.stringify({ field: value }),
})

// PUT
await apiFetch(`/api/endpoint/${id}`, {
  method: "PUT",
  body: JSON.stringify(body),
})

// DELETE (returns undefined for 204)
await apiFetch(`/api/endpoint/${id}`, { method: "DELETE" })
```

`apiFetch` handles:
- `credentials: "include"` (httpOnly cookie auth)
- JSON error parsing (extracts `detail` from response body)
- 204 No Content responses (returns `undefined`)
- Pydantic validation errors (formats array of `{msg, loc}`)

**Exception**: `PATCH /users/me` (fastapi-users endpoint) is NOT under `/api/` prefix. Use raw `fetch()` with `API_BASE` for this endpoint.

### TanStack Query

```tsx
// Query
const { data, isLoading, error } = useQuery<Type[]>({
  queryKey: ["resource-name"],                    // Stable key for cache
  queryFn: () => apiFetch<Type[]>("/api/..."),
  enabled: !!someCondition,                       // Optional: conditional fetching
  refetchInterval: 10000,                         // Optional: polling
})

// Mutation — useApiMutation from @/lib/mutations
import { useApiMutation } from "@/lib/mutations"

const createMutation = useApiMutation({
  mutationFn: (data: ItemInput) => apiFetch("/api/...", { method: "POST", body: JSON.stringify(data) }),
  invalidateKeys: [["resource-name"]],
  successMessage: "Item created",
  onSuccess: () => onClose(),
})

// Usage: createMutation.mutate(formData)
// Loading: createMutation.isPending
// Error: createMutation.error
```

A plain `async` handler with `try/catch` and `queryClient.invalidateQueries`
is still fine for a flow with several side effects in sequence.

### Query Key Conventions

| Resource | Key |
|----------|-----|
| Hosts list | `["hosts"]` |
| Single host | `["host", id]` |
| Host effective rules | `["host-effective-rules", id]` |
| Groups list | `["groups"]` |
| Single group | `["group", id]` |
| Group rules | `["rules", groupId]` |
| SSH keys | `["ssh-keys"]` |
| Git repos | `["git-repos"]` |
| Admin users | `["admin-users"]` |

Files that split one screen (a tab, a dialog) share query keys **by
string**, not through a prop-drilled hook — the key is the contract.

---

## Auth Patterns

Sign-in and first-run are `AuthCard` + `AuthHeading` from
`components/auth-background.tsx`, with `Field` + `.inp` controls. A
superuser-only screen gates at render time, not with a redirect, and says
where the gate is rather than showing an empty table:

```tsx
import { useAuth } from "@/lib/auth"

const { user, loading } = useAuth()

if (loading) return null
if (!user?.is_superuser) {
  return (
    <>
      <PageHead crumbs={CRUMBS} title="Users" sub="Accounts and superuser status." />
      <Empty
        title="Administrators only"
        note="Only a superuser can manage accounts. Ask one to make the change, or to make you one."
        action={<Link href="/settings?section=access" className="btn btn-sm hover:no-underline">Settings › Access →</Link>}
      />
    </>
  )
}
```

---

## File Conventions

| Path | Purpose |
|------|---------|
| `app/(dashboard)/*/page.tsx` | Screens (inside the shell); a server `page.tsx` wrapping a `client-page.tsx` where the screen reads search params |
| `app/(auth)/*/page.tsx` | Auth pages (no shell, centred card) |
| `components/ld/*` | The LabDog kit |
| `components/shell/*` | The rail, pane, palette, account menu and zone registry |
| `components/config/*-editor.tsx` | The eight group module editors |
| `components/ai/*` | The assistant's pieces — transcript, tool call, approval gate, session list and aside, usage panel, markdown renderer |
| `components/*.tsx` | Other shared components — app shell, run dialog and run screen, group editor, sync tray, terminal |
| `lib/api.ts` | API client (`apiFetch`, `API_BASE`) |
| `lib/auth.ts` | Auth context and `useAuth()` hook |
| `lib/types.ts` | All TypeScript interfaces for API responses |
| `lib/fleet.ts`, `lib/status.ts` | Status vocabularies and age helpers |
| `lib/utils.ts` | `formatTimestamp` |

### Page file rules

- Client components start with the `"use client"` directive.
- A screen with enough going on gets private `_tabs/` and `_dialogs/`
  folders beside its `page.tsx` — one file per tab or dialog. The host page
  is the example: `app/(dashboard)/hosts/[id]/_tabs/overview.tsx`,
  `_tabs/config/firewall.tsx` …, `_dialogs/edit-host.tsx`.
- Anything used by more than one screen goes in `components/`.

---

## Toast Notifications

Use `showSuccess`, `showError`, `showInfo` from `@/lib/toast` (wraps Sonner).

- **When to use**: Only on mutations (create, update, delete). Never on reads or navigation.
- **Success**: Auto-dismisses after 3 seconds
- **Error**: Persists until manually dismissed
- **Position**: Bottom-right

```tsx
import { showSuccess, showError } from "@/lib/toast"
showSuccess("SSH key deleted")
showError("Failed to delete: " + error.message)
```

---

## Loading States

No skeletons. A table passes `loading` to `Table`; a status that is still
settling is a pulsing `Dot`; a button swaps its label while it works
(`Saving…`, `collecting…`) and disables itself.

---

## Confirmation Dialogs

Use `Confirm` from `@/components/ld` instead of `window.confirm()`.

- **Destructive actions** (delete, disable): `variant="destructive"` (the danger button)
- **Default actions**: `variant="default"` (the primary button)

While `loading`, the button says what it is doing and nothing can close
the dialog from under it.

```tsx
import { Confirm } from "@/components/ld"

const [deleting, setDeleting] = useState<Thing | null>(null)

<Confirm
  open={deleting !== null}
  onOpenChange={(open) => !open && setDeleting(null)}
  title="Delete thing"
  description={`Delete ${deleting?.name}? This cannot be undone.`}
  confirmLabel="Delete"
  variant="destructive"
  loading={deleteMutation.isPending}
  onConfirm={() => deleting && deleteMutation.mutate(deleting.id)}
/>
```

An in-place two-step button ("Delete group" → "Confirm — 12 hosts drop this
group") is for an object's own danger zone; a row's delete goes through
`Confirm`.

---

## Form Validation

Use React Hook Form + Zod. Schemas are in `@/lib/schemas`.

- **Validation timing**: `mode: "onSubmit"` (errors show only after submit attempt)
- **Error display**: `Field`'s `error` prop, which replaces the hint and is announced (`role="alert"`)
- **Edit forms**: `form.reset(existingData)` when the dialog opens, or mount the dialog fresh each time

```tsx
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { groupSchema, type GroupInput } from "@/lib/schemas"

const form = useForm<GroupInput>({
  resolver: zodResolver(groupSchema),
  defaultValues: { name: "", priority: 1 },
  mode: "onSubmit",
})

<Field label="name" htmlFor="name" error={form.formState.errors.name?.message}>
  <input id="name" className="inp" {...form.register("name")} />
</Field>
```

---

## Error Boundaries

`app/(dashboard)/error.tsx` catches errors in screens: a kit card with a
danger `Banner` carrying the message, **Try again** and **Go to Overview**.

`app/global-error.tsx` catches root-level errors. It uses inline styles in
the dark theme's token values, because it renders when the app's own
stylesheet may not have.

---

## Breadcrumbs

`PageHead`'s `crumbs` — a `<nav aria-label="breadcrumb">` of the page's
parents only; the title is the current page.

```tsx
<PageHead crumbs={[{ label: "fleet", href: "/hosts" }, { label: "groups", href: "/groups" }]} title={group.name} />
```

Crumbs are lowercase; the first is the zone.

---

## Tooltips

There is no tooltip component.

- An icon-only affordance gets a `title=` (and an `aria-label`).
- A one-line explanation of a field is `Field hint=`.
- A paragraph — a caveat, a consequence that is not obvious — is `Help`, a
  native `<details>` summarised as **why**.

---

## Command Palette

`components/shell/palette.tsx`, opened with Cmd/Ctrl+K or the rail's search button. It indexes every destination (zone items and Settings sections), every host, every group, every module × group pair (landing on the group's Config tab), and a handful of verbs (plan a sync, check the fleet for drift, approve pending hosts, start a scan, add a host, new group, open a terminal on a host). Before anything is typed it shows destinations and verbs only. The palette is infrastructure: it is what lets the rail stay at four entries.

---

## Keyboard Shortcuts

- `Cmd/Ctrl+K` — open command palette
- `[` — toggle the contextual pane
- `t` — toggle dark/light theme
- `Escape` — close any open dialog or the command palette (handled by the base-ui dialog)

The single-key shortcuts never fire while an input, textarea, select or contenteditable has focus.

---

## Mobile Responsive

Below 1180px the pane overlays the content (opened from the `›` strip, closes on navigation). Below 640px the rail moves to the bottom edge as four labelled tabs, a slim header carries the zone name, search, the theme toggle and the account menu, and there is no pane. CSS transitions only (no Framer Motion).

---

## Bulk Actions

Selecting rows in a `Table` opens a `BulkBar` — on hosts (enable or disable
drift check, delete), groups (delete, or plan a sync for one) and SSH keys
(delete). Discovery's pending queue acts on the ticked rows from its
toolbar instead (approve, dismiss). Deletes are sequential
single-item API calls (there is no batch endpoint), with a partial-failure
toast: "Deleted {success} of {total}. {failed} failed."

---

## Mutations

Use `useApiMutation` from `@/lib/mutations` instead of ad-hoc try/catch.

```tsx
import { useApiMutation } from "@/lib/mutations"

const deleteMutation = useApiMutation({
  mutationFn: (id: string) => apiFetch(`/api/ssh-keys/${id}`, { method: "DELETE" }),
  invalidateKeys: [["ssh-keys"]],
  successMessage: "SSH key deleted",
})

// Usage: deleteMutation.mutate(id)
// Loading: deleteMutation.isPending
```

Optimistic updates available via `optimisticUpdate` option (for simple delete/toggle only).

---

## Things NOT Used (Intentionally)

| What | Why |
|------|-----|
| A component library (shadcn/ui, Radix …) | The kit in `components/ld` is the design; `@base-ui/react` supplies only the dialog behaviour under `Modal`. |
| An icon library | Text-first: `▾ ▸ →` glyphs, `Dot`, `Tag`. The rail's icons are drawn in `glyph.tsx`. |
| A styled select | Native `<select className="inp">` is the design, not a stopgap. |
| Drag and drop | Firewall rules reorder with ▲ / ▼. |
| System theme following | Dark is the default and light is an explicit choice (`t` or the rail toggle); `next-themes` runs with `enableSystem={false}`. |
| Framer Motion | No animation library. CSS transitions only. |
| Separate `/profile` page | Password change lives in the account menu at the foot of the rail. |
| Pagination | Tables show all data. LabDog manages tens/hundreds of items, not thousands. The audit log is the exception — it loads 100 entries at a time. |
