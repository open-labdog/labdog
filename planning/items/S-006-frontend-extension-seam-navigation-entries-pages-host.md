# S-006: Frontend extension seam: navigation entries, pages, host and group panels

| | |
|---|---|
| Edition | shared |
| Source | roadmap, "Split into a Community (AGPL) repository and an Enterprise repository" (open point: the frontend seam) |
| Tier / score | P1 / 11.0 |
| Stage | 5 |
| Effort | S |
| Autonomy | B |
| Depends on | none (C-061 for the unit tests, which lands in stage 0) |

## Goal

An extension can add an entry to the navigation pane, a full page, and a panel on the host
and group pages by filling lists in `frontend/lib/extensions.ts`. With empty lists, which is
what Community ships, every page renders exactly as it does today.

## Scope

In scope:
- Four new lists in the seam: `navItems`, `pages`, `hostPanels`, `groupPanels`.
- One Community route that renders an extension page.
- Reading every seam list through a helper that tolerates a missing export.
- The command palette lists extension pages and extension Settings sections.

Out of scope:
- Any extension content. Community's lists stay empty.
- Extra tabs on the host or group page. Panels go on the Overview tab only.
- A backend change. `PLUGIN_API` is not bumped: it versions the Python contract.
- Extension points inside dialogs or run buttons (for example a second button beside "Run").

## Context

- `frontend/lib/extensions.ts` exports two lists today, `settingsSections`
  (`SettingsSectionExtension`) and `userPanels` (`UserPanelExtension`). Its header says the
  types may only change additively.
- Consumers: `frontend/app/(dashboard)/settings/client-page.tsx:11,40-42` and
  `frontend/app/(dashboard)/users/page.tsx:11,437-441`. Both import the lists by name.
- An extension build replaces the whole module with its own copy. A copy written before
  this item does not export the new lists, so a named import of them would fail its
  type-check. That is the compatibility case the header's rule exists for.
- Navigation is data in `frontend/components/shell/zones.ts`: `ZONE_DEFS` (line 58),
  `zonesFor()` (line 110), `ROUTE_ZONES` (line 129) with `zoneForPath()`, and
  `itemIsActive()` (line 182), which compares the `view`, `tab` and `section` query keys.
  `pane.tsx`, `rail.tsx` and `palette.tsx:65` all read `zonesFor(user)`.
- `palette.tsx:31` has a hard-coded `SETTINGS_SECTIONS` list, so a Settings section added
  by an extension cannot be reached from the palette.
- The frontend is a static export (`next.config.ts`). Dynamic segments work only because
  the backend rewrites a `placeholder` directory, and `_SAFE_DYNAMIC_SEGMENT` in
  `backend/app/main.py:1006` allows digits only, on purpose. A slug route such as
  `/ext/[key]` would need that security boundary widened. A query parameter does not.
- Host page: `frontend/app/(dashboard)/hosts/[id]/client-page.tsx` renders `OverviewTab`
  (`_tabs/overview.tsx`) at line 316. Group page:
  `frontend/app/(dashboard)/groups/[id]/client-page.tsx` renders `GroupOverview`
  (`_tabs/overview.tsx`) at line 194. Both pages sit inside `<PermissionScope>`
  (`layout.tsx`), so `useCan()` answers for that host or group.

## Dependencies

- C-061 (frontend unit-test harness, stage 0). If it has not landed, stop and say so: the
  non-empty case cannot be tested with Playwright, because Community's lists are empty.
- No decision is open.

## Implementation steps

1. In `frontend/lib/extensions.ts` add the types and four empty lists. Keep the two
   existing exports unchanged.
   - `NavItemExtension { zone: "overview" | "fleet" | "ops" | "assistant"; label: string; href: string; permission?: string }`.
     `permission` is tested with `canAnywhere`.
   - `PageExtension { key: string; title: string; zone: "overview" | "fleet" | "ops" | "assistant" | "settings"; permission?: string; Component: ComponentType }`.
     `key` matches `^[a-z0-9-]{1,40}$`. The page is served at `/ext?page=<key>`.
   - `HostPanelExtension { key: string; label: string; permission?: string; Component: ComponentType<{ host: Host }> }`.
   - `GroupPanelExtension { key: string; label: string; permission?: string; Component: ComponentType<{ group: HostGroup }> }`.
     `Host` and `HostGroup` come from `@/lib/types`. Panel permissions are tested with
     `useCan`, so a scoped user sees a panel only on a host or group where they hold it.
2. Create `frontend/lib/extension-lists.ts`. It does `import * as ext from "@/lib/extensions"`
   and exports `settingsSections`, `userPanels`, `navItems`, `pages`, `hostPanels` and
   `groupPanels`, each as `(ext as Partial<...>).<name> ?? []`. Change the two existing
   consumers to import from this helper. Add a line to the header of `extensions.ts`
   saying pages read the lists through the helper, and why.
