# UX Comprehensive Overhaul

## TL;DR

> **Quick Summary**: Replace all browser-native dialogs, add toast notifications, loading skeletons, command palette, mobile navigation, form validation (RHF + Zod), search/filter, bulk actions, and useMutation migration across the entire Barricade frontend.
>
> **Deliverables**:
> - ConfirmDialog component replacing all `confirm()`/`alert()` calls
> - Sonner toast notification system
> - Skeleton loading states for all pages
> - Breadcrumb navigation on all pages
> - Tooltip component for complex form fields
> - Error boundaries (error.tsx, global-error.tsx)
> - cmdk command palette (navigation-only)
> - Responsive mobile sidebar (hamburger → collapsible sheet)
> - Client-side search/filter on all list pages
> - React Hook Form + Zod for all dashboard forms
> - useMutation wrapper + migration for all mutation patterns
> - Optimistic updates for simple toggle/delete operations
> - Bulk actions (checkbox + delete selected) on list pages
> - Custom hooks for complex page state
> - Keyboard shortcuts (Cmd+K, Escape)
> - Updated FRONTEND.md conventions
> - Extended Playwright E2E test suite
>
> **Estimated Effort**: L (1-2 weeks)
> **Parallel Execution**: YES — 6 waves
> **Critical Path**: T1 (deps) → T2/T3 (ConfirmDialog/Toast) → T12-T14 (replacements) → T20 (useMutation) → T21 (optimistic) → F1-F4 (verify)

---

## Context

### Original Request
Comprehensive UX improvement plan for the Barricade application, covering quick wins, polish, and deep features.

### Interview Summary
**Key Discussions**:
- FRONTEND.md intentional omissions (no toasts, no skeletons, etc.) are **open for revision**
- Scope: **Comprehensive overhaul** — all improvement areas
- Test strategy: **Extend Playwright E2E** for new components
- Toast library: **Sonner** (shadcn ecosystem)
- Command palette: **cmdk** (navigation-only, quick-jump to pages)
- Mobile nav: **Collapsible sidebar** (hamburger → slide-over sheet)
- Form validation: **React Hook Form + Zod**

**Research Findings**:
- 10+ instances of browser `confirm()`/`alert()` breaking design consistency
- hosts/[id]/page.tsx has 100+ state variables — worst complexity hotspot
- Zero `useMutation` usage — all mutations are ad-hoc try/catch patterns across 21 files
- No Zod schemas exist — backend uses Pydantic, frontend has no validation
- Auth pages use raw `fetch()` with URL-encoded form data (exempt from RHF migration)
- shadcn/ui uses base-ui variant (NOT Radix) — cmdk uses Radix Dialog internally, needs compatibility check
- No `(dashboard)/layout.tsx` exists — required for route-group-level error boundaries

### Metis Review
**Identified Gaps** (addressed):
- useMutation migration is a massive prerequisite, not a sub-task of "optimistic updates" → split into explicit phase
- Zod schemas need incremental creation per-form, not all-at-once → phased approach
- cmdk + base-ui compatibility risk → T8 includes verification spike
- Auth pages exempt from RHF (URL-encoded form data) → explicitly excluded
- No `(dashboard)/layout.tsx` → T7 creates it as prerequisite for error boundaries
- Bulk actions need sequential single-item API calls (no backend batch endpoints) → scoped accordingly
- Search/filter is client-side only (no backend changes) → explicitly constrained

---

## Work Objectives

### Core Objective
Transform the Barricade frontend from a functional but raw UI into a polished, professional-grade admin dashboard with consistent interaction patterns, proper feedback mechanisms, and modern UX conventions.

### Concrete Deliverables
- 6 new shared components: ConfirmDialog, Skeleton, Breadcrumb, Tooltip, CommandPalette, MobileSidebar
- Sonner toast provider integrated at root level
- Error boundaries at dashboard and global level
- React Hook Form + Zod validation on all dashboard forms
- useMutation wrapper replacing ad-hoc mutation patterns
- Client-side search/filter on 6 list pages
- Bulk actions on 3 list pages
- Updated FRONTEND.md reflecting all new conventions
- Extended Playwright E2E test coverage

### Definition of Done
- [ ] Zero browser `confirm()`/`alert()` calls in codebase (`ast_grep_search` returns 0 matches)
- [ ] All list pages have search/filter input
- [ ] All pages show skeleton loading states (no "Loading..." text)
- [ ] All pages have breadcrumb navigation
- [ ] Mobile sidebar works at `md:` breakpoint (768px)
- [ ] Command palette opens with Cmd/Ctrl+K
- [ ] `bunx next build` passes with zero errors
- [ ] All existing Playwright E2E tests pass
- [ ] All new Playwright E2E tests pass

### Must Have
- Custom ConfirmDialog replacing every browser `confirm()`/`alert()`
- Toast notifications for all mutation success/error feedback
- Loading skeletons on every page
- Error boundaries preventing full-app crashes
- Form validation with inline error messages
- Mobile-responsive sidebar navigation

### Must NOT Have (Guardrails)
- **NO backend API changes** — everything is client-side only
- **NO server-side search/filter** — all search uses client-side `Array.filter()`
- **NO backend batch endpoints** — bulk actions use sequential single-item API calls
- **NO React Hook Form on auth pages** — login/register use URL-encoded form data, exempt from RHF migration
- **NO optimistic updates on sync/plan/apply flows** — only simple toggle/delete operations
- **NO architectural restructuring of hosts/[id] page** — extract hooks from existing structure only
- **NO cmdk actions/mutations** — command palette is navigation-only (page quick-jump)
- **NO additional keyboard shortcuts** beyond Cmd/Ctrl+K (palette) and Escape (close dialogs/palette)
- **NO dark/light mode toggle** — stays dark-mode only
- **NO Framer Motion or animation library** — CSS transitions only
- **NO AI slop**: no excessive comments, no over-abstraction, no generic variable names

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (Playwright E2E)
- **Automated tests**: Tests-after (extend E2E suite in final wave)
- **Framework**: Playwright (existing) for E2E integration tests

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Frontend/UI**: Use Playwright — navigate, interact, assert DOM, screenshot
- **Components**: Use Bash (`bunx next build`) — verify build passes
- **Type safety**: Use Bash (`bunx tsc --noEmit`) — verify TypeScript compiles

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 6 tasks, all start immediately):
├── T1: Install new dependencies [quick]
├── T2: Create ConfirmDialog component [quick]
├── T3: Create Sonner toast provider + utility [quick]
├── T4: Create Skeleton components (table + card) [quick]
├── T5: Create Breadcrumb component [quick]
└── T6: Create Tooltip component [quick]

Wave 2 (Layout & Navigation — 5 tasks, after Wave 1):
├── T7: Dashboard layout + error boundaries (depends: T4) [unspecified-high]
├── T8: cmdk command palette (depends: T1) [unspecified-high]
├── T9: Responsive mobile sidebar (depends: T1) [visual-engineering]
├── T10: Integrate breadcrumbs across all pages (depends: T5) [quick]
└── T11: Replace loading text with skeletons (depends: T4) [unspecified-high]

Wave 3 (Dialog Replacement + Search — 5 tasks, after T2+T3):
├── T12: Replace confirm()/alert() in sidebar + auth-adjacent [quick]
├── T13: Replace confirm()/alert() in groups + hosts detail pages [unspecified-high]
├── T14: Replace confirm()/alert() in module pages [unspecified-high]
├── T15: Add search/filter to all list pages [visual-engineering]
└── T16: Add tooltips to complex form fields (depends: T6) [quick]

Wave 4 (Form Modernization — 3 tasks, after T1):
├── T17: Create Zod schemas for all entities [quick]
├── T18: Migrate simple forms to RHF + Zod [unspecified-high]
└── T19: Migrate complex forms to RHF + Zod [deep]

Wave 5 (Data Layer + Polish — 5 tasks, after Waves 3+4):
├── T20: Create useMutation wrapper + migrate pages [unspecified-high]
├── T21: Add optimistic updates for toggles + deletes (depends: T20) [deep]
├── T22: Add bulk actions to list pages [visual-engineering]
├── T23: Extract hosts/[id] state into useHostDetail() hook [deep]
└── T24: Keyboard shortcuts (depends: T8) [quick]

Wave 6 (Finalize — 2 tasks, after all):
├── T25: Update FRONTEND.md conventions [writing]
└── T26: Extend Playwright E2E test suite [unspecified-high]

Wave FINAL (Verification — 4 parallel reviews, then user okay):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
→ Present results → Get explicit user okay

Critical Path: T1 → T2/T3 → T12-T14 → T20 → T21 → T25/T26 → F1-F4 → user okay
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 6 (Wave 1)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T8, T9, T17, T18, T19 | 1 |
| T2 | — | T12, T13, T14 | 1 |
| T3 | — | T12, T13, T14 | 1 |
| T4 | — | T7, T11 | 1 |
| T5 | — | T10 | 1 |
| T6 | — | T16 | 1 |
| T7 | T4 | — | 2 |
| T8 | T1 | T24 | 2 |
| T9 | T1 | — | 2 |
| T10 | T5 | — | 2 |
| T11 | T4 | — | 2 |
| T12 | T2, T3 | — | 3 |
| T13 | T2, T3 | — | 3 |
| T14 | T2, T3 | — | 3 |
| T15 | — | — | 3 |
| T16 | T6 | — | 3 |
| T17 | T1 | T18, T19 | 4 |
| T18 | T17 | T20 | 4 |
| T19 | T17 | T20 | 4 |
| T20 | T18 | T21 | 5 |
| T21 | T20 | — | 5 |
| T22 | T2, T3 | — | 5 |
| T23 | — | — | 5 |
| T24 | T8 | — | 5 |
| T25 | all T1-T24 | — | 6 |
| T26 | all T1-T24 | — | 6 |

