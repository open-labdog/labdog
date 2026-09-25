# LabDog Frontend — Design & Pattern Reference

This document defines the frontend conventions for LabDog. All new pages, components, and modifications must follow these patterns.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router) |
| UI Components | shadcn/ui (base-ui variant, **NOT Radix**) for dialogs, inputs and legacy pages; the LabDog kit in `components/ld` for screens on the shell |
| Styling | Tailwind CSS v4 over the LabDog design tokens in `app/globals.css` — dark is primary, light is a real second theme |
| Data Fetching | TanStack Query (`@tanstack/react-query`) |
| API Client | `apiFetch()` from `lib/api.ts` |
| Auth | `useAuth()` context from `lib/auth.ts` |
| Icons | Lucide React |
| Drag & Drop | `@dnd-kit/core` + `@dnd-kit/sortable` |

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

- `zones.ts` — the zone/route registry: `ZONE_DEFS`, `zoneForPath`,
  `isFlushRoute`, `itemIsActive`. Add a destination here, never a new rail
  entry. Legacy routes are mapped to the zone their content moved to.
- `rail.tsx`, `pane.tsx`, `palette.tsx`, `account-menu.tsx`, `glyph.tsx`
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
`/settings/about`, `/pending`) redirect to their new home so deep links keep
working. Pages that have not been rebuilt on the shell (`/hosts/[id]`,
`/assistant`, `/audit`, the integration pages …) render inside it
unchanged, in a padded scrolling `<main>`. Screens built for the shell are
listed in `FLUSH_ROUTES` / `FLUSH_PATTERNS` and own their header and
scroll region.

### Screens on the shell vs legacy pages

A screen built on the shell is a `PageHead` (crumbs, title, sub, actions,
optional filter row) followed by a `.scroll` body or a `Table`, and is
wrapped by a server `page.tsx` with a `Suspense` boundary when it reads
`useSearchParams` (required by the static export). It uses the kit in
`components/ld` and the `.btn` classes. A legacy page keeps the
`<div className="space-y-6">` + `Breadcrumb` + `<h1>` pattern below.

Dynamic segments in a static export must be **numeric** (`/hosts/123`,
`/groups/7`) or prerendered with `generateStaticParams` — the backend's SPA
fallback only substitutes digits into the placeholder page. Anything else
that varies (a module, a tab) goes in the query string.

### The group page

`/groups/[id]` follows the Host detail pattern — Overview · Config ·
Members · Activity — and is where a group is edited; there is no edit
dialog (the `GroupEditor` modal creates groups only). The URL is the
state: `?tab=config&module=firewall`, `?tab=activity&view=schedules`; the
old module tabs (`?tab=rules`, `?tab=dns` …) still resolve.

- **Overview** — settings inline (name, category, description, priority
  with the tie warning and every winner/loser flip a move causes, from
  `useGroupMerge` in `components/group-editor.tsx`), declared modules,
  members, the danger zone, GitOps, recent runs.
- **Config** — the eight modules on the left, the module's editor on the
  right. The editors under `groups/[id]/<segment>/client-page.tsx` are
  embedded with `embedded groupId={id}` and their own `<h1>` hidden; a
  module is "declared" once the group has an item for it.
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
| Body / UI | **IBM Plex Sans** (vendored) | `--font-sans` | 400–700 variable |
| Code / Mono | **IBM Plex Mono** (vendored) | `--font-mono` | 400, 500, 600 |

Loaded in `app/layout.tsx` via `next/font/local` from `app/fonts/` (see the
README there). Applied to `<body>` via `font-sans` class. Body size is 13px.

### Usage (screens on the shell)

- Page titles come from `PageHead` (19px semibold); section labels are `.tt`
  (uppercase mono, 9.5px, `--text-3`)
- Every value an operator might copy — hostnames, IPs, ports, CIDRs, cron
  lines — is `.mono`; counts add `.num` for tabular figures
- Body text `text-xs`/`text-[12.5px]` in `text-text` (primary), `text-text-2`
  (secondary), `text-text-3` (muted), `text-text-faint` (decorative only)

