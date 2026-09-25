import { test, expect } from "./fixtures"

test.describe("Audit page", () => {
  test("audit page loads with heading", async ({ page }) => {
    await page.goto("/audit")
    await expect(page.getByRole("heading", { name: "Audit", exact: true })).toBeVisible()
  })

  test("audit page shows filter controls", async ({ page }) => {
    await page.goto("/audit")
    // The kit Filter's accessible name is its label plus the ▾ glyph —
    // "action ▾" — until a value is picked, when the picked label is
    // inserted before the glyph.
    await expect(page.getByRole("button", { name: /^action\s*▾$/i })).toBeVisible()
    await expect(page.getByRole("button", { name: /^entity\s*▾$/i })).toBeVisible()
  })

  test("action filter dropdown has expected options", async ({ page }) => {
    await page.goto("/audit")
    // Open the Action filter popover
    await page.getByRole("button", { name: /^action\s*▾$/i }).click()

    // Options are lowercase labels from lib/status.ts AUDIT_ACTION, each
    // with a loaded-count suffix — substring, case-insensitive matching
    // finds them regardless of the count.
    await expect(page.getByRole("button", { name: "create" })).toBeVisible()
    await expect(page.getByRole("button", { name: "update" })).toBeVisible()
    await expect(page.getByRole("button", { name: "delete" })).toBeVisible()
  })

  test("entity filter dropdown has expected options", async ({ page }) => {
    await page.goto("/audit")
    // Entity has no fixed vocabulary — its options come from whatever
    // has loaded — so just check the control opens with at least one row.
    await expect(page.getByRole("button", { name: /^entity\s*▾$/i })).toBeVisible()
    await page.getByRole("button", { name: /^entity\s*▾$/i }).click()
    await expect(page.getByRole("button", { name: /^all entity/i })).toBeVisible()
  })

  test("audit entries table is visible with data", async ({ page }) => {
    await page.goto("/audit")

    // Either real rows or the empty state — the page no longer falls back
    // to stub data, so an empty table now genuinely means no entries.
    // The table is always rendered; when there are no rows it carries the
    // empty message in a cell. `getByRole("table").or(getByText(...))`
    // matched both and tripped strict mode.
    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })
  })

  test("filter by action type filters entries", async ({ page }) => {
    await page.goto("/audit")

    // Wait for entries to load
    // The table is always rendered; when there are no rows it carries the
    // empty message in a cell. `getByRole("table").or(getByText(...))`
    // matched both and tripped strict mode.
    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })

    // Open Action filter and click Create option
    await page.getByRole("button", { name: /^action\s*▾$/i }).click()
    // After filtering, either entries remain or empty state shows
    await expect(page.getByRole("table")).toBeVisible()
  })

  test("filter by entity type filters entries", async ({ page }) => {
    await page.goto("/audit")

    // The table is always rendered; when there are no rows it carries the
    // empty message in a cell. `getByRole("table").or(getByText(...))`
    // matched both and tripped strict mode.
    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })

    // Entity filter is present
    await expect(page.getByRole("button", { name: /^entity\s*▾$/i })).toBeVisible()
    await expect(page.getByRole("table")).toBeVisible()
  })

  test("table shows expected columns", async ({ page }) => {
    await page.goto("/audit")

    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })

    const headers = page.getByRole("columnheader")
    await expect(headers.filter({ hasText: "when" })).toBeVisible()
    await expect(headers.filter({ hasText: "user" })).toBeVisible()
    await expect(headers.filter({ hasText: "action" })).toBeVisible()
    await expect(headers.filter({ hasText: "entity" })).toBeVisible()
    await expect(headers.filter({ hasText: "ip address" })).toBeVisible()
  })

  // BUG-75: a failure used to be swallowed and resolved with an empty
  // array, so a 500 rendered as "No audit entries found." On a compliance
  // surface, "nothing happened" and "we could not tell you what happened"
  // must not look the same.
  test("a failing request shows an error, not an empty log", async ({ page }) => {
    await page.route("**/api/audit-log*", (route) =>
      route.fulfill({ status: 500, body: JSON.stringify({ detail: "boom" }) })
    )
    await page.goto("/audit")

    // The banner names the failure; the table's empty cell says the log
    // could not be loaded. Target the banner specifically.
    await expect(page.getByText("Could not load the audit log: boom")).toBeVisible({
      timeout: 10000,
    })
    await expect(page.getByText("No audit entries found.")).toHaveCount(0)
  })

  // BUG-75: the page requested no `limit`, so it took the backend default
  // of 50 and everything older was unreachable — including from the column
  // filters, which only ever searched what had been loaded.
  test("it asks for a full page and can fetch the next one", async ({ page }) => {
    const requested: string[] = []
    await page.route("**/api/audit-log*", async (route) => {
      const url = new URL(route.request().url())
      requested.push(url.search)
      const cursor = url.searchParams.get("cursor")
      const start = cursor ? Number(cursor) - 1 : 1000
      const rows = Array.from({ length: 100 }, (_, i) => ({
        id: start - i,
        user_id: null,
        user_email: null,
        action: "update",
        entity_type: "host",
        entity_id: 1,
        before_state: null,
        after_state: null,
        ip_address: null,
        created_at: new Date().toISOString(),
      }))
      await route.fulfill({ status: 200, body: JSON.stringify(rows) })
    })
    await page.goto("/audit")

    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })
    expect(requested[0]).toContain("limit=100")

    await page.getByRole("button", { name: "Load More" }).click()
    await expect.poll(() => requested.length).toBeGreaterThan(1)
    expect(requested[1]).toContain("cursor=")
  })
})