### Agent Dispatch Summary

- **Wave 1**: **6** — T1-T6 → all `quick`
- **Wave 2**: **5** — T7 → `unspecified-high`, T8 → `unspecified-high`, T9 → `visual-engineering`, T10 → `quick`, T11 → `unspecified-high`
- **Wave 3**: **5** — T12 → `quick`, T13 → `unspecified-high`, T14 → `unspecified-high`, T15 → `visual-engineering`, T16 → `quick`
- **Wave 4**: **3** — T17 → `quick`, T18 → `unspecified-high`, T19 → `deep`
- **Wave 5**: **5** — T20 → `unspecified-high`, T21 → `deep`, T22 → `visual-engineering`, T23 → `deep`, T24 → `quick`
- **Wave 6**: **2** — T25 → `writing`, T26 → `unspecified-high`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Install New Dependencies

  **What to do**:
  - Install: `npm install sonner cmdk react-hook-form @hookform/resolvers zod`
  - Verify all packages resolve correctly in `package.json`
  - Run `bunx tsc --noEmit` to verify no type conflicts

  **Must NOT do**:
  - Install any animation libraries (no framer-motion)
  - Install Radix primitives (base-ui is already in use)
  - Modify any existing component code

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T2-T6)
  - **Blocks**: T8, T9, T17, T18, T19
  - **Blocked By**: None

  **References**:
  - `frontend/package.json` — Add new dependencies here
  - `frontend/tsconfig.json` — Verify TypeScript compat

  **Acceptance Criteria**:

  ```
  Scenario: Dependencies install and build succeeds
    Tool: Bash
    Steps:
      1. Run `npm install sonner cmdk react-hook-form @hookform/resolvers zod` in frontend/
      2. Run `bunx tsc --noEmit` in frontend/
      3. Run `bunx next build` in frontend/
    Expected Result: All commands exit 0. No type errors. Build succeeds.
    Evidence: .sisyphus/evidence/task-1-deps-install.txt
  ```

  **Commit**: YES
  - Message: `chore(frontend): add sonner, cmdk, react-hook-form, zod dependencies`
  - Files: `frontend/package.json`, `frontend/package-lock.json`

- [x] 2. Create ConfirmDialog Component

  **What to do**:
  - Create `frontend/components/ui/confirm-dialog.tsx`
  - Build on existing `Dialog` component from `components/ui/dialog.tsx`
  - Props: `open`, `onOpenChange`, `title`, `description`, `confirmLabel` (default: "Confirm"), `cancelLabel` (default: "Cancel"), `onConfirm`, `variant` ("default" | "destructive"), `loading`
  - Destructive variant: red confirm button
  - Default variant: primary confirm button
  - Show loading state on confirm button while action executes
  - Follow existing Dialog pattern — uses `@base-ui/react` (NOT Radix), no `asChild` prop

  **Must NOT do**:
  - Use Radix Dialog primitives
  - Add animation library
  - Use `asChild` prop on DialogTrigger

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T3-T6)
  - **Blocks**: T12, T13, T14
  - **Blocked By**: None

  **References**:
  - `frontend/components/ui/dialog.tsx` — Base Dialog implementation to build on. Uses `@base-ui/react` Dialog primitives.
  - `frontend/FRONTEND.md:201-240` — Dialog pattern reference (shows controlled dialog pattern, form structure, button layout)
  - `frontend/components/ui/button.tsx` — Button variants including `destructive`

  **Acceptance Criteria**:

  ```
  Scenario: ConfirmDialog renders with destructive variant
    Tool: Bash
    Steps:
      1. Verify file exists: frontend/components/ui/confirm-dialog.tsx
      2. Run `bunx tsc --noEmit` — no type errors
      3. Verify component exports ConfirmDialog with correct props interface
    Expected Result: File exists, types check, exports match spec
    Evidence: .sisyphus/evidence/task-2-confirm-dialog.txt

  Scenario: ConfirmDialog does NOT use Radix or asChild
    Tool: Bash
    Steps:
      1. Search file for "radix" or "asChild" — should return 0 matches
      2. Verify import from "@base-ui/react" or "../ui/dialog"
    Expected Result: Zero Radix references
    Evidence: .sisyphus/evidence/task-2-no-radix.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add ConfirmDialog component`
  - Files: `frontend/components/ui/confirm-dialog.tsx`

- [x] 3. Create Sonner Toast Provider + Utility

  **What to do**:
  - Add `<Toaster />` from sonner to `frontend/app/providers.tsx` (inside the existing Providers component, after QueryClientProvider)
  - Configure Toaster: `position="bottom-right"`, `theme="dark"`, `richColors`, `closeButton`
  - Create `frontend/lib/toast.ts` exporting thin wrappers: `showSuccess(message)`, `showError(message)`, `showInfo(message)` using sonner's `toast` function
  - Success toasts auto-dismiss after 3 seconds
  - Error toasts persist until manually dismissed (`duration: Infinity`)
  - Style Toaster to match slate dark theme (may need `toastOptions` with custom classes)

  **Must NOT do**:
  - Add toast calls to any existing pages yet (that's T12-T14)
  - Use react-hot-toast or any other toast library
  - Modify existing error handling patterns

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1-T2, T4-T6)
  - **Blocks**: T12, T13, T14
  - **Blocked By**: None

  **References**:
  - `frontend/app/providers.tsx` — Add Toaster here. Currently wraps AuthProvider → QueryClientProvider → ThemeProvider. Add Toaster as a sibling inside the outermost fragment.
  - `frontend/FRONTEND.md:44-88` — Color palette reference. Toast styling must match: bg-slate-900 surface, text-white primary, border-slate-700.
  - Sonner docs: https://sonner.emilkowal.dev/ — Configuration options for Toaster component

  **Acceptance Criteria**:

  ```
  Scenario: Toaster renders in the app
    Tool: Bash
    Steps:
      1. Verify `frontend/app/providers.tsx` imports and renders `<Toaster />`
      2. Verify `frontend/lib/toast.ts` exports showSuccess, showError, showInfo
      3. Run `bunx tsc --noEmit` — no type errors
    Expected Result: Provider includes Toaster, utility functions exist, types check
    Evidence: .sisyphus/evidence/task-3-toast-provider.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add Sonner toast provider and utility`
  - Files: `frontend/app/providers.tsx`, `frontend/lib/toast.ts`

- [x] 4. Create Skeleton Loading Components

  **What to do**:
  - Create `frontend/components/ui/skeleton.tsx` with a base `Skeleton` component (animated pulsing block)
  - Style: `animate-pulse bg-slate-800 rounded-md` (matches dark theme)
  - Create composite skeletons in the same file:
    - `TableSkeleton` — props: `rows` (default 5), `columns` (default 4). Renders a table-shaped skeleton matching the existing table wrapper pattern (`rounded-lg border border-slate-700 bg-slate-900`)
    - `CardSkeleton` — props: `lines` (default 3). Renders a card-shaped skeleton matching existing Card component
  - Each skeleton row/cell should have varying widths (60%, 80%, 40%) for visual interest

  **Must NOT do**:
  - Use Framer Motion or any animation library — CSS `animate-pulse` only
  - Create skeletons for every single page layout — just the reusable primitives

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1-T3, T5-T6)
  - **Blocks**: T7, T11
  - **Blocked By**: None

  **References**:
  - `frontend/FRONTEND.md:173-199` — Table wrapper pattern to replicate in skeleton shape: `<div className="rounded-lg border border-slate-700 bg-slate-900"><Table>...</Table></div>`
  - `frontend/components/ui/card.tsx` — Card component structure to match skeleton shape
  - `frontend/components/ui/table.tsx` — Table component structure for TableSkeleton layout

  **Acceptance Criteria**:

  ```
  Scenario: Skeleton components render correctly
    Tool: Bash
    Steps:
      1. Verify file exists: frontend/components/ui/skeleton.tsx
      2. Verify exports: Skeleton, TableSkeleton, CardSkeleton
      3. Run `bunx tsc --noEmit`
      4. Verify Skeleton uses `animate-pulse` class (grep for it)
    Expected Result: All components exist, use CSS animation, types check
    Evidence: .sisyphus/evidence/task-4-skeleton.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add Skeleton loading components`
  - Files: `frontend/components/ui/skeleton.tsx`