### Usage (legacy pages)

- Page titles: `text-2xl font-bold text-white`
- Page descriptions: `text-slate-400 text-sm mt-1`
- Table cell text: `text-slate-300 text-xs`
- Monospace values (IPs, ports, CIDRs): `font-mono text-slate-300 text-xs`
- Muted / secondary: `text-slate-400`
- Links: `text-blue-400 hover:underline` or `underline underline-offset-4 hover:text-primary`

---

## Color Palette

Two themes, both defined as tokens in `app/globals.css` under
`html[data-theme="dark"]` (primary) and `html[data-theme="light"]` (a real
second theme, not an inversion). `next-themes` writes both `class` and
`data-theme` on `<html>`, persisted under `labdog:theme`.

### Design tokens (screens on the shell)

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

In TSX the tone helpers in `components/ld/tone.ts` (`toneVar`, `toneSoft`,
`toneInk`, `tint`) resolve a tone name to these variables; `Tag tone="warn"`
and `Dot tone="ok"` do it for you. The status vocabulary — which word and
tone each backend `sync_status` gets — is `lib/fleet.ts` (`STATUS`), used by
`Status`, `StatusBar` and the palette alike.

shadcn's own variables (`--background`, `--card`, `--primary` …) are
re-pointed at these tokens, so components/ui follow both themes with no
`dark:` blocks. shadcn's neutral hover `--accent` is stored as `--ui-accent`
so `bg-accent` stays neutral while `var(--accent)` is the blue.

### Legacy palette (pages not yet on the shell)

The slate vocabulary below still works. In the light theme a transitional
block in `globals.css` re-points the slate scale and the saturated badge
colours at the light tokens, so legacy pages are not dark islands; it is a
bridge to delete entry by entry as pages migrate, not a design.

### Surfaces

| Surface | Class | Usage |
|---------|-------|-------|
| Page background | `bg-slate-950` | Root body |
| Sidebar | `bg-slate-950` | Sidebar aside |
| Cards / Table wrappers | `bg-slate-900` | Content containers |
| Sidebar active nav | `bg-slate-800` | Active menu item |
| Sidebar hover | `hover:bg-slate-800` | Nav item hover |
| Form inputs | `bg-slate-800` or `bg-transparent` | Input fields |

### Borders

| Element | Class |
|---------|-------|
| Cards, tables, pane | `border-slate-700` (legacy) / `border-line` |
| Table rows | `border-slate-700` |
| Form inputs | `border-slate-700` or `border-input` |
| Sidebar dividers | `border-slate-700` |

### Text

| Role | Class |
|------|-------|
| Primary (headings, names) | `text-white` |
| Secondary (descriptions, metadata) | `text-slate-400` |
| Table values | `text-slate-300` |
| Disabled / muted | `text-slate-400` (use `text-slate-500` only for decorative elements, not readable text at text-xs/text-sm sizes) |
| Error messages | `text-red-400` |
| Success messages | `text-green-400` |

### Semantic Badge Colors

| Meaning | Background | Text |
|---------|-----------|------|
| Success / Active / Synced / Allow | `bg-green-600` | `text-white` |
| Warning / Out of Sync | `bg-amber-600` | `text-white` |
| Error / Inactive / Deny | `bg-red-600` | `text-white` |
| Info / Pending / Importing | `bg-blue-600` | `text-white` |
| Neutral / Disconnected | `bg-slate-600` | `text-slate-300` |
| Superuser badge | `bg-purple-600` | `text-white` |
| Outline / metadata | `variant="outline"` | default |

---

## Layout Architecture

```
app/layout.tsx              — Fonts, Providers (theme, query client, auth), AppShell
├── components/app-shell.tsx    — Rail + pane + content column; mobile header + bottom rail; palette; shortcuts
├── components/shell/*          — zones registry, Rail, Pane, Palette, AccountMenu, redirect helper
├── components/ld/*             — the LabDog UI kit (see below)
└── app/(dashboard)/...         — All dashboard pages (inside the shell)
    app/(auth)/...              — Login/register pages (no shell, centered card layout)
```

