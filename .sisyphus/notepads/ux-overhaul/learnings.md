# UX Overhaul — Learnings

## Stack Facts
- Next.js 16 App Router, React 19, shadcn/ui (base-ui variant NOT Radix)
- Tailwind CSS v4, dark mode only (`<html className="dark">`)
- TanStack Query v5, no useMutation usage anywhere yet
- `@base-ui/react` ^1.3.0 — DialogTrigger does NOT support `asChild` prop
- Auth: httpOnly cookie JWT, auth pages use `fetch()` with URL-encoded form data (NOT apiFetch)
- All dashboard pages: `"use client"` directive, single-file pattern
- No `(dashboard)/layout.tsx` exists yet — needed for error boundaries

## Key Files
- `frontend/components/ui/dialog.tsx` — base-ui Dialog pattern to follow
- `frontend/components/sidebar.tsx` — nav items source for command palette
- `frontend/components/app-shell.tsx` — shell-level components go here
- `frontend/app/providers.tsx` — root providers (Toaster already added)
- `frontend/lib/types.ts` — all TypeScript interfaces
- `frontend/lib/api.ts` — apiFetch client
- `frontend/FRONTEND.md` — conventions doc

## Guardrails
- NO Radix primitives
- NO framer-motion
- NO backend changes
- NO RHF on auth pages (login/register)
- NO server-side search
- cmdk: navigation-only, verify base-ui compat first
- Zod schemas: frontend-only, incremental per-form

## base-ui Tooltip API (confirmed working)
- Import: `import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip"`
- Sub-components: Root, Trigger, Portal, Positioner, Popup, Arrow
- `Trigger` uses `render={<span />}` prop pattern (same as Dialog.Close)
- `delay` prop goes on `Trigger` (not Root)

## Wave 1 COMPLETE (2026-03-19) — All TypeScript clean
- T1: sonner@2.0.7, cmdk@1.1.1, react-hook-form@7.71.2, @hookform/resolvers@5.2.2, zod@4.3.6
- T2: ConfirmDialog (confirm-dialog.tsx) — uses Dialog wrappers, Loader2Icon spinner
- T3: Sonner toast (providers.tsx + lib/toast.ts) — position bottom-right, dark, richColors
- T4: Skeleton/TableSkeleton/CardSkeleton (skeleton.tsx) — animate-pulse bg-slate-800
- T5: Breadcrumb (breadcrumb.tsx) — ChevronRight separator, Next.js Link
- T6: Tooltip (tooltip.tsx) — base-ui Tooltip, 200ms delay, dark theme

## Wave 2 Next (T7-T11)
- T7: Dashboard layout + error boundaries — create (dashboard)/layout.tsx, error.tsx, global-error.tsx
- T8: cmdk command palette — verify base-ui compat first, then build component
- T9: Responsive mobile sidebar — hamburger + slide-over CSS transition
- T10: Breadcrumbs on all pages — use Breadcrumb component from T5
- T11: Skeleton states on all pages — replace "Loading..." text with TableSkeleton/CardSkeleton
  - Need to add `useDelayedLoading(isLoading, 200)` hook to lib/utils.ts

## rtk commands
- `rtk tsc --noEmit` = TypeScript check (bunx not in PATH)
- `rtk npm run build` = Next.js build