- [x] 5. Create Breadcrumb Component

  **What to do**:
  - Create `frontend/components/ui/breadcrumb.tsx`
  - Props: `items: Array<{ label: string, href?: string }>` — last item has no href (current page)
  - Render as horizontal list with `>` separator (chevron icon from Lucide)
  - Links use Next.js `<Link>` component
  - Current page (last item) rendered as plain text in `text-white`, previous items in `text-slate-400 hover:text-white`
  - Maximum depth: 3 levels (e.g., "Groups > Production Servers > Rules")

  **Must NOT do**:
  - Auto-generate breadcrumbs from route structure (manual items prop)
  - Add breadcrumbs to pages in this task (that's T10)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1-T4, T6)
  - **Blocks**: T10
  - **Blocked By**: None

  **References**:
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — Has existing manual breadcrumbs pattern (look for "← Back to Groups" link). The new Breadcrumb component replaces this pattern.
  - `frontend/FRONTEND.md:33-38` — Typography classes for text styling: `text-slate-400` for muted items, `text-white` for current

  **Acceptance Criteria**:

  ```
  Scenario: Breadcrumb component renders with correct structure
    Tool: Bash
    Steps:
      1. Verify file exists: frontend/components/ui/breadcrumb.tsx
      2. Verify component accepts `items` prop with `label` and optional `href`
      3. Run `bunx tsc --noEmit`
    Expected Result: Component exists with correct type interface
    Evidence: .sisyphus/evidence/task-5-breadcrumb.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add Breadcrumb navigation component`
  - Files: `frontend/components/ui/breadcrumb.tsx`

- [x] 6. Create Tooltip Component

  **What to do**:
  - Create `frontend/components/ui/tooltip.tsx` using `@base-ui/react` Tooltip primitives
  - Props: `content: string`, `children: ReactNode`, `side?: "top" | "bottom" | "left" | "right"` (default: "top")
  - Dark theme styling: `bg-slate-800 text-slate-200 border border-slate-700 rounded-md px-3 py-1.5 text-xs shadow-lg`
  - Small arrow pointing to trigger element
  - Delay: 200ms before showing (avoid accidental triggers)

  **Must NOT do**:
  - Use Radix tooltip — use @base-ui/react Tooltip
  - Add tooltips to any forms in this task (that's T16)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1-T5)
  - **Blocks**: T16
  - **Blocked By**: None

  **References**:
  - `frontend/components/ui/dialog.tsx` — Pattern for wrapping @base-ui/react primitives. Use the same import/export style.
  - `frontend/FRONTEND.md:44-88` — Color palette: bg-slate-800 for popover surfaces, border-slate-700 for borders

  **Acceptance Criteria**:

  ```
  Scenario: Tooltip component renders
    Tool: Bash
    Steps:
      1. Verify file exists: frontend/components/ui/tooltip.tsx
      2. Verify imports from @base-ui/react (not radix)
      3. Run `bunx tsc --noEmit`
    Expected Result: Component exists, uses base-ui, types check
    Evidence: .sisyphus/evidence/task-6-tooltip.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add Tooltip component`
  - Files: `frontend/components/ui/tooltip.tsx`

- [x] 7. Add Dashboard Layout + Error Boundaries

  **What to do**:
  - Create `frontend/app/(dashboard)/layout.tsx` — thin wrapper that just renders `{children}`. This is required for Next.js to apply error boundaries at the dashboard route group level.
  - Create `frontend/app/(dashboard)/error.tsx` — client error boundary component. Shows: error message, "Try Again" button (calls `reset()`), "Go to Dashboard" link. Styled with Card component on slate-900 background.
  - Create `frontend/app/global-error.tsx` — root-level error boundary (catches errors in root layout). Minimal HTML with inline styles (can't rely on CSS since layout may have failed). Shows error + "Reload" button.
  - Error boundary UI: centered card with red icon (AlertTriangle from Lucide), error title, error message, action buttons

  **Must NOT do**:
  - Add telemetry or error reporting
  - Add "Report Bug" functionality
  - Modify existing page components

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T8-T11)
  - **Blocks**: None
  - **Blocked By**: T4 (skeleton component for loading states)

  **References**:
  - `frontend/app/layout.tsx` — Root layout structure. The global-error.tsx is a sibling to this file.
  - `frontend/app/(auth)/layout.tsx` — Existing route group layout. The new dashboard layout follows the same pattern.
  - `frontend/components/ui/card.tsx` — Use Card for error boundary UI
  - `frontend/components/ui/button.tsx` — Use Button for "Try Again" and "Go to Dashboard"
  - Next.js docs on error.tsx: Error boundaries must export a default Client Component with `error` and `reset` props.

  **Acceptance Criteria**:

  ```
  Scenario: Error boundaries exist and are correctly structured
    Tool: Bash
    Steps:
      1. Verify files exist: app/(dashboard)/layout.tsx, app/(dashboard)/error.tsx, app/global-error.tsx
      2. Verify error.tsx is a "use client" component with error and reset props
      3. Verify global-error.tsx uses inline styles (no Tailwind dependency)
      4. Run `bunx next build`
    Expected Result: All files exist, build succeeds
    Evidence: .sisyphus/evidence/task-7-error-boundaries.txt
  ```

  **Commit**: YES
  - Message: `feat(frontend): add dashboard layout with error boundaries`
  - Files: `frontend/app/(dashboard)/layout.tsx`, `frontend/app/(dashboard)/error.tsx`, `frontend/app/global-error.tsx`

- [x] 8. Build cmdk Command Palette

  **What to do**:
  - **First: Compatibility spike** — verify cmdk works alongside @base-ui/react. cmdk uses Radix Dialog internally. Create a minimal test: render cmdk `<Command.Dialog>` alongside an existing base-ui Dialog. If conflicts exist, build a custom palette using base-ui Dialog + a filterable list instead.
  - Create `frontend/components/command-palette.tsx`
  - Navigation-only: items are sidebar navigation links (Dashboard, Groups, Hosts, SSH Keys, Git Repos, Audit Log, Users)
  - Open with Cmd/Ctrl+K keyboard shortcut (register `useEffect` with `keydown` listener)
  - Search input filters items by name
  - Selecting an item navigates to that page (use Next.js `router.push()`)
  - Close on Escape or item selection
  - Style: dark theme matching slate palette, monospace font for shortcuts display
  - Add `<CommandPalette />` to `frontend/components/app-shell.tsx` (rendered once at shell level)

  **Must NOT do**:
  - Add search across entities (hosts, groups, rules) — navigation-only
  - Execute any mutations from the palette
  - Add sub-commands or nested menus
  - Force cmdk if base-ui conflict exists — build custom alternative

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T9-T11)
  - **Blocks**: T24
  - **Blocked By**: T1 (cmdk dependency)

  **References**:
  - `frontend/components/sidebar.tsx` — Navigation items list. Extract the same nav items for command palette entries. Look at the nav links array for labels and hrefs.
  - `frontend/components/app-shell.tsx` — Render CommandPalette here as a sibling to the sidebar + main content area.
  - `frontend/components/ui/dialog.tsx` — Fallback: if cmdk conflicts with base-ui, build custom palette using this Dialog.
  - cmdk docs: https://cmdk.paco.me/ — API reference for Command, Command.Dialog, Command.Input, Command.List, Command.Item

  **Acceptance Criteria**:

  ```
  Scenario: Command palette opens and navigates
    Tool: Playwright
    Steps:
      1. Navigate to /dashboard
      2. Press Cmd+K (or Ctrl+K on Linux)
      3. Assert command palette dialog is visible
      4. Type "hosts" in search input
      5. Assert filtered results show "Hosts" item
      6. Click "Hosts" item
      7. Assert URL is now /hosts
    Expected Result: Palette opens, filters, navigates correctly
    Evidence: .sisyphus/evidence/task-8-command-palette.png

  Scenario: Command palette closes on Escape
    Tool: Playwright
    Steps:
      1. Press Cmd+K to open palette
      2. Press Escape
      3. Assert palette is no longer visible
    Expected Result: Palette closes
    Evidence: .sisyphus/evidence/task-8-palette-escape.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add command palette with cmdk`
  - Files: `frontend/components/command-palette.tsx`, `frontend/components/app-shell.tsx`

- [x] 9. Responsive Mobile Sidebar

  **What to do**:
  - Modify `frontend/components/app-shell.tsx`:
    - At `md:` breakpoint (768px) and above: show sidebar as-is (current behavior)
    - Below `md:`: hide sidebar, show hamburger button (Menu icon from Lucide) in a top bar
    - Hamburger opens sidebar as a slide-over sheet from the left (use absolute/fixed positioning + transform transition)
    - Overlay behind sheet (dark semi-transparent backdrop) — click overlay to close
    - Sheet closes on navigation (when user clicks a nav link)
  - Modify `frontend/components/sidebar.tsx`:
    - Add `onNavigation` callback prop (called when a nav link is clicked, so AppShell can close the sheet)
    - Make sidebar width responsive: full width on mobile sheet, `w-64` on desktop
  - Top bar on mobile: hamburger left, "Barricade" text center, user avatar/icon right

  **Must NOT do**:
  - Use Framer Motion for animations — CSS `transition-transform` only
  - Use a Sheet component library — build with plain CSS transitions
  - Change sidebar content or navigation items

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T8, T10, T11)
  - **Blocks**: None
  - **Blocked By**: T1

  **References**:
  - `frontend/components/app-shell.tsx` — Current layout: `flex h-screen` with Sidebar + main. Modify to conditionally render based on viewport.
  - `frontend/components/sidebar.tsx` — Current sidebar: fixed `w-64`, slate-950 bg, nav links, user menu at bottom.
  - `frontend/FRONTEND.md:93-111` — AppShell and Sidebar architecture. Sidebar checks pathname for auth routes.

  **Acceptance Criteria**:

  ```
  Scenario: Mobile sidebar opens and closes
    Tool: Playwright
    Steps:
      1. Set viewport to 375x812 (iPhone)
      2. Navigate to /dashboard
      3. Assert sidebar is NOT visible
      4. Assert hamburger button is visible
      5. Click hamburger button
      6. Assert sidebar is visible as slide-over
      7. Click "Groups" nav link
      8. Assert URL is /groups AND sidebar is closed
    Expected Result: Sidebar toggles correctly on mobile
    Evidence: .sisyphus/evidence/task-9-mobile-sidebar.png

  Scenario: Desktop sidebar remains static
    Tool: Playwright
    Steps:
      1. Set viewport to 1440x900 (desktop)
      2. Navigate to /dashboard
      3. Assert sidebar is visible (w-64)
      4. Assert hamburger button is NOT visible
    Expected Result: No change to desktop behavior
    Evidence: .sisyphus/evidence/task-9-desktop-sidebar.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add responsive mobile sidebar`
  - Files: `frontend/components/app-shell.tsx`, `frontend/components/sidebar.tsx`

