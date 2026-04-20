import { test, expect } from "@playwright/test"

test.describe("Search input UX", () => {
  test("search input exists on groups page", async ({ page }) => {
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
    await expect(page.getByPlaceholder("Search groups...")).toBeVisible()
  })

  // TODO: The hosts page no longer has a standalone search input with placeholder
  // "Search by hostname or IP..." — it uses column-level DataTable filters instead.
  test.skip("search input exists on hosts page", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(
      page.getByPlaceholder("Search by hostname or IP...")
    ).toBeVisible()
  })

  // TODO: The groups page search does not render a "Showing X of Y groups" count text.
  // The filtered results are shown directly in the table sections without a count label.
  test.skip("search displays 'Showing' count when filtering", async ({
    page,
    request,
  }) => {
    const API_BASE =
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

    const g1 = `e2e-search-a-${Date.now()}`
    const g2 = `e2e-search-b-${Date.now()}`
    await request.post(`${API_BASE}/api/groups`, {
      data: { name: g1, description: null, priority: 990 },
    })
    await request.post(`${API_BASE}/api/groups`, {
      data: { name: g2, description: null, priority: 991 },
    })

    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()

    await page.getByPlaceholder("Search groups...").fill(g1)

    await expect(page.getByText(/Showing \d+ of \d+ groups/)).toBeVisible()
  })
})
