import { test, expect } from "./fixtures"

test.describe("Search input UX", () => {
  test("search input exists on groups page", async ({ page }) => {
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
    await expect(page.getByPlaceholder("Search groups...")).toBeVisible()
  })

  // Two tests were removed here: one for a standalone hostname/IP search box
  // on the hosts page (replaced by column-level DataTable filters) and one for
  // a "Showing X of Y" count the groups page never renders. Both had been
  // `test.skip`ped indefinitely against UI that no longer exists.
})