- [ ] 10. Integrate Breadcrumbs Across All Pages

  **What to do**:
  - Add `<Breadcrumb>` component to every dashboard page's header section
  - Replace any existing manual "← Back to X" links with proper breadcrumbs
  - Breadcrumb structures per page:
    - `/dashboard`: `Dashboard` (single item, no link — just context)
    - `/groups`: `Groups`
    - `/groups/new`: `Groups > New Group`
    - `/groups/[id]`: `Groups > {group.name}`
    - `/groups/[id]/rules`: `Groups > {group.name} > Rules`
    - `/groups/[id]/services`: `Groups > {group.name} > Services`
    - `/groups/[id]/hosts-entries`: `Groups > {group.name} > Hosts Entries`
    - `/groups/[id]/users`: `Groups > {group.name} > Users`
    - `/groups/[id]/cron-jobs`: `Groups > {group.name} > Cron Jobs`
    - `/groups/[id]/packages`: `Groups > {group.name} > Packages`
    - `/groups/[id]/sync`: `Groups > {group.name} > Sync`
    - `/hosts`: `Hosts`
    - `/hosts/new`: `Hosts > New Host`
    - `/hosts/discover`: `Hosts > Discover`
    - `/hosts/[id]`: `Hosts > {host.hostname}`
    - `/ssh-keys`: `SSH Keys`
    - `/users`: `Users`
    - `/git-repos`: `Git Repos`
    - `/audit`: `Audit Log`
  - Place breadcrumbs above the page title (`<h1>`)

  **Must NOT do**:
  - Modify page logic or data fetching
  - Remove existing page titles or descriptions

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7-T9, T11)
  - **Blocks**: None
  - **Blocked By**: T5 (Breadcrumb component)

  **References**:
  - `frontend/components/ui/breadcrumb.tsx` — The Breadcrumb component from T5
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — Has existing "← Back to Groups" breadcrumb pattern to replace
  - `frontend/FRONTEND.md:116-164` — Page structure pattern: breadcrumb goes inside `<div className="space-y-6">`, before the header flex row

  **Acceptance Criteria**:

  ```
  Scenario: Breadcrumbs render on nested pages
    Tool: Playwright
    Steps:
      1. Navigate to a group's rules page (/groups/{id}/rules)
      2. Assert breadcrumb shows "Groups > {name} > Rules"
      3. Click "Groups" in breadcrumb
      4. Assert URL is /groups
    Expected Result: Breadcrumbs display correct hierarchy and navigate
    Evidence: .sisyphus/evidence/task-10-breadcrumbs.png
  ```

  **Commit**: YES
  - Message: `refactor(frontend): add breadcrumbs to all dashboard pages`
  - Files: All `page.tsx` files under `frontend/app/(dashboard)/`

- [ ] 11. Replace Loading Text with Skeleton States

  **What to do**:
  - In every dashboard page, replace `{isLoading && <div className="text-slate-400 py-8 text-center">Loading...</div>}` with the appropriate Skeleton component
  - List pages (groups, hosts, ssh-keys, users, git-repos, audit): use `<TableSkeleton rows={5} columns={N} />` where N matches that page's column count
  - Detail pages (group/[id], host/[id]): use `<CardSkeleton />` for info cards + `<TableSkeleton />` for data tables
  - Add a 200ms delay before showing skeleton (prevents flicker on fast loads) — use a `useDelayedLoading(isLoading, 200)` custom hook in `lib/utils.ts`
  - The skeleton wrapper should maintain the same overall page height to prevent layout shift

  **Must NOT do**:
  - Change data fetching logic
  - Add loading.tsx files (keep loading in-page, not route-level)
  - Use animation libraries

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7-T10)
  - **Blocks**: None
  - **Blocked By**: T4 (Skeleton components)

  **References**:
  - `frontend/components/ui/skeleton.tsx` — Skeleton, TableSkeleton, CardSkeleton from T4
  - `frontend/FRONTEND.md:142-148` — Current loading state pattern to replace: `{isLoading && <div className="text-slate-400 py-8 text-center">Loading...</div>}`
  - `frontend/app/(dashboard)/groups/page.tsx` — Example list page with loading state to replace
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Example detail page with loading state

  **Acceptance Criteria**:

  ```
  Scenario: Skeleton replaces "Loading..." text
    Tool: Playwright
    Steps:
      1. Navigate to /groups (with network throttling to slow down API)
      2. Assert skeleton table is visible (animated pulse elements)
      3. Assert NO "Loading..." text exists on the page
      4. Wait for data to load
      5. Assert skeleton is replaced by actual data table
    Expected Result: Skeleton shown during load, then replaced by data
    Evidence: .sisyphus/evidence/task-11-skeleton-loading.png

  Scenario: Fast load shows no skeleton flicker
    Tool: Playwright
    Steps:
      1. Navigate to /groups (normal network speed)
      2. If data loads within 200ms, skeleton should NOT appear
    Expected Result: No skeleton flash on fast loads
    Evidence: .sisyphus/evidence/task-11-no-flicker.png
  ```

  **Commit**: YES
  - Message: `refactor(frontend): replace loading text with skeleton states`
  - Files: All `page.tsx` files under `frontend/app/(dashboard)/`, `frontend/lib/utils.ts`

- [ ] 12. Replace Browser Dialogs — Sidebar + Auth-Adjacent

  **What to do**:
  - In `frontend/components/sidebar.tsx`:
    - Replace `confirm("Are you sure you want to log out?")` (if present) with ConfirmDialog
    - Replace any `alert()` calls with `showError()` toast
    - Password change success: replace inline text with `showSuccess("Password updated successfully")`
    - Password change error: replace `alert()` with `showError(message)` toast
  - Audit all shared components for confirm()/alert() usage and replace similarly
  - Add ConfirmDialog state management (open/onConfirm pattern)

  **Must NOT do**:
  - Modify page-level components (that's T13-T14)
  - Change functionality — only replace the dialog mechanism

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T13-T16)
  - **Blocks**: None
  - **Blocked By**: T2 (ConfirmDialog), T3 (Toast)

  **References**:
  - `frontend/components/sidebar.tsx` — Look for `confirm(`, `alert(`, and inline success/error text patterns
  - `frontend/components/ui/confirm-dialog.tsx` — ConfirmDialog from T2
  - `frontend/lib/toast.ts` — showSuccess, showError from T3

  **Acceptance Criteria**:

  ```
  Scenario: Sidebar uses ConfirmDialog instead of browser confirm
    Tool: Bash
    Steps:
      1. Search frontend/components/sidebar.tsx for "confirm(" — should return 0
      2. Search frontend/components/sidebar.tsx for "alert(" — should return 0
      3. Search for "ConfirmDialog" import — should exist
    Expected Result: Zero browser dialog calls, ConfirmDialog imported
    Evidence: .sisyphus/evidence/task-12-sidebar-dialogs.txt
  ```

  **Commit**: YES
  - Message: `refactor(frontend): replace browser dialogs in sidebar`
  - Files: `frontend/components/sidebar.tsx`

