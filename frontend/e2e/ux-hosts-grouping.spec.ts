import { test, expect } from "./fixtures"

test.describe("Hosts page group features", () => {
  test("groups column header exists in hosts table", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(
      page.getByRole("columnheader", { name: "Groups" })
    ).toBeVisible()
  })

  test("group filter reads 'group' until one is picked", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    // Filters read as a sentence when set — "group · web" — and as their
    // bare label when not. Opening one lists "all group" first.
    // The filter reads "group ▾"; the column header is "groups", so anchor
    // on the caret to tell them apart.
    const filterButton = page.getByRole("button", { name: /^group\s*▾$/i })
    await expect(filterButton).toBeVisible()
    await filterButton.click()
    await expect(page.getByRole("button", { name: /all group/i })).toBeVisible()
  })

  // Two tests for a "Group View" / "Flat View" toggle were removed here.
  // The toggle no longer exists — the hosts page filters by group through a
  // dropdown instead — so they had been `test.skip`ped indefinitely, which
  // reads as coverage without being any.
})
