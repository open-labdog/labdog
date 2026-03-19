# Group Hosts in Hosts View

## TL;DR

> **Quick Summary**: Add group visibility and grouping to the `/hosts` page. Show group badges per host, add a group filter dropdown, and provide a toggle between flat table view and grouped-by-section view.
>
> **Deliverables**:
> - Group badges column in hosts table
> - Group filter dropdown (select one or more groups)
> - View toggle: "Flat" (table with group column) vs "Grouped" (collapsible sections per group)
> - Ungrouped hosts section in grouped view
> - Multi-group hosts appear under each group in grouped view
> - View preference persisted in localStorage
>
> **Estimated Effort**: S (1-2 hours)
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: T1 → T2 → T3

---

## Context

### Current State
- `/hosts` page shows a flat table: checkbox, hostname, IP, firewall backend, sync status, actions
- `Host` interface has `group_ids: number[]` — group membership already available in data
- `HostGroup` interface has `id`, `name`, `priority` — available via `/api/groups`
- Page already has search/filter, bulk actions, breadcrumbs, skeletons
- No group visibility anywhere on the hosts list

### Architecture
- **Frontend-only** — no API changes needed
- Fetch `HostGroup[]` via existing `/api/groups` endpoint (parallel query)
- Client-side grouping: `group_ids` on each host → map to group names
- View toggle stored in `localStorage` key `barricade-hosts-view`

---

## Work Objectives

### Definition of Done
- [x] Hosts table has a "Groups" column showing group name badges
- [x] Group filter dropdown above table (multi-select)
- [x] Toggle between "Flat" and "Grouped" views
- [x] Grouped view: collapsible sections per group, ungrouped section
- [x] Multi-group hosts appear in each relevant group section
- [x] View preference persisted in localStorage
- [x] `npm run build` passes

### Must Have
- Group name badges in each host row (colored, clickable → navigates to group)
- Group filter dropdown (filter hosts to show only those in selected group)
- Flat/Grouped view toggle button
- Grouped view with collapsible sections per group name
- "Ungrouped" section for hosts with empty `group_ids`
- Search still works in both views
- Bulk actions still work in both views

### Must NOT Have (Guardrails)
- NO backend API changes
- NO new API endpoints
- NO server-side filtering
- NO drag-and-drop group assignment from this view
- NO changes to other pages

---

## Verification Strategy

> Frontend-only — verify with `rtk npm run build` + visual QA

### QA Policy
Every task includes agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

---

## Execution Strategy

```
Wave 1 (Foundation — 1 task):
└── T1: Add groups query + group badges column + filter dropdown [visual-engineering]

Wave 2 (Grouped View — 1 task):
└── T2: Add view toggle + grouped section view [visual-engineering]

Wave 3 (Polish — 1 task):
└── T3: E2E test + polish [quick]

Critical Path: T1 → T2 → T3
Max Concurrent: 1 (sequential — all modify hosts/page.tsx)
```

### Agent Dispatch Summary

- **Wave 1**: **1** — T1 → `visual-engineering`
- **Wave 2**: **1** — T2 → `visual-engineering`
- **Wave 3**: **1** — T3 → `quick`

---

## TODOs