3. `components/shell/zones.ts`: `zonesFor(user)` appends to each zone the `navItems` for
   that zone that the user may see, after Community's own items. `zoneForPath()` takes the
   current search string as an optional second argument and, for `/ext`, returns the
   `zone` of the page named by `?page=`. Add `"page"` to the key list in `itemIsActive()`.
   Update the callers of `zoneForPath` in `components/app-shell.tsx`.
4. Create `frontend/app/(dashboard)/ext/page.tsx` and `client-page.tsx`, following the
   `settings/page.tsx` pair. The client page reads `page` with `useSearchParams()`, finds
   the entry in `pages`, checks `permission` with `canAnywhere`, renders `PageHead` with
   the title and then the component. An unknown key or a missing permission renders the
   kit's `Empty` with "Page not found". No dynamic segment, no `generateStaticParams`.
5. Create `frontend/components/extension-panels.tsx` with `HostExtensionPanels({ host })`
   and `GroupExtensionPanels({ group })`. Each filters by `useCan(permission)` and renders
   every panel inside the same section wrapper the Overview tab uses for its own blocks,
   with `label` as the heading. Each returns `null` when nothing is left. Render them as the
   last child of `OverviewTab` and of `GroupOverview`.
6. `components/shell/palette.tsx`: add one "destinations" entry per visible extension page
   (label `title`, `run` goes to `/ext?page=<key>`) and one per extension Settings section
   (`/settings?section=<key>`), both filtered by permission.
7. Unit tests with the C-061 runner, mocking `@/lib/extensions`:
   `frontend/lib/__tests__/extension-lists.test.ts` (a module without the new exports
   yields empty lists), `frontend/components/shell/__tests__/zones-extensions.test.ts`
   (item appended to the right zone, hidden without permission, `zoneForPath("/ext", "page=x")`,
   `itemIsActive` with `page`), and `frontend/components/__tests__/extension-panels.test.tsx`
   (panel shown, hidden without permission, nothing rendered for an empty list).
8. Update the docs listed below.

## Acceptance criteria

- With Community's empty lists, `git diff` of rendered output is nil: the pane, rail and
  palette show the same entries as before, and the host and group Overview tabs have no new
  element in the DOM (checked by the existing Playwright specs passing unchanged).
- A test module that fills `navItems` gets its entry in the named zone's pane and in the
  palette, and a user without its permission does not.
- `/ext?page=<key>` renders the registered component under the registered zone, and "Page
  not found" for an unknown key or a user without the permission.
- A filled `hostPanels` or `groupPanels` list renders on the Overview tab, and a panel
  whose permission the user lacks on that host or group is absent.
- A replacement `extensions.ts` that exports only `settingsSections` and `userPanels`
  still type-checks and builds.
- `backend/app/main.py` is unchanged.

## Test plan

```bash
cd frontend && npx tsc --noEmit && npm run lint
cd frontend && npm test -- extension            # the C-061 script name; check package.json
cd frontend && npx playwright test host-group-overview.spec.ts ux-command-palette.spec.ts settings-integrations.spec.ts
```

Playwright needs the full stack: run it on local Docker on lin-manager-2. For the
old-copy case, temporarily replace `lib/extensions.ts` with a two-export copy in a scratch
checkout, run `npx tsc --noEmit`, and discard the checkout. Run the whole unit and
Playwright suites once before the stage review.

## Docs and changelog

- `frontend/FRONTEND.md`: a section "Extension seam" listing the six lists, where each is
  rendered, and the rule that pages read them through `lib/extension-lists.ts`.
- `CLAUDE.md`, section "Extensions (plugins) and directory sign-in": replace the sentence
  describing the seam as "lists of extra Settings sections and Users-page edit panels".
- `CHANGELOG.md` `[Unreleased]`, Added: "Frontend extension seam: an extension can add
  navigation entries, pages and panels on host and group pages. A Community install looks
  exactly as before."

## Risks and rollback

- A regression in `zonesFor` or `itemIsActive` would affect every page's navigation. The
  unit tests in step 7 and the existing `ux-*` Playwright specs cover it.
- A panel that throws would take the Overview tab down with it. The frontend has no
  reusable error boundary component today (only the route-level `app/(dashboard)/error.tsx`),
  so add a small class-based boundary inside `extension-panels.tsx` and wrap each panel in
  it, so one panel fails alone.
- Rollback: revert the commit. Nothing is stored and no API changes.

## Assumptions

- Assumption (not yet confirmed): an extension page lives at `/ext?page=<key>` instead of a
  slug route, to leave the digits-only rewrite in `backend/app/main.py` untouched. Question
  for Dennis: is a query-parameter URL acceptable for extension pages?
- Assumption (not yet confirmed): panels are sections on the Overview tab, not new tabs.
  A tab seam can be added later without changing these types.
- Assumption (not yet confirmed): no `PLUGIN_API` bump, because nothing in the Python
  contract changes.

## Definition of done

- [ ] Code merged to `dev` through a reviewed PR.
- [ ] Unit tests added and green; `tsc`, lint and the named Playwright specs green.
- [ ] `FRONTEND.md`, `CLAUDE.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set to `done`; one line in `planning/PROGRESS.md`.