### AppShell Pattern

`components/app-shell.tsx` checks the current pathname. Auth routes (`/login`, `/register`) render children bare. Everything else renders the rail, the current zone's pane and a content column. Account things — email, change password, log out — live behind the avatar at the foot of the rail (`components/shell/account-menu.tsx`), where they cannot be mistaken for navigation.

### The LabDog kit (`components/ld`)

Dense, token-driven primitives ported from the design prototype: `PageHead`, `Panel`, `Tabs`, `Seg`, `Filter`, `Table` (a CSS-grid table with table roles, sort, select, sticky head), `Tag`, `Dot`, `Status`, `Provenance`, `Spark`, `Meter`, `StatusBar`, `Modal`, `Empty`, `Kbd`. Buttons on these screens are the `.btn` / `.btn-sm` / `.btn-primary` / `.btn-ghost` / `.btn-danger` classes from `globals.css`; inputs are `.inp`. `components/ui` (shadcn) remains for dialogs, forms and legacy pages.

Shared vocabulary: `lib/modules.ts` is the one registry of the eight modules and their several spellings (route segment, host tab, `ModuleCounts` key, sync `module_filter` name); `lib/fleet.ts` holds the status vocabulary and age helpers; `lib/activity.ts` merges sync jobs and action runs into one stream; `lib/pending.ts` builds the Pending queue's lanes.

---

## Page Structure

Every dashboard page follows this pattern:

```tsx
"use client"

// Imports: React hooks, TanStack Query, UI components, apiFetch, types

export default function PageName() {
  // 1. Auth (if needed): const { user } = useAuth()
  // 2. Query client: const queryClient = useQueryClient()
  // 3. State: dialogs, form fields, errors, loading
  // 4. Queries: useQuery<Type>({ queryKey: [...], queryFn: () => apiFetch<Type>("/api/...") })
  // 5. Handlers: async functions for CRUD operations
  // 6. Render

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Page Title</h1>
          <p className="text-slate-400 text-sm mt-1">Description text</p>
        </div>
        <Button>Action</Button>   {/* or Dialog with DialogTrigger wrapping Button */}
      </div>

      {/* Loading state */}
      {isLoading && <div className="text-slate-400 py-8 text-center">Loading...</div>}

      {/* Error state */}
      {error && <div className="text-red-400 py-8 text-center">Failed to load data</div>}

      {/* Empty state */}
      {!isLoading && !error && data?.length === 0 && (
        <div className="text-slate-400 py-8 text-center">No items found.</div>
      )}

      {/* Data table */}
      {!isLoading && !error && data && data.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>...</Table>
        </div>
      )}

      {/* Dialogs (controlled, at root level) */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>...</Dialog>
    </div>
  )
}
```

---

## Component Patterns

### Tables

Always wrapped in a styled container:

```tsx
<div className="rounded-lg border border-slate-700 bg-slate-900">
  <Table>
    <TableHeader>
      <TableRow className="border-slate-700">
        <TableHead>Column</TableHead>
      </TableRow>
    </TableHeader>
    <TableBody>
      {items.map((item) => (
        <TableRow key={item.id} className="border-slate-700">
          <TableCell className="font-medium text-white">{item.name}</TableCell>
          <TableCell className="text-slate-400 text-xs">{item.meta}</TableCell>
          <TableCell>
            <div className="flex gap-1">
              <Button size="sm" variant="ghost">Edit</Button>
              <Button size="sm" variant="destructive">Delete</Button>
            </div>
          </TableCell>
        </TableRow>
      ))}
    </TableBody>
  </Table>
</div>
```

### Dialogs

**CRITICAL**: This project uses `@base-ui/react`, NOT Radix. `DialogTrigger` does **NOT** support the `asChild` prop.

