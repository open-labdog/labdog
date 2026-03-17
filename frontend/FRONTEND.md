# Barricade Frontend — Design & Pattern Reference

This document defines the frontend conventions for Barricade. All new pages, components, and modifications must follow these patterns.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router) |
| UI Components | shadcn/ui (base-ui variant, **NOT Radix**) |
| Styling | Tailwind CSS v4 (dark mode only) |
| Data Fetching | TanStack Query (`@tanstack/react-query`) |
| API Client | `apiFetch()` from `lib/api.ts` |
| Auth | `useAuth()` context from `lib/auth.ts` |
| Icons | Lucide React |
| Drag & Drop | `@dnd-kit/core` + `@dnd-kit/sortable` |

---

## Typography

| Role | Font | CSS Variable | Weights |
|------|------|-------------|---------|
| Body / UI | **DM Sans** (Google Fonts) | `--font-sans` | 400, 500, 600, 700 |
| Code / Mono | **JetBrains Mono** (Google Fonts) | `--font-mono` | 400, 500 |

Loaded in `app/layout.tsx` via `next/font/google`. Applied to `<body>` via `font-sans` class.

### Usage

- Page titles: `text-2xl font-bold text-white`
- Page descriptions: `text-slate-400 text-sm mt-1`
- Table cell text: `text-slate-300 text-xs`
- Monospace values (IPs, ports, CIDRs): `font-mono text-slate-300 text-xs`
- Muted / secondary: `text-slate-400`
- Links: `text-blue-400 hover:underline` or `underline underline-offset-4 hover:text-primary`

---

## Color Palette

Dark mode only (`<html className="dark">`). The palette is monochromatic slate with colored accents for semantics.

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
| Cards, tables, sidebar | `border-slate-700` |
| Table rows | `border-slate-700` |
| Form inputs | `border-slate-700` or `border-input` |
| Sidebar dividers | `border-slate-700` |

### Text

| Role | Class |
|------|-------|
| Primary (headings, names) | `text-white` |
| Secondary (descriptions, metadata) | `text-slate-400` |
| Table values | `text-slate-300` |
| Disabled / muted | `text-slate-500` |
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
app/layout.tsx          — Font loading, Providers, AppShell
├── components/app-shell.tsx — Conditionally renders sidebar (hidden on /login, /register)
├── components/sidebar.tsx   — Navigation, user menu, logout, password change
└── app/(dashboard)/...      — All dashboard pages (with sidebar)
    app/(auth)/...           — Login/register pages (no sidebar, centered card layout)
```

### AppShell Pattern

`components/app-shell.tsx` checks the current pathname. Auth routes (`/login`, `/register`) render children without the sidebar. All other routes render the standard `flex h-screen` layout with sidebar + scrollable main area.

### Sidebar

- Width: `w-64`
- Nav items are conditionally rendered (e.g., "Users" only for `user?.is_superuser`)
- Active state uses `startsWith()` for parent matching (except `/dashboard` which uses exact match)
- User email + logout + password change pinned to bottom via `mt-auto`

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
      <div className="flex gap-3 pt-2">
        <Button type="submit" disabled={formLoading}>
          {formLoading ? "Creating..." : "Create"}
        </Button>
        <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  </DialogContent>
</Dialog>

// EDIT/DELETE dialog — controlled, no trigger (opened programmatically)
<Dialog open={editDialogOpen} onOpenChange={(open) => { if (!open) setEditDialogOpen(false) }}>
  <DialogContent>...</DialogContent>
</Dialog>
```

### Forms

```tsx
<form onSubmit={handleSubmit} className="space-y-4">
  {/* Text input */}
  <div className="space-y-2">
    <Label htmlFor="name">Name</Label>
    <Input id="name" value={val} onChange={(e) => setVal(e.target.value)} required />
  </div>

  {/* Native select (no shadcn Select component used) */}
  <div className="space-y-2">
    <Label htmlFor="type">Type</Label>
    <select
      id="type"
      value={type}
      onChange={(e) => setType(e.target.value)}
      className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
    >
      <option value="a">Option A</option>
      <option value="b">Option B</option>
    </select>
  </div>

  {/* Checkbox */}
  <div className="flex items-center gap-2">
    <input id="flag" type="checkbox" checked={flag} onChange={(e) => setFlag(e.target.checked)} className="rounded border-input" />
    <Label htmlFor="flag">Enable feature</Label>
  </div>

  {/* Group checkboxes (e.g., host-to-group assignment) */}
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

  {/* Error */}
  {error && <p className="text-sm text-red-400">{error}</p>}

  {/* Buttons */}
  <div className="flex gap-3 pt-2">
    <Button type="submit" disabled={loading}>{loading ? "Saving..." : "Save"}</Button>
    <Button type="button" variant="outline" onClick={close}>Cancel</Button>
  </div>
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

// Mutation pattern (inside handlers, not useMutation)
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
| `app/(dashboard)/*/page.tsx` | Dashboard pages (with sidebar) |
| `app/(auth)/*/page.tsx` | Auth pages (no sidebar, centered card) |
| `components/ui/*.tsx` | shadcn/ui primitives (do not modify) |
| `components/*.tsx` | Custom app components (sidebar, status-badge, rule-dialog, app-shell) |
| `lib/api.ts` | API client (`apiFetch`, `API_BASE`) |
| `lib/auth.ts` | Auth context and `useAuth()` hook |
| `lib/types.ts` | All TypeScript interfaces for API responses |
| `lib/utils.ts` | Tailwind `cn()` helper |

### Page file rules

- Always start with `"use client"` directive
- All page logic in a single file (no splitting into sub-components unless shared)
- Shared components go in `components/` (e.g., `rule-dialog.tsx`, `status-badge.tsx`)

---

## Things NOT Used (Intentionally)

| What | Why |
|------|-----|
| Toast notifications | Not implemented. Errors show inline in dialogs/forms. |
| Loading skeletons | Text indicators used instead ("Loading..."). |
| `useMutation` | Mutations done via inline async handlers with try/catch. |
| shadcn Select | Native `<select>` elements used everywhere. |
| `DialogTrigger asChild` | Not supported by base-ui. Wrap children directly. |
| Dark/light toggle | Dark mode only, hardcoded `className="dark"` on `<html>`. |
| Framer Motion | No animation library. CSS transitions only. |
| Separate `/profile` page | Password change lives in sidebar dialog. |
| Pagination | Tables show all data. Barricade manages tens/hundreds of items, not thousands. |