- [ ] 13. Replace Browser Dialogs — Groups + Hosts Detail Pages

  **What to do**:
  - In `frontend/app/(dashboard)/groups/[id]/page.tsx`:
    - Replace all `confirm()` calls (delete group, remove host from group, disable GitOps) with ConfirmDialog
    - Replace all `alert()` calls with `showError()` toast
    - Replace success feedback with `showSuccess()` toast
  - In `frontend/app/(dashboard)/hosts/[id]/page.tsx`:
    - Replace all `confirm()` calls (delete host, remove from group, delete entries) with ConfirmDialog
    - Replace all `alert()` calls with `showError()` toast
    - Replace success feedback with `showSuccess()` toast
  - Pattern: Add `const [confirmState, setConfirmState] = useState<{open: boolean, action: () => void, title: string, description: string}>` for managing ConfirmDialog

  **Must NOT do**:
  - Modify data fetching or mutation logic
  - Change what happens after confirmation — only change HOW the confirmation is presented

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12, T14-T16)
  - **Blocks**: None
  - **Blocked By**: T2 (ConfirmDialog), T3 (Toast)

  **References**:
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — Search for `confirm(` and `alert(` to find all instances
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Search for `confirm(` and `alert(` to find all instances
  - `frontend/components/ui/confirm-dialog.tsx` — ConfirmDialog from T2
  - `frontend/lib/toast.ts` — showSuccess, showError from T3

  **Acceptance Criteria**:

  ```
  Scenario: Group detail page uses ConfirmDialog for delete
    Tool: Playwright
    Steps:
      1. Navigate to /groups/{id}
      2. Click "Delete Group" button
      3. Assert ConfirmDialog appears with destructive variant (red confirm button)
      4. Assert dialog title contains "Delete"
      5. Click "Cancel" — assert dialog closes, group still exists
    Expected Result: Custom dialog instead of browser confirm
    Evidence: .sisyphus/evidence/task-13-group-confirm.png

  Scenario: No browser confirm/alert in groups or hosts detail
    Tool: Bash
    Steps:
      1. Search groups/[id]/page.tsx for "confirm(" — 0 matches
      2. Search groups/[id]/page.tsx for "alert(" — 0 matches
      3. Search hosts/[id]/page.tsx for "confirm(" — 0 matches
      4. Search hosts/[id]/page.tsx for "alert(" — 0 matches
    Expected Result: Zero browser dialog calls
    Evidence: .sisyphus/evidence/task-13-no-browser-dialogs.txt
  ```

  **Commit**: YES
  - Message: `refactor(frontend): replace browser dialogs in group and host pages`
  - Files: `frontend/app/(dashboard)/groups/[id]/page.tsx`, `frontend/app/(dashboard)/hosts/[id]/page.tsx`

- [ ] 14. Replace Browser Dialogs — Module Pages

  **What to do**:
  - Replace all `confirm()`/`alert()` calls in the remaining dashboard pages:
    - `frontend/app/(dashboard)/groups/[id]/rules/page.tsx` — delete rule confirmations
    - `frontend/app/(dashboard)/groups/[id]/services/page.tsx` — delete service confirmations
    - `frontend/app/(dashboard)/groups/[id]/hosts-entries/page.tsx` — delete entry confirmations
    - `frontend/app/(dashboard)/groups/[id]/users/page.tsx` — delete user confirmations
    - `frontend/app/(dashboard)/groups/[id]/cron-jobs/page.tsx` — delete cron job confirmations
    - `frontend/app/(dashboard)/groups/[id]/packages/page.tsx` — delete package confirmations
    - `frontend/app/(dashboard)/ssh-keys/page.tsx` — delete SSH key confirmations
    - `frontend/app/(dashboard)/users/page.tsx` — delete admin user confirmations
    - `frontend/app/(dashboard)/git-repos/page.tsx` — delete repo confirmations
  - Apply same pattern as T12-T13: ConfirmDialog for confirms, toast for alerts
  - After completion, run `ast_grep_search` for `confirm($MSG)` and `alert($MSG)` across entire frontend — expect 0 matches

  **Must NOT do**:
  - Touch auth pages (login, register)
  - Modify mutation logic — only swap the dialog/feedback mechanism

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12-T13, T15-T16)
  - **Blocks**: None
  - **Blocked By**: T2 (ConfirmDialog), T3 (Toast)

  **References**:
  - All files listed above — search each for `confirm(` and `alert(` calls
  - `frontend/components/ui/confirm-dialog.tsx` — ConfirmDialog from T2
  - `frontend/lib/toast.ts` — showSuccess, showError from T3
  - T12 and T13 — follow the same replacement pattern established there

  **Acceptance Criteria**:

  ```
  Scenario: Zero browser confirm/alert calls in entire frontend
    Tool: Bash
    Steps:
      1. Run ast_grep_search for pattern `confirm($MSG)` in frontend/ — expect 0
      2. Run ast_grep_search for pattern `alert($MSG)` in frontend/ — expect 0
      3. Exclude node_modules and .next directories
    Expected Result: Zero matches for browser dialogs in source code
    Evidence: .sisyphus/evidence/task-14-zero-browser-dialogs.txt
  ```

  **Commit**: YES
  - Message: `refactor(frontend): replace browser dialogs in module pages`
  - Files: All module page.tsx files listed above

- [x] 15. Add Search/Filter to All List Pages

  **What to do**:
  - Add a search input above the data table on every list page:
    - `/groups` — filter by group name
    - `/hosts` — filter by hostname or IP address
    - `/ssh-keys` — filter by key name
    - `/users` — filter by email
    - `/git-repos` — filter by repo name or URL
    - `/audit` — enhance existing filters (already has some filtering)
  - Search component: text input with Search icon (Lucide), placeholder "Search {items}...", `text-sm` size
  - Client-side filtering: `data.filter(item => item.name.toLowerCase().includes(query.toLowerCase()))`
  - Show filtered count: "Showing X of Y items" below search input
  - Clear button (X icon) when search has value
  - Empty search results: "No results matching '{query}'"
  - Place search input between page header and table, with `mb-4` spacing

  **Must NOT do**:
  - Add server-side search (no backend API changes)
  - Add advanced filter builder or saved filters
  - Add column-specific sorting (keep it simple)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12-T14, T16)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `frontend/app/(dashboard)/groups/page.tsx` — Example list page. Search input goes between header and table.
  - `frontend/app/(dashboard)/audit/page.tsx` — Already has filter inputs. Enhance rather than replace.
  - `frontend/components/ui/input.tsx` — Input component to use for search
  - `frontend/FRONTEND.md:116-164` — Page structure pattern: search goes after header, before loading/data section

  **Acceptance Criteria**:

  ```
  Scenario: Search filters groups list
    Tool: Playwright
    Steps:
      1. Navigate to /groups
      2. Assert search input exists with placeholder "Search groups..."
      3. Type "prod" in search input
      4. Assert only groups with "prod" in name are visible
      5. Assert filter count shows "Showing X of Y groups"
      6. Clear search
      7. Assert all groups visible again
    Expected Result: Client-side filtering works correctly
    Evidence: .sisyphus/evidence/task-15-search-groups.png

  Scenario: Empty search results display
    Tool: Playwright
    Steps:
      1. Navigate to /hosts
      2. Type "zzzznonexistent" in search
      3. Assert "No results matching 'zzzznonexistent'" message
    Expected Result: Friendly empty state for no matches
    Evidence: .sisyphus/evidence/task-15-search-empty.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add search/filter to all list pages`
  - Files: All list page.tsx files (groups, hosts, ssh-keys, users, git-repos, audit)

- [ ] 16. Add Tooltips to Complex Form Fields

  **What to do**:
  - Add Tooltip component to form fields that have non-obvious meaning or format requirements:
    - **Rule dialog** (`rule-dialog.tsx`): Source CIDR ("IP range in CIDR notation, e.g., 10.0.0.0/8"), Destination CIDR, Port Range ("Single port or range, e.g., 80 or 8000-9000")
    - **Host forms** (`hosts/new/page.tsx`, `hosts/[id]/page.tsx`): SSH Port ("Default: 22"), Firewall Backend ("Detected automatically on first connect")
    - **Group forms** (`groups/new/page.tsx`): Priority ("Higher number = higher priority. Rules from higher-priority groups override lower ones")
    - **SSH key upload** (`ssh-keys/page.tsx`): Private Key field ("Encrypted at rest with AES-256-GCM")
    - **Cron jobs** (`cron-jobs/page.tsx`): Cron expression fields ("Standard 5-field cron: minute hour day month weekday")
  - Tooltip trigger: small info icon (Info from Lucide, `w-4 h-4 text-slate-500`) next to label text
  - Cap at 15 tooltips total across the application

  **Must NOT do**:
  - Add tooltips to obvious fields (name, description, email)
  - Add tooltips to read-only display elements (only form inputs)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12-T15)
  - **Blocks**: None
  - **Blocked By**: T6 (Tooltip component)

  **References**:
  - `frontend/components/ui/tooltip.tsx` — Tooltip component from T6
  - `frontend/components/rule-dialog.tsx` — Firewall rule form with CIDR and port fields
  - `frontend/app/(dashboard)/groups/new/page.tsx` — Group creation form with priority field
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — SSH key upload form

  **Acceptance Criteria**:

  ```
  Scenario: Tooltip appears on CIDR field in rule dialog
    Tool: Playwright
    Steps:
      1. Navigate to /groups/{id}/rules
      2. Click "Add Rule" to open rule dialog
      3. Hover over info icon next to "Source CIDR" label
      4. Assert tooltip text contains "CIDR notation"
    Expected Result: Tooltip displays help text
    Evidence: .sisyphus/evidence/task-16-tooltip.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add tooltips to complex form fields`
  - Files: `frontend/components/rule-dialog.tsx`, various page.tsx files with forms