- [x] 1. Add Groups Query + Group Badges Column + Filter Dropdown

  **What to do**:
  - Add parallel `useQuery` for `HostGroup[]` from `/api/groups` (reuse existing query key `["groups"]`)
  - Build a `groupMap: Map<number, HostGroup>` from the groups data for O(1) lookups
  - Add a "Groups" column to the table (after "IP Address", before "Firewall"):
    - For each host, render `host.group_ids.map(id => groupMap.get(id))` as small badges
    - Badge style: `text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-300`
    - Each badge is a `<Link>` to `/groups/{id}` (clickable navigation)
    - Hosts with no groups: show `—` (em dash) in muted text
  - Add group filter dropdown above the table (next to search input):
    - `<select>` with options: "All Groups" (default), each group name, "Ungrouped"
    - When a group is selected, filter `filteredHosts` to only show hosts that have that `group_id` in their `group_ids` array
    - "Ungrouped" filter: show hosts where `group_ids.length === 0`
    - Filter works alongside existing search (both applied)
  - Add `filterGroup` state: `useState<number | "ungrouped" | null>(null)`
  - Update `filteredHosts` logic:
    ```tsx
    const filteredHosts = hosts?.filter(h => {
      const matchesSearch = h.hostname.toLowerCase().includes(q) || h.ip_address.toLowerCase().includes(q)
      const matchesGroup = filterGroup === null ? true
        : filterGroup === "ungrouped" ? h.group_ids.length === 0
        : h.group_ids.includes(filterGroup)
      return matchesSearch && matchesGroup
    }) ?? []
    ```

  **Must NOT do**:
  - Do NOT add backend API changes
  - Do NOT modify the groups query endpoint

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: T2
  - **Blocked By**: None

  **References**:
  - `frontend/app/(dashboard)/hosts/page.tsx` — Current hosts page (modify this file)
  - `frontend/lib/types.ts:5-14` — `HostGroup` interface
  - `frontend/lib/types.ts:15-22` — `Host` interface with `group_ids: number[]`
  - `frontend/app/(dashboard)/groups/page.tsx` — Reference for how groups are displayed

  **Acceptance Criteria**:

  ```
  Scenario: Group badges appear in hosts table
    Tool: Bash
    Steps:
      1. Verify hosts/page.tsx imports useQuery for groups
      2. Verify "Groups" column exists in TableHeader
      3. Verify group badges rendered per host row
      4. rtk npm run build passes
    Expected Result: Build passes, group column added
    Evidence: .sisyphus/evidence/task-1-group-badges.txt

  Scenario: Group filter dropdown filters hosts
    Tool: Bash
    Steps:
      1. Verify <select> element exists with group options
      2. Verify filteredHosts logic includes group filter
    Expected Result: Filter state and logic present
    Evidence: .sisyphus/evidence/task-1-group-filter.txt
  ```

  **Commit**: YES
  - Message: `feat(frontend): add group badges and filter to hosts list`
  - Files: `frontend/app/(dashboard)/hosts/page.tsx`

- [x] 2. Add View Toggle + Grouped Section View

  **What to do**:
  - Add view toggle state: `const [viewMode, setViewMode] = useState<"flat" | "grouped">(() => (typeof window !== "undefined" && localStorage.getItem("barricade-hosts-view") === "grouped") ? "grouped" : "flat")`
  - Persist on change: `useEffect(() => localStorage.setItem("barricade-hosts-view", viewMode), [viewMode])`
  - Add toggle button next to search/filter area:
    ```tsx
    <div className="flex items-center gap-2">
      <button
        onClick={() => setViewMode(viewMode === "flat" ? "grouped" : "flat")}
        className="text-sm text-slate-400 hover:text-white flex items-center gap-1"
      >
        {viewMode === "flat" ? <LayoutListIcon className="w-4 h-4" /> : <TableIcon className="w-4 h-4" />}
        {viewMode === "flat" ? "Group View" : "Flat View"}
      </button>
    </div>
    ```
  - When `viewMode === "flat"`: render current table (with new group column from T1)
  - When `viewMode === "grouped"`: render grouped sections:
    - Build groups from `filteredHosts`:
      ```tsx
      const groupedHosts = useMemo(() => {
        const groups = new Map<string, { group: HostGroup | null; hosts: Host[] }>()
        // Add named groups
        for (const host of filteredHosts) {
          if (host.group_ids.length === 0) {
            const key = "__ungrouped__"
            if (!groups.has(key)) groups.set(key, { group: null, hosts: [] })
            groups.get(key)!.hosts.push(host)
          } else {
            for (const gid of host.group_ids) {
              const g = groupMap.get(gid)
              const key = String(gid)
              if (!groups.has(key)) groups.set(key, { group: g ?? null, hosts: [] })
              groups.get(key)!.hosts.push(host)
            }
          }
        }
        // Sort: named groups by priority DESC, ungrouped last
        return [...groups.entries()].sort(([a, av], [b, bv]) => {
          if (a === "__ungrouped__") return 1
          if (b === "__ungrouped__") return -1
          return (bv.group?.priority ?? 0) - (av.group?.priority ?? 0)
        })
      }, [filteredHosts, groupMap])
      ```
    - Render each group as a collapsible section:
      ```tsx
      {groupedHosts.map(([key, { group, hosts }]) => (
        <details key={key} open className="mb-4">
          <summary className="cursor-pointer flex items-center gap-2 py-2 text-sm font-medium text-slate-300 hover:text-white">
            <span>{group?.name ?? "Ungrouped"}</span>
            <span className="text-slate-500">({hosts.length})</span>
          </summary>
          <div className="rounded-lg border border-slate-700 bg-slate-900 mt-1">
            <Table>
              {/* Same table structure as flat view, minus the Groups column */}
            </Table>
          </div>
        </details>
      ))}
      ```
    - Checkbox/bulk-select still works across groups
    - Search still filters within grouped view

  **Must NOT do**:
  - Do NOT break existing flat table functionality
  - Do NOT break bulk actions
  - Do NOT break search

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: T3
  - **Blocked By**: T1

  **References**:
  - `frontend/app/(dashboard)/hosts/page.tsx` — Modified by T1, continue modifying
  - Lucide icons: `LayoutListIcon`, `TableIcon` (or `ListIcon`, `Grid3x3Icon`)

  **Acceptance Criteria**:

  ```
  Scenario: View toggle switches between flat and grouped
    Tool: Bash
    Steps:
      1. Verify viewMode state with localStorage persistence
      2. Verify toggle button exists
      3. Verify grouped view renders <details>/<summary> sections
      4. rtk npm run build passes
    Expected Result: Both views render, toggle works
    Evidence: .sisyphus/evidence/task-2-view-toggle.txt

  Scenario: Grouped view shows ungrouped section
    Tool: Bash
    Steps:
      1. Verify "__ungrouped__" key handling in groupedHosts
      2. Verify "Ungrouped" label rendered for hosts with no groups
    Expected Result: Ungrouped hosts have their own section
    Evidence: .sisyphus/evidence/task-2-ungrouped.txt
  ```

  **Commit**: YES
  - Message: `feat(frontend): add grouped view toggle for hosts list`
  - Files: `frontend/app/(dashboard)/hosts/page.tsx`