```tsx
// CREATE dialog — with DialogTrigger
<Dialog open={dialogOpen} onOpenChange={(open) => {
  setDialogOpen(open)
  if (!open) resetForm()
}}>
  <DialogTrigger>
    <Button>Create Item</Button>    {/* NO asChild prop */}
  </DialogTrigger>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>Create Item</DialogTitle>
    </DialogHeader>
    <form onSubmit={handleSubmit} className="space-y-4 mt-2">
      <div className="space-y-2">
        <Label htmlFor="field">Field Name</Label>
        <Input id="field" value={value} onChange={(e) => setValue(e.target.value)} required />
      </div>
      {formError && <p className="text-sm text-red-400">{formError}</p>}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
          Cancel
        </Button>
        <Button type="submit" disabled={formLoading}>
          {formLoading ? "Creating..." : "Create"}
        </Button>
      </DialogFooter>
    </form>
  </DialogContent>
</Dialog>

// EDIT/DELETE dialog — controlled, no trigger (opened programmatically)
<Dialog open={editDialogOpen} onOpenChange={(open) => { if (!open) setEditDialogOpen(false) }}>
  <DialogContent>...</DialogContent>
</Dialog>
```

**Button order convention**: Cancel (outline/ghost) on the left, primary action on the right. Use `<DialogFooter>` which handles responsive layout (stacks vertically on mobile with `flex-col-reverse`, side-by-side on desktop with `sm:flex-row sm:justify-end`).

### Forms

Use React Hook Form + Zod for all forms. See the [Form Validation](#form-validation) section for the full pattern.

```tsx
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { itemSchema, type ItemInput } from "@/lib/schemas"

const form = useForm<ItemInput>({
  resolver: zodResolver(itemSchema),
  defaultValues: { name: "", type: "a" },
  mode: "onSubmit",
})

<form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
  {/* Text input */}
  <div className="space-y-2">
    <Label htmlFor="name">Name</Label>
    <Input id="name" {...form.register("name")} />
    {form.formState.errors.name && (
      <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>
    )}
  </div>

  {/* Native select (no shadcn Select component used) */}
  <div className="space-y-2">
    <Label htmlFor="type">Type</Label>
    <select
      id="type"
      {...form.register("type")}
      className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
    >
      <option value="a">Option A</option>
      <option value="b">Option B</option>
    </select>
  </div>

  {/* Checkbox */}
  <div className="flex items-center gap-2">
    <input id="flag" type="checkbox" {...form.register("flag")} className="rounded border-input" />
    <Label htmlFor="flag">Enable feature</Label>
  </div>

  {/* Group checkboxes (e.g., host-to-group assignment) — managed via useState, not RHF */}
  <div className="space-y-2">
    <Label>Groups</Label>
    <div className="space-y-2 rounded-lg border border-input p-3 dark:bg-input/10">
      {groups.map((g) => (
        <label key={g.id} className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={selected.includes(g.id)} onChange={() => toggle(g.id)} className="rounded border-input" />
          <span className="text-sm text-foreground">{g.name}</span>
        </label>
      ))}
    </div>
  </div>

  {/* Buttons */}
  <DialogFooter>
    <Button type="button" variant="outline" onClick={close}>Cancel</Button>
    <Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving..." : "Save"}</Button>
  </DialogFooter>
</form>
```

### Badges

Import from `@/components/ui/badge`. For status badges, use the custom components from `@/components/status-badge`:

```tsx
// Status badges (predefined color mapping)
<SyncStatusBadge status={host.sync_status} />
<FirewallBadge backend={host.firewall_backend} />
<GitOpsStatusBadge status={group.gitops_status} />

// Inline badges (manual color)
<Badge className="bg-green-600 text-white">Active</Badge>
<Badge className="bg-red-600 text-white">Inactive</Badge>
<Badge className="bg-purple-600 text-white">Superuser</Badge>
<Badge variant="outline">metadata</Badge>

// Empty / disabled state
<span className="text-slate-500">—</span>
```

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
  refetchInterval: 10000,                         // Optional: polling (dashboard only)
})