- [ ] 17. Create Zod Validation Schemas

  **What to do**:
  - Create `frontend/lib/schemas.ts` with Zod schemas for all form entities
  - Schemas to create (matching backend Pydantic models):
    - `groupSchema` — name (string, min 1, max 100), description (string, optional), priority (number, int, min 1)
    - `hostSchema` — hostname (string, min 1), ip_address (string, IP format), ssh_port (number, int, min 1, max 65535), ssh_key_id (string, uuid), group_ids (array of strings)
    - `ruleSchema` — action (enum: allow/deny/reject), protocol (enum: tcp/udp/icmp/any), direction (enum: input/output), source_cidr (string, optional, CIDR format), destination_cidr (string, optional, CIDR format), port_start (number, optional, 1-65535), port_end (number, optional, 1-65535), comment (string, optional)
    - `serviceSchema` — service_name (string, min 1), state (enum: running/stopped), enabled (boolean), comment (string, optional)
    - `hostsEntrySchema` — ip_address (string, IP format), hostname (string, min 1), aliases (string, optional, space-separated), comment (string, optional)
    - `sshKeySchema` — name (string, min 1), public_key (string, min 1), private_key (string, min 1)
    - `gitRepoSchema` — name (string, min 1), url (string, url format), branch (string, default "main"), ssh_key_id (string, uuid, optional)
    - `cronJobSchema` — name (string, min 1), minute/hour/day/month/weekday (string, cron field), command (string, min 1), user (string, default "root")
    - `passwordChangeSchema` — current_password (string, min 1), new_password (string, min 8), confirm_password (refine: matches new_password)
  - Export both schemas and inferred types (`z.infer<typeof groupSchema>`)
  - Add CIDR validation regex and IP address validation regex as reusable Zod refinements

  **Must NOT do**:
  - Create schemas for auth (login/register) — those use URL-encoded form data
  - Create schemas for read-only display types — only forms that accept user input
  - Import backend types — schemas are frontend-only

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with T18-T19)
  - **Blocks**: T18, T19
  - **Blocked By**: T1 (zod dependency)

  **References**:
  - `frontend/lib/types.ts` — Existing TypeScript interfaces for API responses. Schemas should match these shapes for create/update forms.
  - `backend/app/schemas/` — Backend Pydantic schemas (reference for validation rules, field constraints, enums)
  - `frontend/components/rule-dialog.tsx` — Current rule form fields to derive ruleSchema from
  - `frontend/app/(dashboard)/groups/new/page.tsx` — Current group form fields

  **Acceptance Criteria**:

  ```
  Scenario: Zod schemas compile and validate correctly
    Tool: Bash
    Steps:
      1. Verify file exists: frontend/lib/schemas.ts
      2. Run `bunx tsc --noEmit` — no type errors
      3. Verify schema exports exist for all entities listed above
    Expected Result: All schemas exist, types check
    Evidence: .sisyphus/evidence/task-17-zod-schemas.txt
  ```

  **Commit**: YES
  - Message: `feat(frontend): add Zod validation schemas for all entities`
  - Files: `frontend/lib/schemas.ts`

- [ ] 18. Migrate Simple Forms to React Hook Form + Zod

  **What to do**:
  - Migrate these simple CRUD forms from manual useState to React Hook Form + Zod:
    - `frontend/app/(dashboard)/groups/new/page.tsx` — create group form
    - `frontend/app/(dashboard)/ssh-keys/page.tsx` — create SSH key dialog form
    - `frontend/app/(dashboard)/git-repos/page.tsx` — create/edit git repo dialog form
    - `frontend/app/(dashboard)/hosts/new/page.tsx` — create host form
    - `frontend/app/(dashboard)/hosts/discover/page.tsx` — host discovery form
  - Pattern for each form:
    ```tsx
    const form = useForm<GroupInput>({
      resolver: zodResolver(groupSchema),
      defaultValues: { name: "", description: "", priority: 1 }
    })
    ```
  - Replace manual `value`/`onChange` with `form.register("fieldName")`
  - Replace manual error state with `form.formState.errors.fieldName?.message`
  - Show validation errors on submit only (not blur): `mode: "onSubmit"`
  - Keep existing mutation pattern (apiFetch + try/catch) — useMutation migration is T20
  - On successful submit: `form.reset()` + close dialog + invalidate queries (same as current)

  **Must NOT do**:
  - Migrate auth forms (login/register) — they use URL-encoded data
  - Migrate complex multi-section forms (hosts/[id], groups/[id]) — that's T19
  - Change mutation logic (no useMutation yet)
  - Use `Controller` component unless absolutely necessary (prefer `register()`)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with T17, T19)
  - **Blocks**: T20
  - **Blocked By**: T17 (Zod schemas)

  **References**:
  - `frontend/lib/schemas.ts` — Zod schemas from T17
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — Simplest CRUD page. Use as migration template: look at its form state variables and replace with useForm.
  - `frontend/FRONTEND.md:244-293` — Current form pattern with manual state. RHF replaces all the useState + setter pairs.
  - React Hook Form docs: register(), handleSubmit(), formState, reset()

  **Acceptance Criteria**:

  ```
  Scenario: Group creation form validates with Zod
    Tool: Playwright
    Steps:
      1. Navigate to /groups/new
      2. Submit form without filling required fields
      3. Assert inline error messages appear below required fields
      4. Fill in valid data
      5. Submit form
      6. Assert redirect to /groups and new group appears
    Expected Result: Zod validation prevents invalid submissions, valid data creates group
    Evidence: .sisyphus/evidence/task-18-rhf-groups.png

  Scenario: No manual useState for form fields in migrated files
    Tool: Bash
    Steps:
      1. In each migrated file, verify `useForm` is imported from react-hook-form
      2. Verify `zodResolver` is imported from @hookform/resolvers/zod
      3. Verify no form-related useState calls remain (e.g., useState("") for field values)
    Expected Result: Clean RHF migration with zero manual form state
    Evidence: .sisyphus/evidence/task-18-no-manual-state.txt
  ```

  **Commit**: YES
  - Message: `refactor(frontend): migrate simple forms to React Hook Form`
  - Files: groups/new, ssh-keys, git-repos, hosts/new, hosts/discover page.tsx files

