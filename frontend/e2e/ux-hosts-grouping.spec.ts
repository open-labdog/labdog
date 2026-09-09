import { test, expect } from "./fixtures"

test.describe("Hosts page group features", () => {
  test("groups column header exists in hosts table", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(
      page.getByRole("columnheader", { name: "Groups" })
    ).toBeVisible()
  })

  test("group filter dropdown exists with 'All Groups' default", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    // The hosts page uses a custom button-based dropdown (not a native <select>)
    // The default state shows "All Groups" as the button label
    const filterButton = page.getByRole("button", { name: /All Groups/i })
    await expect(filterButton).toBeVisible()
  })

  // Two tests for a "Group View" / "Flat View" toggle were removed here.
  // The toggle no longer exists — the hosts page filters by group through a
  // dropdown instead — so they had been `test.skip`ped indefinitely, which
  // reads as coverage without being any.
})