// Mutation pattern — use useApiMutation from @/lib/mutations
import { useApiMutation } from "@/lib/mutations"

const createMutation = useApiMutation({
  mutationFn: (data: ItemInput) => apiFetch("/api/...", { method: "POST", body: JSON.stringify(data) }),
  invalidateKeys: [["resource-name"]],
  successMessage: "Item created",
  onSuccess: () => {
    setDialogOpen(false)
    form.reset()
  },
})

// Usage: createMutation.mutate(formData)
// Loading: createMutation.isPending
// Error: createMutation.error

// Legacy inline pattern (still valid for complex flows with multiple side effects)
const queryClient = useQueryClient()

async function handleCreate(e: React.FormEvent) {
  e.preventDefault()
  setFormError(null)
  setFormLoading(true)
  try {
    await apiFetch("/api/...", { method: "POST", body: JSON.stringify(data) })
    await queryClient.invalidateQueries({ queryKey: ["resource-name"] })
    setDialogOpen(false)
    resetForm()
  } catch (err) {
    setFormError(err instanceof Error ? err.message : "Failed to create")
  } finally {
    setFormLoading(false)
  }
}
```

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

---

## Auth Patterns

```tsx
import { useAuth } from "@/lib/auth"

const { user, loading, logout } = useAuth()