- [x] 3. Playwright E2E Test

  **What to do**:
  - Create `frontend/e2e/ux-hosts-grouping.spec.ts`:
    - Test 1: Group badges column exists — navigate to `/hosts`, assert "Groups" column header
    - Test 2: Group filter dropdown exists — assert `<select>` with group options
    - Test 3: View toggle button exists — assert toggle button with "Group View" or "Flat View" text
    - Test 4: Grouped view renders sections — click toggle to grouped, assert `<details>` elements exist
  - Run `rtk npm run build` to verify no regressions

  **Must NOT do**:
  - Do NOT modify existing test files

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: F1-F4
  - **Blocked By**: T2

  **References**:
  - `frontend/e2e/ux-search.spec.ts` — Reference E2E test pattern
  - `frontend/playwright.config.ts` — Config

  **Acceptance Criteria**:

  ```
  Scenario: E2E tests pass
    Tool: Bash
    Steps:
      1. Verify file exists: frontend/e2e/ux-hosts-grouping.spec.ts
      2. rtk npm run build passes
    Expected Result: Test file created, build clean
    Evidence: .sisyphus/evidence/task-3-e2e.txt
  ```

  **Commit**: YES
  - Message: `test(frontend): add hosts grouping E2E tests`
  - Files: `frontend/e2e/ux-hosts-grouping.spec.ts`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — verify all Must Have items present, all Must NOT Have guardrails respected
- [x] F2. **Code Quality Review** — `rtk tsc --noEmit` + `rtk npm run build`, check for anti-patterns
- [x] F3. **Scope Fidelity Check** — verify no backend changes, no other pages modified

---

## Commit Strategy

- **T1**: `feat(frontend): add group badges and filter to hosts list`
- **T2**: `feat(frontend): add grouped view toggle for hosts list`
- **T3**: `test(frontend): add hosts grouping E2E tests`

---

## Success Criteria

### Verification Commands
```bash
cd frontend && rtk tsc --noEmit    # Expected: no errors
cd frontend && rtk npm run build   # Expected: successful build
```

### Final Checklist
- [x] Group badges visible in hosts table
- [x] Group filter dropdown works
- [x] Flat/Grouped toggle works
- [x] Grouped view has collapsible sections
- [x] Ungrouped hosts have their own section
- [x] Multi-group hosts appear in each group section
- [x] Search works in both views
- [x] Bulk actions work in both views
- [x] View preference persisted in localStorage
- [x] Frontend build passes