- [ ] 19. Migrate Complex Forms to React Hook Form + Zod

  **What to do**:
  - Migrate these complex multi-field forms:
    - `frontend/components/rule-dialog.tsx` — firewall rule create/edit dialog (8 fields, conditional visibility)
    - `frontend/app/(dashboard)/groups/[id]/page.tsx` — inline edit forms (edit name/description dialog, add host dialog, GitOps enable dialog)
    - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — inline edit forms (edit host dialog, add service/hosts-entry/cron-job dialogs)
    - `frontend/app/(dashboard)/groups/[id]/services/page.tsx` — service create/edit dialog
    - `frontend/app/(dashboard)/groups/[id]/hosts-entries/page.tsx` — hosts entry create/edit dialog
    - `frontend/app/(dashboard)/groups/[id]/cron-jobs/page.tsx` — cron job create/edit dialog
    - `frontend/app/(dashboard)/groups/[id]/users/page.tsx` — user create/edit dialog
  - For edit forms: populate with `form.reset(existingData)` when dialog opens
  - For conditional fields (e.g., port fields only shown when protocol != "icmp"): use `form.watch("protocol")` to conditionally render
  - For the password change dialog in sidebar.tsx: use `passwordChangeSchema` with refine for matching passwords
  - Native `<select>` elements: use `form.register()` directly (works with native selects)

  **Must NOT do**:
  - Restructure page architecture (keep single-file pattern)
  - Refactor hosts/[id] page state management (that's T23)
  - Add useMutation (that's T20)

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T17 completes)
  - **Parallel Group**: Wave 4 (with T17, T18)
  - **Blocks**: T20
  - **Blocked By**: T17 (Zod schemas)

  **References**:
  - `frontend/lib/schemas.ts` — Zod schemas from T17
  - `frontend/components/rule-dialog.tsx` — Complex form with conditional fields. Use `form.watch()` for protocol-dependent field visibility.
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Has 100+ state variables. Only migrate the FORM state to RHF, leave other UI state (dialog open/close, loading flags) as useState.
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — Multiple inline dialog forms to migrate

  **Acceptance Criteria**:

  ```
  Scenario: Rule dialog validates CIDR format
    Tool: Playwright
    Steps:
      1. Navigate to /groups/{id}/rules
      2. Click "Add Rule"
      3. Enter invalid CIDR in Source CIDR field: "not-a-cidr"
      4. Submit form
      5. Assert error message appears below Source CIDR field
      6. Enter valid CIDR: "10.0.0.0/8"
      7. Submit form
      8. Assert rule is created successfully
    Expected Result: Zod validates CIDR format, invalid input shows error
    Evidence: .sisyphus/evidence/task-19-rule-validation.png
  ```

  **Commit**: YES
  - Message: `refactor(frontend): migrate complex forms to React Hook Form`
  - Files: rule-dialog.tsx, groups/[id]/page.tsx, hosts/[id]/page.tsx, services/page.tsx, hosts-entries/page.tsx, cron-jobs/page.tsx, users/page.tsx, sidebar.tsx

- [ ] 20. Create useMutation Wrapper + Migrate Pages

  **What to do**:
  - Create `frontend/lib/mutations.ts` with a reusable mutation helper:
    ```tsx
    export function useApiMutation<TData, TVariables>(options: {
      mutationFn: (variables: TVariables) => Promise<TData>
      invalidateKeys?: string[][]
      onSuccess?: (data: TData) => void
      successMessage?: string
      errorMessage?: string
    })
    ```
  - This wraps TanStack Query's `useMutation` with:
    - Auto-invalidation of specified query keys on success
    - Auto-toast on success (`showSuccess(successMessage)`) and error (`showError(errorMessage || error.message)`)
    - Returns `{ mutate, mutateAsync, isPending, error }` (standard useMutation API)
  - Migrate ALL dashboard pages from ad-hoc try/catch/apiFetch pattern to `useApiMutation`:
    - Each page's `handleCreate`, `handleUpdate`, `handleDelete` functions become mutation calls
    - Remove manual `setLoading`, `setError`, `setFormLoading`, `setFormError` state — useMutation handles this
    - Replace manual `queryClient.invalidateQueries()` calls with `invalidateKeys` option
  - Start with simplest page (`ssh-keys/page.tsx`) and work outward to complex pages

  **Must NOT do**:
  - Migrate auth pages (login/register)
  - Add optimistic updates yet (that's T21)
  - Change API endpoints or request/response shapes

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential after T18)
  - **Parallel Group**: Wave 5
  - **Blocks**: T21
  - **Blocked By**: T18 (simple forms migrated)

  **References**:
  - `frontend/lib/api.ts` — `apiFetch` function used in all current mutations. useMutation wraps this.
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — Simplest CRUD pattern. Migrate first as template.
  - `frontend/FRONTEND.md:354-381` — Current mutation pattern to replace (try/catch with manual state management)
  - `frontend/lib/toast.ts` — Toast utility from T3 (auto-called by mutation wrapper)

  **Acceptance Criteria**:

  ```
  Scenario: Mutations use useMutation wrapper
    Tool: Bash
    Steps:
      1. Search all dashboard page.tsx files for `setFormLoading` — should return 0
      2. Search all dashboard page.tsx files for `setFormError` — should return 0 (RHF handles form errors, useMutation handles API errors)
      3. Search for `useApiMutation` imports — should match total mutation count
      4. Run `bunx tsc --noEmit`
    Expected Result: Zero manual loading/error state, all mutations via wrapper
    Evidence: .sisyphus/evidence/task-20-usemutation-migration.txt
  ```

  **Commit**: YES
  - Message: `refactor(frontend): migrate mutations to useMutation wrapper`
  - Files: `frontend/lib/mutations.ts`, all dashboard page.tsx files

- [ ] 21. Add Optimistic Updates for Toggle + Delete Operations

  **What to do**:
  - Add optimistic updates to the `useApiMutation` wrapper with an optional `optimisticUpdate` config:
    ```tsx
    optimisticUpdate?: {
      queryKey: string[]
      updater: (oldData: TData[], variables: TVariables) => TData[]
    }
    ```
  - Implement optimistic updates ONLY for these simple operations:
    - **Delete operations**: Remove item from cached list immediately, rollback on error
    - **Toggle operations**: Toggle GitOps enabled/disabled, toggle drift check enabled — update cache immediately
  - Pattern: `onMutate` → snapshot old data → update cache → `onError` → rollback to snapshot → `onSettled` → invalidate
  - Do NOT add optimistic updates to:
    - Create operations (need server-generated ID)
    - Sync/plan/apply flows (complex server-side effects)
    - Any operation that changes multiple resources

  **Must NOT do**:
  - Add optimistic updates to create operations
  - Add optimistic updates to sync/plan/apply workflows
  - Add optimistic updates to bulk operations

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T20)
  - **Parallel Group**: Wave 5
  - **Blocks**: None
  - **Blocked By**: T20 (useMutation migration)

  **References**:
  - `frontend/lib/mutations.ts` — useMutation wrapper from T20. Extend with optimistic update support.
  - TanStack Query docs on optimistic updates: onMutate/onError/onSettled pattern with queryClient.setQueryData/getQueryData
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — GitOps toggle is a good candidate for optimistic update
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Drift check toggle is a good candidate

  **Acceptance Criteria**:

  ```
  Scenario: Delete updates UI immediately with rollback on error
    Tool: Playwright
    Steps:
      1. Navigate to /ssh-keys
      2. Note item count
      3. Click delete on an SSH key → confirm in ConfirmDialog
      4. Assert item disappears from table immediately (before API response)
      5. Assert toast shows "SSH key deleted"
    Expected Result: UI updates optimistically, no loading delay
    Evidence: .sisyphus/evidence/task-21-optimistic-delete.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add optimistic updates for toggle and delete operations`
  - Files: `frontend/lib/mutations.ts`, affected page.tsx files

- [ ] 22. Add Bulk Actions to List Pages

  **What to do**:
  - Add checkbox selection + bulk action toolbar to these list pages:
    - `/groups` — bulk delete groups
    - `/hosts` — bulk delete hosts
    - `/ssh-keys` — bulk delete SSH keys
  - Checkbox implementation:
    - Add checkbox column as first table column
    - Header checkbox for select all / deselect all
    - Track selection via `useState<Set<string>>` with item IDs
  - Bulk action toolbar:
    - Appears above table when 1+ items selected (sticky, slides in)
    - Shows: "{N} selected" count + "Delete Selected" button (destructive variant)
    - "Delete Selected" triggers ConfirmDialog with count: "Delete {N} groups?"
  - Deletion: sequential single-item API calls (no backend batch endpoint)
    - Show progress: "Deleting {completed}/{total}..."
    - On partial failure: toast with "Deleted {success} of {total}. {failed} failed."
    - Clear selection after completion

  **Must NOT do**:
  - Add backend batch endpoints
  - Add bulk actions to detail pages or module sub-pages
  - Add bulk edit (only bulk delete)
  - Add bulk actions to audit page (read-only)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with T21, T23, T24)
  - **Blocks**: None
  - **Blocked By**: T2 (ConfirmDialog), T3 (Toast)

  **References**:
  - `frontend/components/ui/table.tsx` — Table component. Add checkbox column to TableHead and TableRow.
  - `frontend/components/ui/confirm-dialog.tsx` — ConfirmDialog from T2 for bulk delete confirmation
  - `frontend/app/(dashboard)/groups/page.tsx` — Groups list page to add bulk actions to
  - `frontend/app/(dashboard)/hosts/page.tsx` — Hosts list page
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — SSH keys list page

  **Acceptance Criteria**:

  ```
  Scenario: Bulk delete groups
    Tool: Playwright
    Steps:
      1. Navigate to /groups
      2. Check 2 group checkboxes
      3. Assert bulk action toolbar shows "2 selected"
      4. Click "Delete Selected"
      5. Assert ConfirmDialog shows "Delete 2 groups?"
      6. Confirm deletion
      7. Assert both groups removed from list
      8. Assert toast shows success message
    Expected Result: Bulk delete works with proper UI feedback
    Evidence: .sisyphus/evidence/task-22-bulk-delete.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add bulk actions to list pages`
  - Files: groups/page.tsx, hosts/page.tsx, ssh-keys/page.tsx

- [ ] 23. Extract hosts/[id] State into useHostDetail Hook

  **What to do**:
  - Create `frontend/hooks/use-host-detail.ts`
  - Extract the 100+ state variables from `hosts/[id]/page.tsx` into a structured custom hook
  - Group state by concern:
    - `useHostQueries(id)` — all useQuery calls (host, effective rules, groups, ssh keys, module statuses)
    - `useHostDialogs()` — all dialog open/close state (editDialog, deleteDialog, serviceDialog, etc.)
    - `useHostForms()` — all form state for inline edit dialogs (leveraging React Hook Form from T19)
  - The page component should become a thin render shell that imports and uses these hooks
  - Keep all mutations and event handlers that reference multiple state pieces in the page file — don't force awkward hook boundaries

  **Must NOT do**:
  - Restructure the page into sub-routes or sub-components
  - Change any user-facing behavior
  - Move query keys or API endpoints

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with T20-T22, T24)
  - **Blocks**: None
  - **Blocked By**: None (can run independently)

  **References**:
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — The page with 100+ state variables to extract from. Read this THOROUGHLY before starting.
  - `frontend/lib/types.ts` — TypeScript interfaces for Host, HostGroup, SSHKey, etc.

  **Acceptance Criteria**:

  ```
  Scenario: Host detail page works identically after refactor
    Tool: Playwright
    Steps:
      1. Navigate to /hosts/{id}
      2. Verify all sections render (host info, groups, effective rules, module statuses)
      3. Open edit dialog — verify form populates
      4. Cancel edit — verify no changes
      5. Check drift — verify status updates
    Expected Result: Identical behavior, cleaner code structure
    Evidence: .sisyphus/evidence/task-23-host-detail-refactor.png

  Scenario: Page state reduced in hosts/[id]/page.tsx
    Tool: Bash
    Steps:
      1. Count useState calls in hosts/[id]/page.tsx — should be significantly fewer than before
      2. Verify hooks/use-host-detail.ts exists with exported hooks
      3. Run `bunx tsc --noEmit`
    Expected Result: State extracted to hooks, page is thinner
    Evidence: .sisyphus/evidence/task-23-state-reduction.txt
  ```

  **Commit**: YES
  - Message: `refactor(frontend): extract hosts detail state into useHostDetail hook`
  - Files: `frontend/hooks/use-host-detail.ts`, `frontend/app/(dashboard)/hosts/[id]/page.tsx`