// Superuser gate (render-level, NOT redirect)
if (loading) return <div className="text-slate-400 py-8 text-center">Loading...</div>
if (!user?.is_superuser) {
  return (
    <div className="text-center py-12">
      <p className="text-slate-400">Access denied. Only administrators can manage users.</p>
      <Link href="/dashboard" className="text-blue-400 hover:underline text-sm mt-2 inline-block">
        Back to Dashboard
      </Link>
    </div>
  )
}
```

---

## File Conventions

| Path | Purpose |
|------|---------|
| `app/(dashboard)/*/page.tsx` | Dashboard pages (inside the shell) |
| `app/(auth)/*/page.tsx` | Auth pages (no shell, centered card) |
| `components/ui/*.tsx` | shadcn/ui primitives (do not modify) |
| `components/*.tsx` | Custom app components (app-shell, group-editor, status-badge, rule-dialog); `components/shell/*` the rail, pane and palette; `components/ld/*` the LabDog kit |
| `lib/api.ts` | API client (`apiFetch`, `API_BASE`) |
| `lib/auth.ts` | Auth context and `useAuth()` hook |
| `lib/types.ts` | All TypeScript interfaces for API responses |
| `lib/utils.ts` | Tailwind `cn()` helper |

### Page file rules

- Always start with `"use client"` directive
- All page logic in a single file (no splitting into sub-components unless shared)
- Shared components go in `components/` (e.g., `rule-dialog.tsx`, `status-badge.tsx`)

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

Use `TableSkeleton` and `CardSkeleton` from `@/components/ui/skeleton`. Use `useDelayedLoading` from `@/lib/utils` to prevent flicker on fast loads (200ms delay).

```tsx
import { TableSkeleton, CardSkeleton } from "@/components/ui/skeleton"
import { useDelayedLoading } from "@/lib/utils"

const showLoading = useDelayedLoading(isLoading)
{showLoading && <TableSkeleton rows={5} columns={4} />}
```

---

## Confirmation Dialogs

Use `ConfirmDialog` from `@/components/ui/confirm-dialog` instead of `window.confirm()`.

- **Destructive actions** (delete, disable): `variant="destructive"` (red button)
- **Default actions**: `variant="default"` (primary button)

```tsx
import { ConfirmDialog } from "@/components/ui/confirm-dialog"

const [confirmState, setConfirmState] = useState<{
  open: boolean; title: string; description: string; action: () => void; loading?: boolean
} | null>(null)

// Trigger:
setConfirmState({ open: true, title: "Delete Key", description: "Cannot be undone.", action: handleDelete })

// Render:
{confirmState && (
  <ConfirmDialog
    open={confirmState.open}
    onOpenChange={(open) => !open && setConfirmState(null)}
    title={confirmState.title}
    description={confirmState.description}
    variant="destructive"
    loading={confirmState.loading}
    onConfirm={confirmState.action}
  />
)}
```

---

## Form Validation

Use React Hook Form + Zod. Schemas are in `@/lib/schemas`.

- **Validation timing**: `mode: "onSubmit"` (errors show only after submit attempt)
- **Error display**: Inline below each field in `text-sm text-red-400`
- **Edit forms**: `form.reset(existingData)` when dialog opens

```tsx
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { groupSchema, type GroupInput } from "@/lib/schemas"

const form = useForm<GroupInput>({
  resolver: zodResolver(groupSchema),
  defaultValues: { name: "", priority: 1 },
  mode: "onSubmit",
})

// Register field:
<Input {...form.register("name")} />
{form.formState.errors.name && (
  <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>
)}

// Submit:
const onSubmit = form.handleSubmit(async (data) => { ... })
<form onSubmit={onSubmit}>
```

---

## Error Boundaries

`app/(dashboard)/error.tsx` catches errors in dashboard pages. Shows AlertTriangle icon, error message, "Try Again" and "Go to Dashboard" buttons.

`app/global-error.tsx` catches root-level errors. Uses inline styles (no Tailwind dependency).

---

## Breadcrumbs

Use `Breadcrumb` from `@/components/ui/breadcrumb`. Place above the page `<h1>`.

```tsx
import { Breadcrumb } from "@/components/ui/breadcrumb"
<Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group.name }]} />
```

Maximum depth: 3 levels. Last item has no `href` (current page).

---

## Tooltips

Use `Tooltip` from `@/components/ui/tooltip` for non-obvious form fields. Trigger: `InfoIcon` next to label.

```tsx
import { Tooltip } from "@/components/ui/tooltip"
import { InfoIcon } from "lucide-react"

<div className="flex items-center gap-1.5">
  <Label htmlFor="cidr">Source CIDR</Label>
  <Tooltip content="IP range in CIDR notation, e.g., 10.0.0.0/8">
    <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
  </Tooltip>
</div>
```

Cap: ~15 tooltips total. Only for complex/non-obvious fields.

---

## Command Palette

`components/shell/palette.tsx`, opened with Cmd/Ctrl+K or the rail button. It indexes every destination (zone items and Settings sections), every host, every group, every module × group pair (landing on the group's Config tab), and a handful of verbs (plan a sync, check the fleet for drift, approve pending hosts, start a scan, add a host, new group, open a terminal on a host). Before anything is typed it shows destinations and verbs only. The palette is infrastructure: it is what lets the rail stay at four entries.

---

## Keyboard Shortcuts

- `Cmd/Ctrl+K` — open command palette
- `[` — toggle the contextual pane
- `t` — toggle dark/light theme
- `Escape` — close any open dialog or command palette (handled natively by base-ui)

The single-key shortcuts never fire while an input, textarea, select or contenteditable has focus.

---

## Mobile Responsive

Below 1180px the pane overlays the content (opened from the `›` strip, closes on navigation). Below 640px the rail moves to the bottom edge as five labelled tabs, a slim header carries the zone name, search and theme toggle, and there is no pane. CSS transitions only (no Framer Motion).

---

## Bulk Actions

Checkbox + "Delete Selected" toolbar on groups, hosts, ssh-keys list pages. Sequential single-item API calls (no batch endpoint). Partial failure toast: "Deleted {success} of {total}. {failed} failed."

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
| shadcn Select | Native `<select>` elements used currently. Migration to styled Select component planned. |
| `DialogTrigger asChild` | Not supported by base-ui. Wrap children directly. |
| System theme following | Dark is the default and light is an explicit choice (`t` or the rail toggle); there is no `prefers-color-scheme` follow. |
| Framer Motion | No animation library. CSS transitions only. |
| Separate `/profile` page | Password change lives in the account menu at the foot of the rail. |
| Pagination | Tables show all data. LabDog manages tens/hundreds of items, not thousands. |