- [ ] 24. Add Keyboard Shortcuts

  **What to do**:
  - Register global keyboard shortcuts in `frontend/components/app-shell.tsx`:
    - `Cmd/Ctrl + K` — open command palette (already handled by T8, verify it works)
    - `Escape` — close any open dialog or command palette
  - Verify that Escape properly closes: ConfirmDialog, all form dialogs, command palette, mobile sidebar
  - If any dialog doesn't close on Escape, add `onKeyDown` handler
  - No other shortcuts — keep it minimal

  **Must NOT do**:
  - Add shortcuts for mutations (no Cmd+S to save)
  - Add shortcuts for navigation (only Cmd+K palette)
  - Add shortcut hints/cheatsheet UI

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with T20-T23)
  - **Blocks**: None
  - **Blocked By**: T8 (command palette)

  **References**:
  - `frontend/components/command-palette.tsx` — Command palette from T8. Verify Cmd+K integration.
  - `frontend/components/app-shell.tsx` — Global keyboard listener location
  - `frontend/components/ui/dialog.tsx` — Verify Escape closes dialogs (base-ui should handle this natively)

  **Acceptance Criteria**:

  ```
  Scenario: Keyboard shortcuts work
    Tool: Playwright
    Steps:
      1. Navigate to /dashboard
      2. Press Cmd+K — assert command palette opens
      3. Press Escape — assert command palette closes
      4. Open a form dialog
      5. Press Escape — assert dialog closes
    Expected Result: All keyboard shortcuts functional
    Evidence: .sisyphus/evidence/task-24-keyboard-shortcuts.png
  ```

  **Commit**: YES
  - Message: `feat(frontend): add keyboard shortcuts`
  - Files: `frontend/components/app-shell.tsx`

- [ ] 25. Update FRONTEND.md Conventions

  **What to do**:
  - Update `frontend/FRONTEND.md` to document all new conventions introduced in this plan:
  - **New sections to add**:
    - "Toast Notifications" — when to use toasts (mutations only), duration rules, position
    - "Loading States" — skeleton components, `useDelayedLoading` hook, when to use which skeleton variant
    - "Confirmation Dialogs" — ConfirmDialog usage, variant rules, when to use destructive vs default
    - "Form Validation" — React Hook Form + Zod pattern, validation timing (onSubmit), error display
    - "Error Boundaries" — error.tsx behavior, recovery options
    - "Breadcrumbs" — component API, maximum depth, placement
    - "Tooltips" — when to add (complex fields only), placement, content guidelines
    - "Command Palette" — navigation-only scope, items source
    - "Keyboard Shortcuts" — Cmd+K and Escape only
    - "Mobile Responsive" — breakpoint (md: 768px), sidebar behavior
    - "Bulk Actions" — checkbox pattern, sequential deletion, partial failure handling
    - "Mutations" — useMutation wrapper, optimistic updates scope
  - **Sections to update**:
    - "Things NOT Used" — remove toast, skeleton, useMutation from this list (they're now used)
    - "Data Fetching" → add useMutation pattern
    - "Forms" → update to show React Hook Form pattern
  - Keep existing formatting and structure

  **Must NOT do**:
  - Remove any still-valid conventions
  - Change stack/technology descriptions (unless reflecting actual changes)

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with T26)
  - **Blocks**: None
  - **Blocked By**: All T1-T24 (documents final conventions)

  **References**:
  - `frontend/FRONTEND.md` — The file to update. Read it fully before editing.
  - All new components and patterns from T1-T24

  **Acceptance Criteria**:

  ```
  Scenario: FRONTEND.md reflects all new conventions
    Tool: Bash
    Steps:
      1. Verify FRONTEND.md contains sections for: Toast, Skeleton, ConfirmDialog, RHF, Error Boundaries, Breadcrumbs, Tooltips, Command Palette, Keyboard Shortcuts, Mobile, Bulk Actions, useMutation
      2. Verify "Things NOT Used" section no longer lists toast, skeleton, useMutation
      3. Verify form pattern examples use React Hook Form
    Expected Result: Complete documentation of all new conventions
    Evidence: .sisyphus/evidence/task-25-frontend-md.txt
  ```

  **Commit**: YES
  - Message: `docs(frontend): update FRONTEND.md with new UX conventions`
  - Files: `frontend/FRONTEND.md`

- [ ] 26. Extend Playwright E2E Test Suite

  **What to do**:
  - Create new E2E test files for key UX features:
    - `frontend/e2e/ux-confirm-dialog.spec.ts` — test ConfirmDialog in real delete flows (SSH key delete, group delete)
    - `frontend/e2e/ux-toast.spec.ts` — test toast appears on mutation success/error
    - `frontend/e2e/ux-command-palette.spec.ts` — test palette open/search/navigate/close
    - `frontend/e2e/ux-mobile.spec.ts` — test mobile sidebar at 375px viewport
    - `frontend/e2e/ux-search.spec.ts` — test search/filter on groups and hosts pages
    - `frontend/e2e/ux-breadcrumbs.spec.ts` — test breadcrumb navigation on nested pages
  - Follow existing test patterns:
    - Use `auth.setup.ts` for authenticated session
    - Use API-based test data setup (create test groups/hosts via API before testing UI)
    - Clean up test data after each test
  - Each test file should have 2-4 focused test cases

  **Must NOT do**:
  - Write component-level tests (Playwright only)
  - Modify existing test files
  - Write tests for internal implementation details

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with T25)
  - **Blocks**: None
  - **Blocked By**: All T1-T24

  **References**:
  - `frontend/e2e/auth.setup.ts` — Auth setup pattern. New tests inherit authenticated session.
  - `frontend/e2e/groups.spec.ts` — Example test file. Follow same patterns: API-based data setup, assertion patterns.
  - `frontend/playwright.config.ts` — Playwright configuration. New test files auto-discovered.
  - `frontend/e2e/hosts.spec.ts` — Another example with CRUD test patterns

  **Acceptance Criteria**:

  ```
  Scenario: All new E2E tests pass
    Tool: Bash
    Steps:
      1. Run `npx playwright test e2e/ux-*.spec.ts`
      2. Assert all tests pass
      3. Assert no existing tests broken: `npx playwright test`
    Expected Result: All E2E tests pass (new and existing)
    Evidence: .sisyphus/evidence/task-26-e2e-results.txt
  ```

  **Commit**: YES
  - Message: `test(frontend): extend Playwright E2E suite for new UX components`
  - Files: All new `frontend/e2e/ux-*.spec.ts` files

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `bunx tsc --noEmit` + `bunx next build`. Review all changed files for: `as any`/`@ts-ignore`, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names (data/result/item/temp). Verify new components follow shadcn/ui base-ui patterns (NOT Radix).
  Output: `Build [PASS/FAIL] | TypeCheck [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high` + `playwright` skill
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration: confirm dialog → toast appears, command palette navigates correctly, mobile sidebar works, search filters correctly, breadcrumbs reflect current page. Test edge cases: empty search, rapid dialog open/close, mobile + desktop transitions.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance: no backend changes, no auth page RHF, no server-side search, no cmdk actions, no animation library. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

Each task gets its own atomic commit. App must build and existing E2E tests must pass after every commit.

- **T1**: `chore(frontend): add sonner, cmdk, react-hook-form, zod dependencies`
- **T2**: `feat(ui): add ConfirmDialog component`
- **T3**: `feat(ui): add Sonner toast provider and utility`
- **T4**: `feat(ui): add Skeleton loading components`
- **T5**: `feat(ui): add Breadcrumb navigation component`
- **T6**: `feat(ui): add Tooltip component`
- **T7**: `feat(frontend): add dashboard layout with error boundaries`
- **T8**: `feat(frontend): add command palette with cmdk`
- **T9**: `feat(frontend): add responsive mobile sidebar`
- **T10**: `refactor(frontend): add breadcrumbs to all dashboard pages`
- **T11**: `refactor(frontend): replace loading text with skeleton states`
- **T12**: `refactor(frontend): replace browser dialogs in sidebar`
- **T13**: `refactor(frontend): replace browser dialogs in group pages`
- **T14**: `refactor(frontend): replace browser dialogs in host and module pages`
- **T15**: `feat(frontend): add search/filter to all list pages`
- **T16**: `feat(frontend): add tooltips to complex form fields`
- **T17**: `feat(frontend): add Zod validation schemas for all entities`
- **T18**: `refactor(frontend): migrate simple forms to React Hook Form`
- **T19**: `refactor(frontend): migrate complex forms to React Hook Form`
- **T20**: `refactor(frontend): migrate mutations to useMutation wrapper`
- **T21**: `feat(frontend): add optimistic updates for toggle and delete operations`
- **T22**: `feat(frontend): add bulk actions to list pages`
- **T23**: `refactor(frontend): extract hosts detail state into useHostDetail hook`
- **T24**: `feat(frontend): add keyboard shortcuts`
- **T25**: `docs(frontend): update FRONTEND.md with new UX conventions`
- **T26**: `test(frontend): extend Playwright E2E suite for new UX components`

---

## Success Criteria

### Verification Commands
```bash
bunx tsc --noEmit              # Expected: no errors
bunx next build                # Expected: successful build
npx playwright test            # Expected: all tests pass (existing + new)
```

### Final Checklist
- [ ] Zero browser `confirm()`/`alert()` calls remain
- [ ] All list pages have functional search/filter
- [ ] All pages show skeleton loading states
- [ ] All pages have breadcrumb navigation
- [ ] ConfirmDialog, Toast, Skeleton, Breadcrumb, Tooltip, CommandPalette components exist
- [ ] Error boundaries catch and display errors gracefully
- [ ] Mobile sidebar toggles correctly at 768px breakpoint
- [ ] React Hook Form + Zod on all dashboard forms
- [ ] useMutation wrapper used for all mutations
- [ ] FRONTEND.md updated with new conventions
- [ ] All tests pass
